import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from corpus.models import Work
from library.models import Author, Document

pytestmark = pytest.mark.django_db


def authored(title, access, *, owner=None):
    author = Author.objects.create(name=title, slug=title)
    document = Document.objects.create(title=title, access=access, owner=owner)
    document.authors.add(author)
    return document


def test_chat_author_selector_only_contains_accessible_authors(client):
    owner = User.objects.create_user("owner")
    other = User.objects.create_user("other")
    authored("Public", "open")
    authored("Licensed", "licensed")
    authored("Private", "private")
    authored("OwnLicensed", "licensed", owner=owner)
    authored("OwnPersonal", "personal", owner=owner)
    authored("ForeignPersonal", "personal", owner=other)
    Author.objects.create(name="Empty", slug="empty")
    response = client.get(reverse("rag:index"))
    assert {a.name for a in response.context["authors"]} == {"Public"}
    client.force_login(owner)
    response = client.get(reverse("rag:index"))
    assert {a.name for a in response.context["authors"]} == {
        "Public",
        "OwnLicensed",
        "OwnPersonal",
    }


def test_metadata_is_not_retained_after_acl_changes(client):
    document = authored("Revoked", "open")
    deleted = authored("Deleted", "open")
    work = Work.objects.create(
        code="SECRET", name="secret", language="pl", kind="translation"
    )
    first = client.get(reverse("pages:corpus"))
    assert {d.pk for d in first.context["s"]["recent"]} == {document.pk, deleted.pk}
    assert [w.code for w in first.context["s"]["works"]] == ["SECRET"]
    Document.objects.filter(pk=document.pk).update(access="private")
    Work.objects.filter(pk=work.pk).update(access="licensed")
    deleted.delete()
    second = client.get(reverse("pages:corpus"))
    stats = second.context["s"]
    assert stats["recent"] == [] and stats["authors"] == [] and stats["works"] == []
    assert stats["docs_total"] == 0
    assert client.get(reverse("pages:guide")).context["works"] == []
    assert client.get(reverse("rag:index")).context["works"] == []


def test_corpus_stats_use_ownership_without_cross_user_cache(client):
    owner = User.objects.create_user("owner")
    other = User.objects.create_user("other")
    authored("OwnLicensed", "licensed", owner=owner)
    client.force_login(owner)
    stats = client.get(reverse("pages:corpus")).context["s"]
    assert stats["docs_total"] == 1
    assert [a.name for a in stats["authors"]] == ["OwnLicensed"]
    client.force_login(other)
    stats = client.get(reverse("pages:corpus")).context["s"]
    assert stats["docs_total"] == 0 and stats["authors"] == []
