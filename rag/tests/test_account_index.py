"""Offline account adapter tests: SQL commits and index status are distinct."""

from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import SUCCESS, WARNING, get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from library.models import Document, IndexStatus
from rag import views

pytestmark = pytest.mark.django_db
SECRET = "https://index.internal/?api_key=never-show-this-secret"
TOKEN = "a" * 32


@pytest.fixture
def account_client():
    user = User.objects.create_user("owner")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    client.cookies["csrftoken"] = TOKEN
    return client, user


def post_account(client, data):
    return client.post(reverse("rag:account"), data, HTTP_X_CSRFTOKEN=TOKEN)


def operation_data(action, doc_id):
    data = {"action": action, "doc_id": str(doc_id)}
    if action == "upload":
        data.update(
            title="Materiał",
            doc_type="script",
            register="mixed",
            file=SimpleUploadedFile("notes.txt", b"Offline material"),
        )
    elif action == "share":
        data.update(register="scientific", license="za zgodą autora", accept="on")
    return data


@pytest.mark.parametrize("action", ["upload", "share", "unshare", "retry_index"])
@pytest.mark.parametrize("status", list(IndexStatus.values))
def test_committed_sql_and_index_status_have_distinct_safe_messages(
    account_client, monkeypatch, action, status
):
    client, user = account_client
    doc = Document.objects.create(
        title="Materiał",
        owner=user,
        access="personal",
        chunk_count=3,
        index_status=status,
        index_error=SECRET,
    )
    handler_name = {
        "upload": "ingest_personal",
        "share": "share_personal",
        "unshare": "unshare_personal",
        "retry_index": "retry_personal_index",
    }[action]
    handler = Mock(return_value=doc)
    monkeypatch.setattr(views, handler_name, handler)
    response = post_account(client, operation_data(action, doc.pk))
    assert response.status_code == 302
    assert response.url == reverse("rag:account")
    handler.assert_called_once()
    assert handler.call_args.args[0].pk == user.pk
    if action != "upload":
        assert handler.call_args.args[1] == doc.pk
    notices = list(get_messages(response.wsgi_request))
    assert len(notices) == 1
    notice = notices[0]
    assert "bazy" in str(notice) or "bazie" in str(notice)
    assert "Materiał" in str(notice)
    assert SECRET not in str(notice)
    assert "api_key" not in str(notice)
    expected = (
        WARNING if status in (IndexStatus.FAILED, IndexStatus.PENDING) else SUCCESS
    )
    assert notice.level == expected
    if status == IndexStatus.FAILED:
        assert "Nie udało się zaktualizować indeksu" in str(notice)
        assert "zapis w bazie pozostaje ważny" in str(notice)
    elif status == IndexStatus.PENDING:
        assert "oczekuje na aktualizację" in str(notice)
    elif status == IndexStatus.NOT_REQUIRED:
        assert "nie jest wymagany" in str(notice)
    else:
        assert "jest aktualny" in str(notice)
    if status in (IndexStatus.FAILED, IndexStatus.PENDING):
        assert "ponowić indeksowanie" in str(notice)


@pytest.mark.parametrize(
    "action", ["upload", "share", "unshare", "retry_index", "delete"]
)
def test_account_post_actions_require_csrf(account_client, monkeypatch, action):
    client, user = account_client
    doc = Document.objects.create(title="Materiał", owner=user, access="personal")
    for handler in (
        "ingest_personal",
        "share_personal",
        "unshare_personal",
        "retry_personal_index",
        "delete_personal",
    ):
        monkeypatch.setattr(
            views,
            handler,
            Mock(side_effect=AssertionError("must not run without CSRF")),
        )
    response = client.post(reverse("rag:account"), operation_data(action, doc.pk))
    assert response.status_code == 403
    response = client.post(
        reverse("rag:account"),
        operation_data(action, doc.pk),
        HTTP_X_CSRFTOKEN="b" * 32,
    )
    assert response.status_code == 403
    for handler in (
        "ingest_personal",
        "share_personal",
        "unshare_personal",
        "retry_personal_index",
        "delete_personal",
    ):
        getattr(views, handler).assert_not_called()


@pytest.mark.parametrize("access", ["open", "licensed", "personal"])
@pytest.mark.parametrize("superuser", [False, True])
def test_retry_is_owner_only_even_for_public_docs_and_superusers(
    account_client, monkeypatch, access, superuser
):
    client, user = account_client
    if superuser:
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
    other = User.objects.create_user("other")
    foreign = Document.objects.create(
        title="Foreign", owner=other, access=access, index_status="failed"
    )
    retry = Mock()
    monkeypatch.setattr(views, "retry_personal_index", retry)
    response = post_account(client, {"action": "retry_index", "doc_id": foreign.pk})
    assert response.status_code == 404
    retry.assert_not_called()
    foreign.refresh_from_db()
    assert foreign.index_revision == 0


@pytest.mark.parametrize(
    "doc_id", [None, "", "bad", "1.5", "0", "-1", str(2**63), "9" * 5000]
)
def test_retry_invalid_id_is_400(account_client, monkeypatch, doc_id):
    client, _ = account_client
    retry = Mock()
    monkeypatch.setattr(views, "retry_personal_index", retry)
    data = {"action": "retry_index"}
    if doc_id is not None:
        data["doc_id"] = doc_id
    assert post_account(client, data).status_code == 400
    retry.assert_not_called()


def test_retry_missing_document_is_404(account_client, monkeypatch):
    client, _ = account_client
    retry = Mock()
    monkeypatch.setattr(views, "retry_personal_index", retry)
    assert (
        post_account(client, {"action": "retry_index", "doc_id": 999999}).status_code
        == 404
    )
    retry.assert_not_called()


def test_retry_get_does_not_mutate(account_client, monkeypatch):
    client, user = account_client
    doc = Document.objects.create(title="Materiał", owner=user, access="personal")
    retry = Mock()
    monkeypatch.setattr(views, "retry_personal_index", retry)
    assert (
        client.get(
            reverse("rag:account"), {"action": "retry_index", "doc_id": doc.pk}
        ).status_code
        == 200
    )
    retry.assert_not_called()


def test_retry_requires_login(monkeypatch):
    retry = Mock()
    monkeypatch.setattr(views, "retry_personal_index", retry)
    client = Client(enforce_csrf_checks=True)
    client.cookies["csrftoken"] = TOKEN
    response = post_account(client, {"action": "retry_index", "doc_id": 1})
    assert response.status_code == 302
    assert response.url.startswith(reverse("rag:login"))
    retry.assert_not_called()


def test_retry_exception_is_not_exposed(account_client, monkeypatch):
    client, user = account_client
    doc = Document.objects.create(
        title="Materiał", owner=user, access="licensed", index_status="failed"
    )
    monkeypatch.setattr(
        views, "retry_personal_index", Mock(side_effect=RuntimeError(SECRET))
    )
    response = post_account(client, {"action": "retry_index", "doc_id": doc.pk})
    assert response.status_code == 302
    notice = str(list(get_messages(response.wsgi_request))[0])
    assert "Nie udało się ponowić indeksowania" in notice
    assert SECRET not in notice


@pytest.mark.parametrize("error", [RuntimeError(SECRET), ValidationError(SECRET)])
def test_upload_exception_is_not_exposed_in_form(account_client, monkeypatch, error):
    client, _ = account_client
    monkeypatch.setattr(views, "ingest_personal", Mock(side_effect=error))
    response = post_account(client, operation_data("upload", 0))
    assert response.status_code == 200
    errors = str(response.context["upload_form"].non_field_errors())
    assert errors
    assert SECRET not in errors
    assert SECRET.encode() not in response.content


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "backend,status",
    [
        ("db", IndexStatus.NOT_REQUIRED),
        ("opensearch", IndexStatus.INDEXED),
        ("opensearch", IndexStatus.FAILED),
    ],
)
def test_retry_real_service_with_offline_index(
    account_client, monkeypatch, settings, backend, status
):
    client, user = account_client
    settings.SEARCH_BACKEND = backend
    doc = Document.objects.create(
        title="Materiał",
        owner=user,
        access="licensed",
        chunk_count=2,
        index_status="failed",
    )
    index = Mock(return_value=2)
    if status == IndexStatus.FAILED:
        index.side_effect = RuntimeError(SECRET)
    monkeypatch.setattr("library.search.index_document", index)
    response = post_account(client, {"action": "retry_index", "doc_id": doc.pk})
    assert response.status_code == 302
    doc.refresh_from_db()
    assert doc.index_status == status
    assert doc.index_revision == 1
    assert doc.access == "licensed" and doc.owner_id == user.pk
    assert (doc.indexed_at is not None) == (status == IndexStatus.INDEXED)
    assert index.call_count == (0 if backend == "db" else 1)
    notice = list(get_messages(response.wsgi_request))[0]
    assert notice.level == (WARNING if status == IndexStatus.FAILED else SUCCESS)
    assert SECRET not in str(notice)
