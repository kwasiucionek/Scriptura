import json

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client

from library.models import Author, Document
from rag.access import GROUP_LICENSED
from rag.citations import build_entries

pytestmark = pytest.mark.django_db


def source(document, n):
    return {
        "n": n,
        "document_id": document.pk,
        "title": "client-supplied metadata",
        "authors": ["forged author"],
    }


def test_export_uses_current_sql_acl_and_never_falls_back_to_payload(client):
    owner = User.objects.create_user("owner")
    other = User.objects.create_user("other")
    public = Document.objects.create(title="Public", doi="10/public")
    licensed = Document.objects.create(
        title="Licensed", access="licensed", doi="10/secret"
    )
    private = Document.objects.create(title="Private", access="private")
    own = Document.objects.create(title="Own licensed", access="licensed", owner=owner)
    personal = Document.objects.create(
        title="Own personal", access="personal", owner=owner
    )
    foreign = Document.objects.create(
        title="Foreign personal", access="personal", owner=other
    )
    deleted = Document.objects.create(title="Deleted")
    revoked = Document.objects.create(title="Revoked")
    chunks = [
        source(d, n)
        for n, d in enumerate(
            (public, licensed, private, own, personal, foreign, deleted, revoked), 1
        )
    ]
    chunks.append({"n": 9, "title": "ID-less fallback must not leak"})
    payload = {"sources": {"chunks": chunks}, "answer": "", "format": "bib"}
    # Simulate an answer saved before deletion/revocation.
    deleted.delete()
    Document.objects.filter(pk=revoked.pk).update(access="private")
    author = Author.objects.create(name="Current Author", slug="current")
    public.authors.add(author)

    response = client.post(
        "/export/citations", json.dumps(payload), content_type="application/json"
    )
    assert response.status_code == 200
    text = response.content.decode()
    assert "Public" in text and "Current" in text
    for secret in (
        "Licensed",
        "Private",
        "Foreign",
        "Revoked",
        "Deleted",
        "10/secret",
        "client-supplied",
        "forged",
        "ID-less",
    ):
        assert secret not in text

    client.force_login(owner)
    response = client.post(
        "/export/citations", json.dumps(payload), content_type="application/json"
    )
    text = response.content.decode()
    assert "Own licensed" in text and "Own personal" in text
    assert "Foreign" not in text
    owner.groups.add(Group.objects.create(name=GROUP_LICENSED))
    assert {e["title"] for e in build_entries(payload["sources"], user=owner)} == {
        "Public",
        "Licensed",
        "Own licensed",
        "Own personal",
    }


def test_superuser_does_not_export_foreign_personal_metadata():
    root = User.objects.create_superuser("root", password="x")
    owner = User.objects.create_user("owner")
    hidden = Document.objects.create(title="personal", access="personal", owner=owner)
    assert build_entries({"chunks": [source(hidden, 1)]}, user=root) == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        "text",
        1,
        {"format": []},
        {"format": "xml"},
        {"format": None},
        {"sources": []},
        {"sources": None},
        {"answer": []},
        {"sources": {"chunks": {}}},
        {"sources": {"chunks": [None]}},
        {"sources": {"chunks": [{"document_id": []}]}},
        {"sources": {"chunks": [{"document_id": True}]}},
        {"sources": {"chunks": [{"document_id": 2**64}]}},
        {"sources": {"chunks": [{"authors": "author"}]}},
        {"sources": {"chunks": [{"year": {}}]}},
        {"sources": {"patristics": [{"ref": None}]}},
        {"sources": {"patristics": [{"author": []}]}},
        {"sources": {"ane": [{"text": []}]}},
    ],
)
def test_bad_export_schema_is_400(client, payload):
    assert (
        client.post(
            "/export/citations", json.dumps(payload), content_type="application/json"
        ).status_code
        == 400
    )


@pytest.mark.parametrize("body", [b"{", b"\xff", b"[" * 10000 + b"]" * 10000])
def test_bad_export_json_is_400(client, body):
    assert (
        client.post(
            "/export/citations", body, content_type="application/json"
        ).status_code
        == 400
    )


@pytest.mark.parametrize("fmt", ["bib", "ris"])
@pytest.mark.parametrize(
    "answer,expected",
    [
        ("[P1, 2]", {"Pat1", "Pat2"}),
        ("[P1; P2]", {"Pat1", "Pat2"}),
        ("[1—3]", {"SQL public", "SQL own licensed"}),
        ("[1; typo]", set()),
        ("[1—P2]", set()),
        ("[1—201]", set()),
        ("[1", set()),
        ("[1; typo] [3]", {"SQL own licensed"}),
        ("", {"SQL public", "SQL own licensed", "Pat1", "Pat2", "ANE1", "ANE2"}),
    ],
)
def test_shared_marker_grammar_never_bypasses_sql_acl_or_restores_payload_metadata(
    fmt, answer, expected
):
    owner = User.objects.create_user("marker-owner")
    hidden = Document.objects.create(
        title="SQL secret", access="licensed", doi="10/secret"
    )
    public = Document.objects.create(title="SQL public", doi="10/public")
    own = Document.objects.create(
        title="SQL own licensed", access="licensed", owner=owner
    )
    deleted = Document.objects.create(title="SQL deleted")
    revoked = Document.objects.create(title="SQL revoked")
    chunks = [
        source(d, n) for n, d in enumerate((hidden, public, own, deleted, revoked), 1)
    ]
    chunks.append({"n": 6, "title": "ID-less fallback"})
    deleted.delete()
    Document.objects.filter(pk=revoked.pk).update(access="private")
    sources = {
        "chunks": chunks,
        "patristics": [{"author": f"Ojciec{n}", "work": f"Pat{n}"} for n in (1, 2)],
        "ane": [{"text": f"ANE{n}"} for n in (1, 2)],
    }
    entries = build_entries(sources, answer, user=owner)
    assert {e["title"] for e in entries} == expected
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    token = "a" * 32
    client.cookies["csrftoken"] = token
    response = client.post(
        "/export/citations",
        json.dumps({"sources": sources, "answer": answer, "format": fmt}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == (200 if expected else 404)
    text = response.content.decode()
    for title in expected:
        assert title in text
    for other_title in {
        "SQL public",
        "SQL own licensed",
        "Pat1",
        "Pat2",
        "ANE1",
        "ANE2",
    } - expected:
        assert other_title not in text
    for secret in (
        "SQL secret",
        "10/secret",
        "SQL deleted",
        "SQL revoked",
        "client-supplied metadata",
        "forged author",
        "ID-less fallback",
    ):
        assert secret not in text


def test_very_long_citation_number_is_not_an_exception(client):
    response = client.post(
        "/export/citations",
        json.dumps({"answer": "[" + "9" * 5000 + "]"}),
        content_type="application/json",
    )
    assert response.status_code == 404
