"""Offline retrieval tests: SQL is authoritative even with stale OS hits."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from library import search
from library.models import Author, Chunk, Document

pytestmark = pytest.mark.django_db


def os_hit(document, *, exact=True):
    source = {
        "document_id": document.pk,
        "order": 0,
        "title": "stale title",
        "authors": ["stale author"],
        "access": "open",
        "owner_id": 0,
        "section": "",
        "text": "maszynowy przekład",
        "sigla_labels": [],
    }
    if exact:
        source["text_exact"] = "Original source text"
    return {"_id": f"{document.pk}:0", "_source": source}


def fake_opensearch(monkeypatch, settings, hits):
    settings.SEARCH_BACKEND = "opensearch"
    calls = []

    def query(**kwargs):
        calls.append(kwargs["body"])
        return {"hits": {"hits": hits}}

    monkeypatch.setattr(
        "corpus.search.client.get_client", lambda: SimpleNamespace(search=query)
    )
    monkeypatch.setattr("library.embeddings.embed", lambda *a, **kw: [[0.0]])
    monkeypatch.setattr(search, "HAS_ENGLISH_DOCS", lambda: False)
    return calls


def test_stale_acl_deleted_document_and_owners_are_rechecked(monkeypatch, settings):
    owner = User.objects.create_user("owner")
    other = User.objects.create_user("other")
    revoked = Document.objects.create(title="revoked", access="open")
    deleted = Document.objects.create(title="deleted")
    foreign = Document.objects.create(
        title="foreign personal", access="personal", owner=other
    )
    licensed = Document.objects.create(title="shared licensed", access="licensed")
    own = Document.objects.create(title="own licensed", access="licensed", owner=owner)
    personal = Document.objects.create(
        title="own personal", access="personal", owner=owner
    )
    public = Document.objects.create(title="public")
    author = Author.objects.create(name="Current author", slug="current")
    own.authors.add(author)
    hits = [
        os_hit(d) for d in (revoked, deleted, foreign, licensed, own, personal, public)
    ]
    Document.objects.filter(pk=revoked.pk).update(access="private")
    deleted.delete()
    calls = fake_opensearch(monkeypatch, settings, hits)

    result = search._retrieve_opensearch(
        "test", 3, ["open", "personal"], None, None, user_id=owner.pk
    )
    assert [h.title for h in result] == ["own licensed", "own personal", "public"]
    assert result[0].authors == ["Current author"]
    assert result[0].access == "licensed"
    assert all(h.text == "Original source text" for h in result)
    assert len(calls) == 2  # BM25 and kNN both use the same SQL final check
    assert {"term": {"owner_id": owner.pk}} in calls[0]["query"]["bool"]["filter"][0][
        "bool"
    ]["should"]


def test_original_text_fallback_for_old_index(monkeypatch, settings):
    document = Document.objects.create(title="old index")
    fake_opensearch(monkeypatch, settings, [os_hit(document, exact=False)])
    result = search._retrieve_opensearch("test", 1, ["open"], None, None)
    assert result[0].text == "maszynowy przekład"


@pytest.mark.parametrize("backend", ["db", "opensearch"])
def test_ownership_at_all_levels_and_personal_only(monkeypatch, settings, backend):
    owner = User.objects.create_user("owner")
    other = User.objects.create_user("other")
    own = []
    for level in ("open", "licensed", "private", "personal"):
        document = Document.objects.create(title=level, access=level, owner=owner)
        Chunk.objects.create(document=document, order=0, text="Testowanie dostępu")
        own.append(document)
    foreign = Document.objects.create(title="foreign", access="personal", owner=other)
    Chunk.objects.create(document=foreign, order=0, text="Testowanie dostępu")
    if backend == "opensearch":
        fake_opensearch(monkeypatch, settings, [os_hit(d) for d in [*own, foreign]])
    else:
        settings.SEARCH_BACKEND = "db"
    result = search.retrieve(
        "Testowanie", k=8, access=[], user_id=owner.pk, personal_only=True
    )
    assert {h.document_id for h in result} == {d.pk for d in own}
    assert (
        search.retrieve("Testowanie", k=8, access=["personal"], personal_only=True)
        == []
    )
    assert search.retrieve("Testowanie", k=8, access=[]) == []


def test_sql_register_and_author_changes_override_index(monkeypatch, settings):
    document = Document.objects.create(title="current", register="popular")
    fake_opensearch(monkeypatch, settings, [os_hit(document)])
    assert (
        search._retrieve_opensearch("test", 1, ["open"], ["stale author"], None) == []
    )
    assert (
        search._retrieve_opensearch(
            "test", 1, ["open"], None, None, registers=["scientific"]
        )
        == []
    )
