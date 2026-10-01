import hashlib
import importlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import QuerySet
from django.test import Client

from library.models import Author, Chunk, Consent, Document, IndexStatus
from rag import personal
from rag.personal import (
    ShareForm,
    UploadForm,
    ingest_personal,
    retry_personal_index,
    share_personal,
    unshare_personal,
)

pytestmark = pytest.mark.django_db

MD = b"# Jerycho\n\nMury Jerycha zostaly zniszczone ok. 1550 r. p.n.e., 300 lat przed czasami Jozuego (Joz 6). Archeologia pokazuje warstwe zniszczenia, ale nie daje podpisu sprawcy.\n"


def _events(resp):
    body = b"".join(resp.streaming_content).decode()
    return [
        (e, json.loads(d)) for e, d in re.findall(r"event: (\w+)\ndata: (.*)\n", body)
    ]


def _ask(client, q, **kw):
    return _events(
        client.post(
            "/ask/stream",
            data=json.dumps({"question": q, **kw}),
            content_type="application/json",
        )
    )


def test_upload_personal_and_retrieve_only_for_owner(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    Document.objects.create(title="Otwarty o Jerychu", access="open")
    Chunk.objects.create(
        document=Document.objects.get(title="Otwarty o Jerychu"),
        order=0,
        text="Mury Jerycha zniszczone — ujecie otwarte (Joz 6).",
        sigla=[],
    )
    owner = User.objects.create_user("ja", password="x")
    other = User.objects.create_user("obcy", password="x")

    c = Client(HTTP_HOST="localhost")
    c.force_login(owner)
    r = c.post(
        "/account/",
        {
            "action": "upload",
            "title": "Webinar: Jerycho",
            "author": "",
            "doc_type": "script",
            "register": "popular",
            "file": SimpleUploadedFile("webinar.md", MD, content_type="text/markdown"),
        },
    )
    assert r.status_code == 302
    doc = owner.documents.get()
    assert (
        doc.access == "personal"
        and doc.chunk_count >= 1
        and doc.authors.first().name == "ja"
    )
    assert (tmp_path / "library" / "users" / str(owner.id)).exists()

    # dublet tego samego pliku odrzucony
    r = c.post(
        "/account/",
        {
            "action": "upload",
            "title": "Znowu",
            "doc_type": "script",
            "register": "popular",
            "file": SimpleUploadedFile("w2.md", MD),
        },
    )
    assert "już w Twoich materiałach" in r.content.decode()

    # właściciel widzi materiał w RAG (razem z open), a z personal_only tylko swój
    ev = _ask(c, "mury Jerycha zniszczone", mode="popular")
    titles = {s["title"] for s in ev[0][1]["chunks"]}
    assert "Webinar: Jerycho" in titles and "Otwarty o Jerychu" in titles
    assert any(
        s["personal"] for s in ev[0][1]["chunks"] if s["title"] == "Webinar: Jerycho"
    )
    ev = _ask(c, "mury Jerycha zniszczone", mode="popular", personal_only=True)
    assert {s["title"] for s in ev[0][1]["chunks"]} == {"Webinar: Jerycho"}

    # inny użytkownik i anonim nie widzą
    o = Client(HTTP_HOST="localhost")
    o.force_login(other)
    assert "Webinar: Jerycho" not in {
        s["title"] for s in _ask(o, "mury Jerycha zniszczone")[0][1]["chunks"]
    }
    anon = Client(HTTP_HOST="localhost")
    assert "Webinar: Jerycho" not in {
        s["title"] for s in _ask(anon, "mury Jerycha zniszczone")[0][1]["chunks"]
    }
    assert o.get("/account/").content.decode().count("Webinar") == 0

    # usunięcie
    r = c.post("/account/", {"action": "delete", "doc_id": doc.id})
    assert r.status_code == 302 and owner.documents.count() == 0


def test_profile_and_password_change():
    u = User.objects.create_user("anna", password="stare-haslo-123")
    c = Client(HTTP_HOST="localhost")
    c.force_login(u)
    c.post(
        "/account/",
        {
            "action": "profile",
            "first_name": "Anna",
            "last_name": "K",
            "email": "a@x.pl",
        },
    )
    u.refresh_from_db()
    assert u.first_name == "Anna" and u.email == "a@x.pl"
    r = c.post(
        "/account/",
        {
            "action": "password",
            "old_password": "stare-haslo-123",
            "new_password1": "Nowe-haslo-987",
            "new_password2": "Nowe-haslo-987",
        },
    )
    assert r.status_code == 302
    u.refresh_from_db()
    assert u.check_password("Nowe-haslo-987")
    assert (
        Client(HTTP_HOST="localhost").get("/account/").status_code == 302
    )  # anonim -> login


def test_share_and_unshare_personal(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    owner = User.objects.create_user("autor", password="x")
    reader = User.objects.create_user("czytelnik", password="x")
    from django.contrib.auth.models import Group

    from rag.access import GROUP_LICENSED

    reader.groups.add(Group.objects.create(name=GROUP_LICENSED))
    c = Client(HTTP_HOST="localhost")
    c.force_login(owner)
    c.post(
        "/account/",
        {
            "action": "upload",
            "title": "Mój webinar",
            "doc_type": "script",
            "register": "popular",
            "file": SimpleUploadedFile("w.md", MD),
        },
    )
    doc = owner.documents.get()
    r = Client(HTTP_HOST="localhost")
    r.force_login(reader)
    assert "Mój webinar" not in {
        s["title"] for s in _ask(r, "mury Jerycha zniszczone")[0][1]["chunks"]
    }

    # bez akceptacji oświadczenia — odmowa
    c.post(
        "/account/",
        {
            "action": "share",
            "doc_id": doc.id,
            "register": "scientific",
            "license": "za zgodą autora",
        },
    )
    doc.refresh_from_db()
    assert doc.access == "personal"
    # z akceptacją — licensed + Consent
    c.post(
        "/account/",
        {
            "action": "share",
            "doc_id": doc.id,
            "register": "scientific",
            "license": "za zgodą autora",
            "accept": "on",
        },
    )
    doc.refresh_from_db()
    assert (
        doc.access == "licensed" and doc.register == "scientific" and doc.owner == owner
    )
    assert doc.consents.filter(revoked_at__isnull=True).count() == 1
    assert "Mój webinar" in {
        s["title"]
        for s in _ask(r, "mury Jerycha zniszczone", mode="scientific")[0][1]["chunks"]
    }
    # anonim (tylko open) nadal nie widzi
    assert "Mój webinar" not in {
        s["title"]
        for s in _ask(Client(HTTP_HOST="localhost"), "mury Jerycha zniszczone")[0][1][
            "chunks"
        ]
    }
    # wycofanie
    c.post("/account/", {"action": "unshare", "doc_id": doc.id})
    doc.refresh_from_db()
    assert doc.access == "personal" and doc.consents.get().revoked_at is not None
    assert "Mój webinar" not in {
        s["title"] for s in _ask(r, "mury Jerycha zniszczone")[0][1]["chunks"]
    }


def _upload_form(data=MD, **fields):
    values = {
        "title": "Materiał osobisty",
        "author": "Autor",
        "doc_type": "script",
        "register": "popular",
        "note": "Źródło: własne notatki",
        **fields,
    }
    form = UploadForm(values, {"file": SimpleUploadedFile("material.md", data)})
    assert form.is_valid(), form.errors
    return form


def _share_form(**fields):
    form = ShareForm(
        {
            "register": "scientific",
            "license": "za zgodą autora",
            "accept": True,
            **fields,
        }
    )
    assert form.is_valid(), form.errors
    return form


@pytest.fixture
def personal_owner(settings, tmp_path):
    settings.DATA_DIR = tmp_path / "uploads"
    settings.PERSONAL_MAX_DOCS = 5
    return User.objects.create_user("właściciel")


@pytest.fixture
def isolated_sqlite(monkeypatch, tmp_path):
    original = connections["default"]
    if original.vendor != "sqlite":
        pytest.skip("Test izolowanego SQLite; PostgreSQL nie jest wymagany offline")
    isolated = original.copy()
    isolated.settings_dict["NAME"] = str(tmp_path / "isolated.sqlite3")
    # Serwis ma wymusić IMMEDIATE, nawet gdy domyślne ustawienie jest DEFERRED.
    isolated.settings_dict["OPTIONS"]["transaction_mode"] = "DEFERRED"
    with monkeypatch.context() as patch:
        patch.setattr(connections._connections, "default", isolated)
        try:
            yield isolated
        finally:
            isolated.close()


class TestPersonalPersistence:
    """Testy serwisu/SQL offline: bez HTTP, retrievalu i połączeń z OpenSearch."""

    pytestmark = pytest.mark.django_db(transaction=True)

    def test_upload_saves_note_and_complete_graph(self, personal_owner):
        doc = ingest_personal(personal_owner, _upload_form())
        assert doc.note == "Źródło: własne notatki"
        assert doc.chunk_count == doc.chunks.count() > 0
        assert doc.authors.get().name == "Autor"
        assert doc.index_status == IndexStatus.NOT_REQUIRED
        assert doc.index_error == "" and doc.indexed_at is None
        assert Path(doc.source_path).read_bytes() == MD

    @pytest.mark.parametrize("result", [[], RuntimeError("parser failed")])
    def test_parse_failure_leaves_no_file_or_sql(
        self, personal_owner, settings, monkeypatch, result
    ):
        parser = (
            Mock(side_effect=result)
            if isinstance(result, Exception)
            else Mock(return_value=result)
        )
        monkeypatch.setattr(personal, "build_chunks", parser)
        with pytest.raises((ValidationError, RuntimeError)):
            ingest_personal(personal_owner, _upload_form())
        assert not list(settings.DATA_DIR.rglob("*.md"))
        assert not Document.objects.exists()
        assert not Chunk.objects.exists()
        assert not Author.objects.exists()

    def test_partial_file_write_is_cleaned(self, personal_owner, settings, monkeypatch):
        real = personal.tempfile.NamedTemporaryFile

        def failing_file(*args, **kwargs):
            target = real(*args, **kwargs)
            write = target.write

            def partial_write(data):
                write(data[:4])
                raise OSError("disk full")

            target.write = partial_write
            return target

        monkeypatch.setattr(personal.tempfile, "NamedTemporaryFile", failing_file)
        with pytest.raises(OSError, match="disk full"):
            ingest_personal(personal_owner, _upload_form())
        assert not list(settings.DATA_DIR.rglob("*.md"))
        assert not Document.objects.exists()

    @pytest.mark.parametrize(
        "stage", ["author", "document", "authors", "chunks", "status"]
    )
    def test_sql_failure_rolls_back_entire_graph_and_file(
        self, personal_owner, settings, monkeypatch, stage
    ):
        if stage == "author":
            original = personal._get_author

            def fail_author(name):
                original(name)
                raise RuntimeError("SQL failed")

            monkeypatch.setattr(personal, "_get_author", fail_author)
        elif stage == "authors":
            monkeypatch.setattr(
                Document.authors.related_manager_cls,
                "add",
                Mock(side_effect=RuntimeError("SQL failed")),
            )
        elif stage == "chunks":
            original = Chunk.objects.bulk_create

            def fail_chunks(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("SQL failed")

            monkeypatch.setattr(Chunk.objects, "bulk_create", fail_chunks)
        else:
            original = Document.save

            def fail_save(doc, *args, **kwargs):
                original(doc, *args, **kwargs)
                if stage == "document" or "index_status" in kwargs.get(
                    "update_fields", []
                ):
                    raise RuntimeError("SQL failed")

            monkeypatch.setattr(Document, "save", fail_save)
        with pytest.raises(RuntimeError, match="SQL failed"):
            ingest_personal(personal_owner, _upload_form())
        assert not Document.objects.exists()
        assert not Author.objects.exists()
        assert not Chunk.objects.exists()
        assert not list(settings.DATA_DIR.rglob("*.md"))

    def test_duplicate_and_limit_do_not_touch_original_file(
        self, personal_owner, settings
    ):
        settings.PERSONAL_MAX_DOCS = 1
        doc = ingest_personal(personal_owner, _upload_form())
        with pytest.raises(ValidationError, match="już w Twoich materiałach"):
            ingest_personal(personal_owner, _upload_form())
        with pytest.raises(ValidationError, match="Limit"):
            ingest_personal(personal_owner, _upload_form(MD + b" inny plik"))
        assert list(settings.DATA_DIR.rglob("*.md")) == [Path(doc.source_path)]
        assert Path(doc.source_path).read_bytes() == MD
        assert personal_owner.documents.count() == 1

    def test_database_constraint_only_applies_to_nonempty_owned_hash(
        self, personal_owner
    ):
        other = User.objects.create_user("inny")
        digest = hashlib.md5(MD).hexdigest()
        Document.objects.create(
            title="pierwszy", owner=personal_owner, content_hash=digest
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            Document.objects.create(
                title="dublet", owner=personal_owner, content_hash=digest
            )
        Document.objects.create(title="inne konto", owner=other, content_hash=digest)
        for _ in range(2):
            Document.objects.create(title="korpus", content_hash=digest)
            Document.objects.create(title="bez hasha", owner=personal_owner)

    def test_index_failure_is_saved_upload_and_retry_uses_current_sql(
        self, personal_owner, settings, monkeypatch
    ):
        settings.SEARCH_BACKEND = "opensearch"
        index = Mock(
            side_effect=RuntimeError("https://secret:password@example.invalid")
        )
        monkeypatch.setattr("library.search.index_document", index)
        original = personal._reindex
        before_index = []

        def after_commit(doc):
            before_index.append(connection.in_atomic_block)
            assert doc.chunks.count() == doc.chunk_count
            original(doc)

        monkeypatch.setattr(personal, "_reindex", after_commit)
        doc = ingest_personal(personal_owner, _upload_form())
        doc.refresh_from_db()
        assert before_index == [False]
        assert doc.index_status == IndexStatus.FAILED
        assert "password" not in doc.index_error
        assert doc.indexed_at is None
        assert Path(doc.source_path).exists()
        assert doc.authors.exists() and doc.chunks.exists()
        index.side_effect = lambda current: current.chunks.count()
        retried = retry_personal_index(personal_owner, doc.pk)
        assert retried.index_status == IndexStatus.INDEXED
        assert retried.index_error == "" and retried.indexed_at is not None
        assert index.call_args.args[0].access == "personal"
        assert (
            retry_personal_index(personal_owner, doc.pk).index_status
            == IndexStatus.INDEXED
        )
        assert Document.objects.count() == 1

    def test_incomplete_index_result_is_failure(
        self, personal_owner, settings, monkeypatch
    ):
        settings.SEARCH_BACKEND = "opensearch"
        monkeypatch.setattr("library.search.index_document", Mock(return_value=0))
        doc = ingest_personal(personal_owner, _upload_form())
        assert doc.chunk_count > 0
        assert doc.index_status == IndexStatus.FAILED
        assert Path(doc.source_path).exists()

    def test_status_write_failure_does_not_undo_upload(
        self, personal_owner, settings, monkeypatch
    ):
        settings.SEARCH_BACKEND = "opensearch"
        monkeypatch.setattr(
            "library.search.index_document", Mock(side_effect=RuntimeError())
        )
        original = QuerySet.update

        def fail_failed_status(queryset, **kwargs):
            if kwargs.get("index_status") == IndexStatus.FAILED:
                raise IntegrityError("status write failed")
            return original(queryset, **kwargs)

        monkeypatch.setattr(QuerySet, "update", fail_failed_status)
        doc = ingest_personal(personal_owner, _upload_form())
        doc.refresh_from_db()
        assert doc.index_status == IndexStatus.PENDING
        assert Path(doc.source_path).exists() and doc.chunks.exists()

    def test_share_and_unshare_are_idempotent_despite_index_failure(
        self, personal_owner, settings, monkeypatch
    ):
        doc = ingest_personal(personal_owner, _upload_form())
        settings.SEARCH_BACKEND = "opensearch"
        index = Mock(side_effect=RuntimeError("index down"))
        monkeypatch.setattr("library.search.index_document", index)
        shared = share_personal(personal_owner, doc.pk, _share_form())
        assert shared.access == "licensed" and shared.index_status == IndexStatus.FAILED
        consent = doc.consents.get()
        share_personal(personal_owner, doc.pk, _share_form())
        assert doc.consents.count() == 1
        unshared = unshare_personal(personal_owner, doc.pk)
        assert (
            unshared.access == "personal"
            and unshared.index_status == IndexStatus.FAILED
        )
        consent.refresh_from_db()
        revoked = consent.revoked_at
        assert revoked is not None
        unshare_personal(personal_owner, doc.pk)
        consent.refresh_from_db()
        assert consent.revoked_at == revoked
        index.side_effect = lambda current: current.chunks.count()
        retry_personal_index(personal_owner, doc.pk)
        assert index.call_args.args[0].access == "personal"
        assert not doc.consents.filter(revoked_at__isnull=True).exists()
        shared_again = share_personal(personal_owner, doc.pk, _share_form())
        assert shared_again.access == "licensed"
        assert shared_again.index_status == IndexStatus.INDEXED
        assert doc.consents.count() == 2
        assert doc.consents.filter(revoked_at__isnull=True).count() == 1

    def test_changing_share_terms_preserves_consent_history(self, personal_owner):
        doc = ingest_personal(personal_owner, _upload_form())
        share_personal(personal_owner, doc.pk, _share_form())
        original = doc.consents.get()
        changed = share_personal(
            personal_owner, doc.pk, _share_form(license="CC BY 4.0", register="popular")
        )
        assert changed.license == "CC BY 4.0" and changed.register == "popular"
        original.refresh_from_db()
        assert original.revoked_at is not None
        assert original.license == "za zgodą autora"
        assert doc.consents.filter(revoked_at__isnull=True).get().license == "CC BY 4.0"
        share_personal(
            personal_owner, doc.pk, _share_form(license="CC BY 4.0", register="popular")
        )
        assert doc.consents.count() == 2

    @pytest.mark.parametrize("late_failure", [False, True])
    def test_revocation_can_commit_during_index_and_late_result_requires_retry(
        self, personal_owner, settings, monkeypatch, late_failure
    ):
        doc = ingest_personal(personal_owner, _upload_form())
        share_personal(personal_owner, doc.pk, _share_form())
        settings.SEARCH_BACKEND = "opensearch"
        accesses = []

        def overlapping_index(snapshot):
            assert not connection.in_atomic_block
            accesses.append(snapshot.access)
            if snapshot.access == "licensed":
                revoked = unshare_personal(personal_owner, snapshot.pk)
                assert revoked.access == "personal"
                assert revoked.index_status == IndexStatus.INDEXED
                if late_failure:
                    raise RuntimeError("late partial failure")
            return snapshot.chunk_count

        monkeypatch.setattr("library.search.index_document", overlapping_index)
        result = retry_personal_index(personal_owner, doc.pk)
        assert accesses == ["licensed", "personal"]
        assert (
            result.access == "personal" and result.index_status == IndexStatus.PENDING
        )
        assert result.indexed_at is None
        consent = doc.consents.get()
        assert consent.revoked_at is not None
        assert (
            retry_personal_index(personal_owner, doc.pk).index_status
            == IndexStatus.INDEXED
        )
        assert accesses[-1] == "personal" and doc.consents.count() == 1

    @pytest.mark.parametrize("operation", ["share", "unshare"])
    def test_access_and_consent_updates_rollback_together(
        self, personal_owner, monkeypatch, operation
    ):
        doc = ingest_personal(personal_owner, _upload_form())
        if operation == "unshare":
            share_personal(personal_owner, doc.pk, _share_form())
        original = Document.save

        def fail_access(doc, *args, **kwargs):
            original(doc, *args, **kwargs)
            if "access" in kwargs.get("update_fields", []):
                raise IntegrityError("access update failed")

        index = Mock()
        monkeypatch.setattr(personal, "_reindex", index)
        monkeypatch.setattr(Document, "save", fail_access)
        with pytest.raises(IntegrityError):
            if operation == "share":
                share_personal(personal_owner, doc.pk, _share_form())
            else:
                unshare_personal(personal_owner, doc.pk)
        doc.refresh_from_db()
        assert doc.access == ("personal" if operation == "share" else "licensed")
        assert doc.consents.count() == (0 if operation == "share" else 1)
        if operation == "unshare":
            assert doc.consents.get().revoked_at is None
        index.assert_not_called()

    def test_share_requires_valid_explicit_acceptance(self, personal_owner):
        doc = ingest_personal(personal_owner, _upload_form())
        form = ShareForm({"register": "scientific", "license": "licencja"})
        with pytest.raises(ValidationError):
            share_personal(personal_owner, doc.pk, form)
        doc.refresh_from_db()
        assert doc.access == "personal" and not doc.consents.exists()

    def test_owner_checks_for_mutations_and_retry(self, personal_owner, monkeypatch):
        doc = ingest_personal(personal_owner, _upload_form())
        stranger = User.objects.create_user("obcy")
        index = Mock()
        monkeypatch.setattr("library.search.index_document", index)
        for action in (
            lambda: share_personal(stranger, doc.pk, _share_form()),
            lambda: unshare_personal(stranger, doc.pk),
            lambda: retry_personal_index(stranger, doc.pk),
            lambda: personal.delete_personal(stranger, doc.pk),
        ):
            with pytest.raises(Document.DoesNotExist):
                action()
        index.assert_not_called()
        assert Path(doc.source_path).exists()

    def test_queued_reindex_reads_new_access_not_stale_document(
        self, personal_owner, settings, monkeypatch
    ):
        stale = ingest_personal(personal_owner, _upload_form())
        share_personal(personal_owner, stale.pk, _share_form())
        assert stale.access == "personal"
        settings.SEARCH_BACKEND = "opensearch"
        index = Mock(side_effect=lambda current: current.chunks.count())
        monkeypatch.setattr("library.search.index_document", index)
        personal._reindex(stale)
        assert index.call_args.args[0].access == "licensed"
        stale = personal_owner.documents.get(pk=stale.pk)
        unshare_personal(personal_owner, stale.pk)
        personal._reindex(stale)
        assert index.call_args.args[0].access == "personal"

    def test_delete_sql_and_file_survive_index_failure(
        self, personal_owner, settings, monkeypatch
    ):
        doc = ingest_personal(personal_owner, _upload_form())
        source = Path(doc.source_path)
        settings.SEARCH_BACKEND = "opensearch"
        remove = Mock(side_effect=RuntimeError("index down"))
        monkeypatch.setattr("library.search.delete_document_from_index", remove)
        personal.delete_personal(personal_owner, doc.pk)
        assert not personal_owner.documents.exists()
        assert not Chunk.objects.exists() and not source.exists()
        assert remove.call_args.args[0].pk == doc.pk

    @pytest.mark.parametrize(
        "names",
        [("Jan Nowak", "JAN NOWAK"), ("José", "Jose\u0301"), ("Straße", "STRASSE")],
    )
    def test_author_case_and_unicode_normalization_reuse(self, personal_owner, names):
        legacy = Author.objects.create(name=names[0], slug="historyczny-slug")
        first = ingest_personal(personal_owner, _upload_form(author=names[0]))
        second = ingest_personal(
            personal_owner, _upload_form(MD + b" drugi", author=names[1])
        )
        assert first.authors.get().pk == second.authors.get().pk == legacy.pk
        assert Author.objects.count() == 1

    def test_distinct_names_with_same_ascii_slug_do_not_collide(self, personal_owner):
        names = ["José", "Jose", "!!!", "???"]
        authors = [
            ingest_personal(
                personal_owner, _upload_form(MD + str(i).encode(), author=name)
            ).authors.get()
            for i, name in enumerate(names)
        ]
        assert len({author.slug for author in authors}) == len(names)
        assert all(len(author.slug) <= 128 for author in authors)

    def test_existing_hash_slug_collision_gets_another_slug(self, personal_owner):
        digest = hashlib.sha256(b"autor").hexdigest()
        collision = Author.objects.create(name="Inny autor", slug=f"autor-{digest}")
        doc = ingest_personal(personal_owner, _upload_form())
        assert doc.authors.get().pk != collision.pk
        assert doc.authors.get().slug != collision.slug

    def test_long_fallback_author_is_rejected_without_orphans(
        self, personal_owner, settings
    ):
        personal_owner.first_name = "a" * 150
        with pytest.raises(ValidationError, match="autora"):
            ingest_personal(personal_owner, _upload_form(author=""))
        assert not Document.objects.exists() and not list(
            settings.DATA_DIR.rglob("*.md")
        )

    def test_upload_rejects_outer_transaction_before_leaving_files(
        self, personal_owner, settings
    ):
        with transaction.atomic(), pytest.raises(RuntimeError, match="durable"):
            ingest_personal(personal_owner, _upload_form())
        assert not Document.objects.exists() and not list(
            settings.DATA_DIR.rglob("*.md")
        )

    @pytest.mark.parametrize("same_content", [True, False])
    def test_concurrent_upload_deduplication_and_limit(
        self, settings, monkeypatch, isolated_sqlite, same_content
    ):
        with isolated_sqlite.schema_editor() as editor:
            for model in (User, Author, Document, Chunk, Consent):
                editor.create_model(model)
        owner = User.objects.create_user("równoległy")
        settings.PERSONAL_MAX_DOCS = 1
        settings.DATA_DIR = (
            Path(isolated_sqlite.settings_dict["NAME"]).parent / "uploads"
        )
        barrier = Barrier(2)
        parser = personal.build_chunks
        modes = []
        original_author = personal._get_author

        def locked_author(name):
            modes.append(connection.transaction_mode)
            return original_author(name)

        def together(path):
            result = parser(path)
            barrier.wait(timeout=10)
            return result

        monkeypatch.setattr(personal, "build_chunks", together)
        monkeypatch.setattr(personal, "_get_author", locked_author)

        def upload(number):
            database = isolated_sqlite.copy()
            connections._connections.default = database
            try:
                data = MD if same_content else MD + str(number).encode()
                return ingest_personal(owner, _upload_form(data))
            except ValidationError as exc:
                return exc
            finally:
                database.close()

        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(upload, number) for number in range(2)]
            results = [future.result(timeout=30) for future in futures]
        assert sum(isinstance(result, Document) for result in results) == 1
        errors = [result for result in results if isinstance(result, ValidationError)]
        assert len(errors) == 1
        assert ("już w Twoich" if same_content else "Limit") in str(errors[0])
        assert modes == ["IMMEDIATE"]
        assert owner.documents.count() == 1 and Author.objects.count() == 1
        assert len(list(settings.DATA_DIR.rglob("*.md"))) == 1
        assert isolated_sqlite.transaction_mode == "DEFERRED"

    def test_postgres_branch_locks_user_before_document_reads(
        self, personal_owner, monkeypatch
    ):
        locked = Mock()
        locked.get.return_value = personal_owner
        select = Mock(return_value=locked)
        monkeypatch.setattr(connection, "vendor", "postgresql")
        monkeypatch.setattr(User.objects, "select_for_update", select)
        ingest_personal(personal_owner, _upload_form())
        assert select.call_count == 2  # SQL zapis + osobna próba indeksowania po commit
        locked.get.assert_called_with(pk=personal_owner.pk)

    def test_migration_preserves_existing_duplicates_and_consent(
        self, isolated_sqlite, tmp_path
    ):
        before = [("library", "0008_consent")]
        after = [("library", "0009_personal_upload_state")]
        executor = MigrationExecutor(isolated_sqlite)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        OldUser = apps.get_model("auth", "User")
        OldDoc = apps.get_model("library", "Document")
        OldChunk = apps.get_model("library", "Chunk")
        OldConsent = apps.get_model("library", "Consent")
        owner = OldUser.objects.create(username="migration")
        source = tmp_path / "existing.md"
        source.write_bytes(MD)
        first = OldDoc.objects.create(
            title="pierwszy",
            owner=owner,
            content_hash="a" * 32,
            source_path=str(source),
        )
        duplicate = OldDoc.objects.create(
            title="drugi",
            owner=owner,
            content_hash="a" * 32,
            source_path=str(source),
            access="licensed",
        )
        OldChunk.objects.create(document=duplicate, order=0, text="tekst")
        OldConsent.objects.create(
            document=duplicate,
            user=owner,
            statement="zgoda",
            license="licencja",
            register="scientific",
        )
        OldDoc.objects.create(title="bez właściciela", content_hash="a" * 32)
        OldDoc.objects.create(title="bez właściciela 2", content_hash="a" * 32)
        executor = MigrationExecutor(isolated_sqlite)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        NewDoc = apps.get_model("library", "Document")
        NewChunk = apps.get_model("library", "Chunk")
        NewConsent = apps.get_model("library", "Consent")
        assert NewDoc.objects.count() == 4
        assert NewDoc.objects.get(pk=first.pk).content_hash == "a" * 32
        migrated = NewDoc.objects.get(pk=duplicate.pk)
        assert migrated.content_hash == "" and migrated.access == "licensed"
        assert migrated.index_status == "pending" and migrated.note == ""
        assert NewChunk.objects.filter(document_id=duplicate.pk).count() == 1
        assert NewConsent.objects.get(document_id=duplicate.pk).revoked_at is None
        assert source.read_bytes() == MD
        with pytest.raises(IntegrityError), transaction.atomic():
            NewDoc.objects.create(
                title="nowy dublet", owner_id=owner.pk, content_hash="a" * 32
            )
        # Przygotowanie danych jest ponawialne (bez kolejnego kasowania hashy).
        migration = importlib.import_module(
            "library.migrations.0009_personal_upload_state"
        )
        with isolated_sqlite.schema_editor() as editor:
            migration.prepare_owned_documents(apps, editor)
        assert NewDoc.objects.get(pk=first.pk).content_hash == "a" * 32


def test_form_lengths_match_persisted_fields():
    assert (
        UploadForm.base_fields["title"].max_length
        == Document._meta.get_field("title").max_length
    )
    assert (
        UploadForm.base_fields["author"].max_length
        == Author._meta.get_field("name").max_length
    )
    assert (
        UploadForm.base_fields["note"].max_length
        == Document._meta.get_field("note").max_length
    )
    assert (
        ShareForm.base_fields["license"].max_length
        == Document._meta.get_field("license").max_length
    )
    assert (
        ShareForm.base_fields["license"].max_length
        <= Consent._meta.get_field("license").max_length
    )
    for field in ("title", "author", "note"):
        limit = UploadForm.base_fields[field].max_length
        values = {
            "title": "t",
            "doc_type": "script",
            "register": "popular",
            field: "x" * (limit + 1),
        }
        form = UploadForm(values, {"file": SimpleUploadedFile("material.md", MD)})
        assert not form.is_valid() and field in form.errors
    form = ShareForm({"license": "x" * 129, "register": "scientific", "accept": True})
    assert not form.is_valid() and "license" in form.errors
