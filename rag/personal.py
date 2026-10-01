"""Materiały osobiste: trwały zapis SQL, niezależny i ponawialny indeks.

Mutacje są granicami transakcji (durable atomic); nie należy opakowywać ich
w dodatkowe atomic/ATOMIC_REQUESTS. Indeksowanie startuje dopiero po commit.
"""

import hashlib
import logging
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone

from library.ingest import build_chunks
from library.models import (
    Author,
    Chunk,
    Consent,
    DocType,
    Document,
    IndexStatus,
    Register,
)

logger = logging.getLogger(__name__)
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
        labels = {"first_name": "Imię", "last_name": "Nazwisko", "email": "E-mail"}


class UploadForm(forms.Form):
    file = forms.FileField(label="Plik (PDF, TXT, MD)")
    title = forms.CharField(
        label="Tytuł", max_length=Document._meta.get_field("title").max_length
    )
    author = forms.CharField(
        label="Autor",
        max_length=Author._meta.get_field("name").max_length,
        required=False,
        help_text="np. autor materiału; puste = Ty",
    )
    year = forms.IntegerField(
        label="Rok", required=False, min_value=1400, max_value=2100
    )
    doc_type = forms.ChoiceField(
        label="Typ", choices=DocType.choices, initial=DocType.SCRIPT
    )
    register = forms.ChoiceField(
        label="Rejestr", choices=Register.choices, initial=Register.MIXED
    )
    note = forms.CharField(
        label="Uwaga / źródło",
        max_length=Document._meta.get_field("note").max_length,
        required=False,
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if Path(f.name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValidationError("Dozwolone: PDF, TXT, MD.")
        if f.size > settings.PERSONAL_MAX_MB * 1024 * 1024:
            raise ValidationError(f"Plik większy niż {settings.PERSONAL_MAX_MB} MB.")
        return f


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "plik"


def _author_name(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _get_author(name: str) -> Author:
    name = _author_name(name)
    if not name or len(name) > Author._meta.get_field("name").max_length:
        raise ValidationError("Nazwa autora musi mieć od 1 do 128 znaków.")
    key = name.casefold()
    # Starszy korpus ma slugi bez hasha i nazwy w różnych normalizacjach Unicode.
    for author in Author.objects.only("id", "name", "slug").order_by("pk").iterator():
        if _author_name(author.name).casefold() == key:
            return author
    digest = hashlib.sha256(key.encode()).hexdigest()
    base = f"{_slug(name)}-{digest}"
    for attempt in range(100):
        slug = base if not attempt else f"{base[:120]}-{attempt}"
        try:
            # Savepoint: kolizja unique nie może zatruć transakcji uploadu.
            with transaction.atomic():
                author, _ = Author.objects.get_or_create(
                    slug=slug, defaults={"name": name}
                )
        except IntegrityError:
            author = Author.objects.filter(name=name).first()
            if author is None:
                raise
        if _author_name(author.name).casefold() == key:
            return author
    raise ValidationError("Nie udało się przydzielić unikalnego identyfikatora autora.")


@contextmanager
def _owner_transaction(user: User):
    connection.ensure_connection()
    sqlite = connection.vendor == "sqlite"
    previous_mode = connection.transaction_mode if sqlite else None
    if sqlite:
        connection.transaction_mode = "IMMEDIATE"
    try:
        with transaction.atomic(durable=True):
            if sqlite:
                # Także przy testowym zewnętrznym atomic: blokada zapisu przed odczytami.
                found = User.objects.filter(pk=user.pk).update(
                    last_login=F("last_login")
                )
                if not found:
                    raise User.DoesNotExist
            else:
                User.objects.select_for_update().get(pk=user.pk)
            yield
    finally:
        if sqlite:
            connection.transaction_mode = previous_mode


def _schedule_index(doc: Document) -> None:
    doc.index_status = IndexStatus.PENDING
    doc.index_error = ""
    doc.indexed_at = None
    doc.index_revision += 1
    doc.save(
        update_fields=["index_status", "index_error", "indexed_at", "index_revision"]
    )
    transaction.on_commit(lambda: _reindex(doc))


def ingest_personal(user: User, form: UploadForm) -> Document:
    """Plik i cały graf SQL albo nic; awaria indeksu nie jest awarią uploadu."""
    if not form.is_valid():
        raise ValidationError("Nieprawidłowy formularz uploadu.")
    f = form.cleaned_data["file"]
    f.seek(0)
    data = f.read()
    if len(data) > settings.PERSONAL_MAX_MB * 1024 * 1024:
        raise ValidationError(f"Plik większy niż {settings.PERSONAL_MAX_MB} MB.")
    content_hash = hashlib.md5(data).hexdigest()  # noqa: S324 — zgodność z korpusem
    dest_dir = Path(settings.DATA_DIR) / "library" / "users" / str(user.pk)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = None
    committed = False

    def mark_committed():
        nonlocal committed
        committed = True

    try:
        # Wyłączny, losowy plik próby: przegrany upload nie usuwa pliku zwycięzcy.
        with tempfile.NamedTemporaryFile(
            dir=dest_dir,
            prefix=f"{content_hash}-{_slug(Path(f.name).stem)}-",
            suffix=Path(f.name).suffix.lower(),
            delete=False,
        ) as target:
            dest = Path(target.name)
            target.write(data)
        if len(str(dest)) > Document._meta.get_field("source_path").max_length:
            raise ValidationError("Ścieżka pliku jest zbyt długa.")
        drafts = build_chunks(dest)
        if not drafts:
            raise ValidationError(
                "Nie udało się wydobyć tekstu z pliku (skan bez warstwy tekstowej?)."
            )
        with _owner_transaction(user):
            if user.documents.filter(content_hash=content_hash).exists():
                raise ValidationError(
                    "Ten plik jest już w Twoich materiałach.", code="duplicate_file"
                )
            if user.documents.count() >= settings.PERSONAL_MAX_DOCS:
                raise ValidationError(
                    f"Limit {settings.PERSONAL_MAX_DOCS} materiałów na konto."
                )
            author = _get_author(
                form.cleaned_data["author"]
                or user.get_full_name()
                or user.get_username()
            )
            doc = Document.objects.create(
                title=form.cleaned_data["title"],
                doc_type=form.cleaned_data["doc_type"],
                register=form.cleaned_data["register"],
                year=form.cleaned_data["year"],
                note=form.cleaned_data["note"],
                license="użytek własny",
                access="personal",
                owner=user,
                source_path=str(dest),
                content_hash=content_hash,
                chunk_count=len(drafts),
                language="pl",
            )
            doc.authors.add(author)
            Chunk.objects.bulk_create(
                [
                    Chunk(
                        document=doc,
                        order=d.order,
                        section=d.section[:300],
                        text=d.text,
                        page_start=d.page_start,
                        page_end=d.page_end,
                        sigla=d.sigla,
                    )
                    for d in drafts
                ]
            )
            transaction.on_commit(mark_committed)
            _schedule_index(doc)
    except BaseException:
        if dest is not None and not committed:
            dest.unlink(missing_ok=True)
        raise
    return doc


def delete_personal(user: User, doc_id: int) -> None:
    with _owner_transaction(user):
        doc = user.documents.get(id=doc_id)
        source = Path(doc.source_path) if doc.source_path else None
        doc.delete()

        def cleanup():
            if source is not None:
                try:
                    source.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Nie udało się usunąć pliku materiału osobistego")
            if settings.SEARCH_BACKEND == "opensearch":
                try:
                    from library.search import delete_document_from_index

                    # delete() zeruje pk obiektu, a indeks usuwa po document_id.
                    doc.pk = doc_id
                    delete_document_from_index(doc)
                except Exception:
                    logger.exception(
                        "Nie udało się usunąć dokumentu %s z indeksu", doc_id
                    )

        transaction.on_commit(cleanup)


CONSENT_STATEMENT = (
    "Oświadczam, że jestem autorem tego materiału lub posiadam prawo do dysponowania nim, "
    "i wyrażam zgodę na włączenie go do wspólnego korpusu Scriptura: indeksowanie, wyszukiwanie "
    "oraz cytowanie fragmentów w odpowiedziach systemu (także dla użytkowników niezalogowanych "
    "w trybie popularnonaukowym), z podaniem autorstwa. Zgodę mogę wycofać w każdej chwili; "
    "po wycofaniu materiał wraca do moich materiałów osobistych."
)


class ShareForm(forms.Form):
    register = forms.ChoiceField(
        label="Rejestr we wspólnym korpusie",
        choices=Register.choices,
        initial=Register.SCIENTIFIC,
    )
    license = forms.CharField(
        label="Licencja / zakres",
        max_length=min(
            Document._meta.get_field("license").max_length,
            Consent._meta.get_field("license").max_length,
        ),
        initial="za zgodą autora (Consent)",
    )
    accept = forms.BooleanField(label="Akceptuję oświadczenie", required=True)


def share_personal(user: User, doc_id: int, form: ShareForm) -> Document:
    """Atomowe personal -> licensed; powtórzenie nie tworzy kolejnej zgody."""
    if not form.is_valid():
        raise ValidationError(
            "Udostępnienie wymaga poprawnego formularza i akceptacji zgody."
        )
    with _owner_transaction(user):
        doc = user.documents.get(id=doc_id, access__in=["personal", "licensed"])
        active = doc.consents.filter(revoked_at__isnull=True)
        if (
            doc.access == "personal"
            or not active.exists()
            or doc.license != form.cleaned_data["license"]
            or doc.register != form.cleaned_data["register"]
        ):
            # Naprawia również historyczne niespójne zgody, nie usuwa śladu dowodowego.
            active.update(revoked_at=timezone.now())
            Consent.objects.create(
                document=doc,
                user=user,
                statement=CONSENT_STATEMENT,
                license=form.cleaned_data["license"],
                register=form.cleaned_data["register"],
            )
            doc.access = "licensed"
            doc.register = form.cleaned_data["register"]
            doc.license = form.cleaned_data["license"]
            doc.save(update_fields=["access", "register", "license"])
        _schedule_index(doc)
    return doc


def unshare_personal(user: User, doc_id: int) -> Document:
    """Commit wycofania zgody nie zależy od indeksu; operację można powtarzać."""
    with _owner_transaction(user):
        doc = user.documents.get(id=doc_id, access__in=["personal", "licensed"])
        doc.consents.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        doc.access = "personal"
        doc.save(update_fields=["access"])
        _schedule_index(doc)
    return doc


def retry_personal_index(user: User, doc_id: int) -> Document:
    """Owner-only retry według SQL; sieć nigdy nie blokuje wycofania zgody."""
    external_index = settings.SEARCH_BACKEND == "opensearch"
    with _owner_transaction(user):
        doc = user.documents.get(id=doc_id)
        doc.index_revision += 1
        doc.index_status = (
            IndexStatus.PENDING if external_index else IndexStatus.NOT_REQUIRED
        )
        doc.index_error = ""
        doc.indexed_at = None
        doc.save(
            update_fields=[
                "index_status",
                "index_error",
                "indexed_at",
                "index_revision",
            ]
        )
    if not external_index:
        return doc

    revision = doc.index_revision
    try:
        from library.search import index_document

        indexed = index_document(doc)
        if indexed != doc.chunk_count:
            raise RuntimeError("Indeks nie potwierdził zapisu wszystkich fragmentów.")
    except Exception as exc:
        logger.exception("Błąd indeksowania dokumentu %s", doc.pk)
        status = IndexStatus.FAILED
        # Komunikaty usług zewnętrznych mogą zawierać sekrety lub treść dokumentu.
        error = f"Błąd indeksowania ({type(exc).__name__}). Można ponowić."
        indexed_at = None
    else:
        status, error, indexed_at = IndexStatus.INDEXED, "", timezone.now()

    documents = user.documents.filter(pk=doc.pk)
    updated = documents.filter(index_revision=revision).update(
        index_status=status, index_error=error, indexed_at=indexed_at
    )
    if not updated:
        # Stara próba mogła nadpisać indeks po nowszej. Nie potwierdzamy zgodności:
        # SQL ACL pozostaje autorytatywny, a najnowszy stan wymaga kolejnego retry.
        documents.filter(index_revision__gt=revision).update(
            index_status=IndexStatus.PENDING, index_error="", indexed_at=None
        )
    return user.documents.get(pk=doc.pk)


def _reindex(doc: Document) -> None:
    # Sieć i zapis statusu są poza pierwotną transakcją. Nawet awaria zapisu statusu
    # nie może zamienić poprawnie zatwierdzonego uploadu/wycofania w pozorną porażkę.
    try:
        current = retry_personal_index(doc.owner, doc.pk)
    except Document.DoesNotExist:
        return  # dokument usunięty pomiędzy commit a callbackiem
    except Exception:
        logger.exception(
            "Nie udało się zaktualizować statusu indeksu dokumentu %s", doc.pk
        )
        return  # w SQL zostaje pending — retry nadal możliwy
    doc.index_status = current.index_status
    doc.index_error = current.index_error
    doc.indexed_at = current.indexed_at
    doc.index_revision = current.index_revision
