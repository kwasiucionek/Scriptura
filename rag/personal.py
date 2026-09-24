"""Materiały osobiste użytkownika: wgrywanie i ingestia (access=personal, owner=user).

Plik trafia do DATA_DIR/library/users/<user_id>/, chunkowanie i sigla tym samym pipeline'em
co korpus, indeks od razu (bez przekładu — materiały osobiste nie idą przez translate_chunks).
Limity: PERSONAL_MAX_MB na plik, PERSONAL_MAX_DOCS na konto.
"""

import hashlib
import re
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

from library.ingest import build_chunks
from library.models import Author, Chunk, Consent, DocType, Document, Register

ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
        labels = {"first_name": "Imię", "last_name": "Nazwisko", "email": "E-mail"}


class UploadForm(forms.Form):
    file = forms.FileField(label="Plik (PDF, TXT, MD)")
    title = forms.CharField(label="Tytuł", max_length=500)
    author = forms.CharField(
        label="Autor",
        max_length=200,
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
    note = forms.CharField(label="Uwaga / źródło", max_length=300, required=False)

    def clean_file(self):
        f = self.cleaned_data["file"]
        if Path(f.name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValidationError("Dozwolone: PDF, TXT, MD.")
        if f.size > settings.PERSONAL_MAX_MB * 1024 * 1024:
            raise ValidationError(f"Plik większy niż {settings.PERSONAL_MAX_MB} MB.")
        return f


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "plik"


def ingest_personal(user: User, form: UploadForm) -> Document:
    """Zapisuje plik, buduje chunki i indeksuje jako materiał osobisty użytkownika."""
    if user.documents.count() >= settings.PERSONAL_MAX_DOCS:
        raise ValidationError(
            f"Limit {settings.PERSONAL_MAX_DOCS} materiałów na konto."
        )
    f = form.cleaned_data["file"]
    dest_dir = settings.DATA_DIR / "library" / "users" / str(user.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = f.read()
    content_hash = hashlib.md5(data).hexdigest()  # noqa: S324
    if user.documents.filter(content_hash=content_hash).exists():
        raise ValidationError("Ten plik jest już w Twoich materiałach.")
    dest = (
        dest_dir
        / f"{content_hash[:8]}-{_slug(Path(f.name).stem)}{Path(f.name).suffix.lower()}"
    )
    dest.write_bytes(data)

    drafts = build_chunks(dest)
    if not drafts:
        dest.unlink(missing_ok=True)
        raise ValidationError(
            "Nie udało się wydobyć tekstu z pliku (skan bez warstwy tekstowej?)."
        )
    author_name = form.cleaned_data["author"].strip() or (
        user.get_full_name() or user.get_username()
    )
    author, _ = Author.objects.get_or_create(
        name=author_name, defaults={"slug": _slug(author_name)}
    )
    doc = Document.objects.create(
        title=form.cleaned_data["title"].strip(),
        doc_type=form.cleaned_data["doc_type"],
        register=form.cleaned_data["register"],
        year=form.cleaned_data["year"],
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
            Chunk(document=doc, order=d.order, section=d.section[:300], text=d.text,
                  page_start=d.page_start, page_end=d.page_end, sigla=d.sigla)
            for d in drafts
        ]
    )  # fmt: skip
    if settings.SEARCH_BACKEND == "opensearch":
        from library.search import index_document

        index_document(doc)
    return doc


def delete_personal(user: User, doc_id: int) -> None:
    doc = user.documents.get(id=doc_id)
    if settings.SEARCH_BACKEND == "opensearch":
        from library.search import delete_document_from_index

        delete_document_from_index(doc)
    Path(doc.source_path).unlink(missing_ok=True)
    doc.delete()


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
        label="Licencja / zakres", max_length=200, initial="za zgodą autora (Consent)"
    )
    accept = forms.BooleanField(label="Akceptuję oświadczenie", required=True)


def share_personal(user: User, doc_id: int, form: ShareForm) -> Document:
    """personal -> licensed ze zgodą; dokument zostaje przy właścicielu (może wycofać)."""
    doc = user.documents.get(id=doc_id, access="personal")
    Consent.objects.create(
        document=doc, user=user, statement=CONSENT_STATEMENT,
        license=form.cleaned_data["license"], register=form.cleaned_data["register"],
    )  # fmt: skip
    doc.access = "licensed"
    doc.register = form.cleaned_data["register"]
    doc.license = form.cleaned_data["license"]
    doc.save(update_fields=["access", "register", "license"])
    _reindex(doc)
    return doc


def unshare_personal(user: User, doc_id: int) -> Document:
    """Wycofanie zgody: licensed -> personal; wpis Consent zostaje z datą wycofania."""
    doc = user.documents.get(id=doc_id, access="licensed")
    doc.consents.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    doc.access = "personal"
    doc.save(update_fields=["access"])
    _reindex(doc)
    return doc


def _reindex(doc: Document) -> None:
    if settings.SEARCH_BACKEND == "opensearch":
        from library.search import index_document

        index_document(
            doc
        )  # nadpisuje dokumenty w indeksie (te same _id) z nowym access
