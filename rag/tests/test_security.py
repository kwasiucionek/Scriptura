"""Local endpoint and limiter tests; no LLM/OpenSearch services required."""

import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.core.cache import cache, caches
from django.test import Client, RequestFactory, override_settings

from rag import views


@pytest.fixture
def offline_ask(monkeypatch, settings):
    settings.ALLOW_ANONYMOUS = True
    settings.RATE_LIMIT_ANON = 0
    calls = []

    def ask(*args, **kwargs):
        calls.append((args, kwargs))
        yield "done", {"answer": "ok"}

    monkeypatch.setattr(views.service, "ask", ask)
    return calls


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        "text",
        1,
        True,
        {"question": []},
        {"question": {}},
        {"question": 42},
        {"question": None},
        {"question": "test", "authors": "abc"},
        {"question": "test", "authors": [1]},
        {"question": "test", "works": {}},
        {"question": "test", "works": [None]},
        {"question": "test", "mode": []},
        {"question": "test", "mode": "unknown"},
        {"question": "test", "include_ane": "false"},
        {"question": "test", "include_patristics": 1},
        {"question": "test", "personal_only": []},
        {"question": "test", "conversation_id": "bad"},
        {"question": "test", "conversation_id": True},
        {"question": "test", "conversation_id": -1},
        {"question": "test", "conversation_id": 2**64},
    ],
)
def test_ask_bad_schema_is_400_before_rate_limit(payload, offline_ask, monkeypatch):
    def unexpected_limit(*args):
        pytest.fail("invalid request must not consume rate quota")

    monkeypatch.setattr(views, "_rate_ok", unexpected_limit)
    response = Client().post(
        "/ask/stream", data=json.dumps(payload), content_type="application/json"
    )
    assert response.status_code == 400
    assert offline_ask == []


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"\xff",
        b'{"question": NaN}',
        b"[" * 10000 + b"]" * 10000,
        b'{"conversation_id": ' + b"9" * 5000 + b"}",
    ],
)
def test_invalid_json_is_400(body, offline_ask):
    assert (
        Client()
        .post("/ask/stream", data=body, content_type="application/json")
        .status_code
        == 400
    )


def test_stream_requires_csrf_cookie_and_header(offline_ask):
    client = Client(enforce_csrf_checks=True)
    kwargs = {
        "data": json.dumps({"question": "test"}),
        "content_type": "application/json",
    }
    assert client.post("/ask/stream", **kwargs).status_code == 403
    token = "a" * 32
    client.cookies["csrftoken"] = token
    assert client.post("/ask/stream", **kwargs).status_code == 403
    assert (
        client.post("/ask/stream", HTTP_X_CSRFTOKEN="b" * 32, **kwargs).status_code
        == 403
    )
    response = client.post("/ask/stream", HTTP_X_CSRFTOKEN=token, **kwargs)
    assert response.status_code == 200
    assert b"event: done" in b"".join(response.streaming_content)
    assert len(offline_ask) == 1


@pytest.mark.parametrize("selection", [None, [], ["LICENSED"]])
def test_ask_preserves_explicit_work_selection(offline_ask, selection):
    payload = {"question": "test"}
    if selection is not None:
        payload["works"] = selection
    response = Client().post(
        "/ask/stream", json.dumps(payload), content_type="application/json"
    )
    assert response.status_code == 200
    list(response.streaming_content)
    assert offline_ask[0][1]["works"] == selection


def test_fixed_hourly_window_does_not_slide(monkeypatch, settings):
    cache.clear()
    settings.RATE_LIMIT_ANON = 2
    request = RequestFactory().post("/", REMOTE_ADDR="192.0.2.1")
    monkeypatch.setattr(views.time, "time", lambda: 3601)
    assert views._rate_ok(request, None)
    monkeypatch.setattr(views.time, "time", lambda: 7199)
    assert views._rate_ok(request, None)
    assert not views._rate_ok(request, None)
    monkeypatch.setattr(views.time, "time", lambda: 7200)
    assert views._rate_ok(request, None)
    assert views._rate_ok(request, None)
    assert not views._rate_ok(request, None)
    cache.clear()


def test_locmem_thread_safety(monkeypatch, settings):
    cache.clear()
    settings.RATE_LIMIT_ANON = 7
    monkeypatch.setattr(views.time, "time", lambda: 4000)
    request = RequestFactory().post("/", REMOTE_ADDR="192.0.2.2")
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: views._rate_ok(request, None), range(50)))
    assert sum(results) == 7
    cache.clear()


def _file_worker(args):
    # Each process has an independent cache instance and Python lock.
    import django

    django.setup()
    location, attempts = args
    config = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": location,
            "OPTIONS": {"MAX_ENTRIES": 10000},
        }
    }
    with override_settings(CACHES=config, RATE_LIMIT_ANON=11):
        request = SimpleNamespace(META={"REMOTE_ADDR": "192.0.2.3"})
        return sum(views._rate_ok(request, None) for _ in range(attempts))


def test_file_cache_process_safety(tmp_path):
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("fork")
    ) as pool:
        results = list(pool.map(_file_worker, [(str(tmp_path), 15)] * 4))
    assert sum(results) == 11
    assert list(tmp_path.glob(".rate-lock-*"))


def test_file_cache_thread_safety(tmp_path, settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": str(tmp_path),
        }
    }
    settings.RATE_LIMIT_ANON = 5
    request = RequestFactory().post("/", REMOTE_ADDR="192.0.2.4")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: views._rate_ok(request, None), range(30)))
    assert sum(results) == 5


def test_unavailable_or_unsupported_cache_fails_closed(monkeypatch, settings):
    settings.RATE_LIMIT_ANON = 1
    request = RequestFactory().post("/")
    monkeypatch.setattr(
        caches["default"],
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("offline")),
    )
    assert not views._rate_ok(request, None)
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}
    }
    assert not views._rate_ok(request, None)


def test_native_atomic_cache_path_with_fake(monkeypatch, settings):
    from django.core.cache.backends.redis import RedisCache

    class AtomicCache(RedisCache):
        def __init__(self):
            self.value = None
            self.timeouts = []

        def add(self, key, value, timeout):
            if self.value is None:
                self.value = value
                self.timeouts.append(timeout)
                return True
            return False

        def incr(self, key):
            self.value += 1
            return self.value

    backend = AtomicCache()
    settings.RATE_LIMIT_ANON = 2
    request = RequestFactory().post("/")
    with monkeypatch.context() as patch:
        patch.setattr("django.core.cache.caches", {"default": backend})
        patch.setattr(views.time, "time", lambda: 7190)
        assert views._rate_ok(request, None)
        assert views._rate_ok(request, None)
        assert not views._rate_ok(request, None)
        assert backend.timeouts == [10]  # increment never extends the expiry


def test_cache_initialization_failure_is_closed(monkeypatch, settings):
    class Unavailable:
        def __getitem__(self, key):
            raise OSError("cannot initialize cache")

    settings.RATE_LIMIT_ANON = 1
    with monkeypatch.context() as patch:
        patch.setattr("django.core.cache.caches", Unavailable())
        assert not views._rate_ok(RequestFactory().post("/"), None)


def test_identity_and_disabled_limit(settings):
    cache.clear()
    settings.RATE_LIMIT_USER = 1
    request = RequestFactory().post("/")
    assert views._rate_ok(request, SimpleNamespace(id=1))
    assert not views._rate_ok(request, SimpleNamespace(id=1))
    assert views._rate_ok(request, SimpleNamespace(id=2))
    settings.RATE_LIMIT_USER = 0
    assert views._rate_ok(request, SimpleNamespace(id=1))
    cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize("fmt", ["bib", "ris"])
def test_export_requires_csrf_and_never_falls_back_to_client_metadata(fmt):
    from django.contrib.auth.models import User

    from library.models import Document

    user = User.objects.create_user("export-owner")
    other = User.objects.create_user("export-other")
    public = Document.objects.create(title="SQL public title", doi="10/verified")
    own = Document.objects.create(
        title="SQL own licensed title", access="licensed", owner=user
    )
    hidden = Document.objects.create(
        title="SQL hidden title", access="licensed", doi="10/secret"
    )
    foreign = Document.objects.create(
        title="SQL foreign personal", access="personal", owner=other
    )
    revoked = Document.objects.create(title="SQL revoked title")
    deleted = Document.objects.create(title="SQL deleted title")
    chunks = [
        {
            "n": n,
            "document_id": doc.pk,
            "title": "PAYLOAD title fallback",
            "authors": ["PAYLOAD author fallback"],
            "url": "https://payload.invalid/secret",
        }
        for n, doc in enumerate((public, own, hidden, foreign, revoked, deleted), 1)
    ]
    chunks.append({"n": 7, "title": "PAYLOAD ID-less fallback"})
    Document.objects.filter(pk=revoked.pk).update(access="private")
    deleted.delete()
    payload = json.dumps({"sources": {"chunks": chunks}, "format": fmt})
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    kwargs = {"data": payload, "content_type": "application/json"}
    assert client.post("/export/citations", **kwargs).status_code == 403
    token = "a" * 32
    client.cookies["csrftoken"] = token
    assert client.post("/export/citations", **kwargs).status_code == 403
    assert (
        client.post(
            "/export/citations", HTTP_X_CSRFTOKEN="b" * 32, **kwargs
        ).status_code
        == 403
    )
    response = client.post("/export/citations", HTTP_X_CSRFTOKEN=token, **kwargs)
    assert response.status_code == 200
    text = response.content.decode()
    assert "SQL public title" in text and "10/verified" in text
    assert "SQL own licensed title" in text
    for secret in (
        "SQL hidden",
        "10/secret",
        "SQL foreign",
        "SQL revoked",
        "SQL deleted",
        "PAYLOAD",
        "payload.invalid",
    ):
        assert secret not in text
    # A valid CSRF token never changes ACL or enables client metadata fallback.
    response = client.post(
        "/export/citations",
        json.dumps({"sources": {"chunks": chunks[2:]}, "format": fmt}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"format": None},
        {"format": "xml"},
        {"sources": []},
        {"sources": {"chunks": [{"document_id": []}]}},
    ],
)
def test_export_invalid_schema_with_valid_csrf_still_returns_400(payload):
    client = Client(enforce_csrf_checks=True)
    token = "a" * 32
    client.cookies["csrftoken"] = token
    response = client.post(
        "/export/citations",
        json.dumps(payload),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 400
