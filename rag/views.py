"""Endpointy czatu: GUI, strumień SSE /ask/stream, historia rozmów, logowanie."""

import hashlib
import json
import logging
import math
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from corpus.services import text as corpus_svc
from library.models import Author, IndexStatus
from rag import service
from rag.access import access_for_user, allowed_modes, visible_documents
from rag.models import Conversation, Message
from rag.personal import (
    CONSENT_STATEMENT,
    ProfileForm,
    ShareForm,
    UploadForm,
    delete_personal,
    ingest_personal,
    retry_personal_index,
    share_personal,
    unshare_personal,
)

# Przykładowe pytania: bez "access" widoczne dla wszystkich (korpus open je udźwignie);
# z "access": "licensed" tylko dla kont z tym dostępem (źródła licencjonowane: blog, skrypty).
EXAMPLES = [
    {
        "title": "Ugarit i początki kultu",
        "q": "Co odkrycia w Ugarit mówią o bogu El, Baalu i początkach kultu Jahwe w Izraelu?",
    },
    {
        "title": "Rdz 1 jako tekst",
        "q": "Czy Bóg stworzył świat w siedem dni? Jak rozumieć Rdz 1 jako tekst literacki i teologiczny?",
    },
    {
        "title": "Termin hebrajski",
        "q": "Co znaczy hebrajskie hesed i jak oddają je polskie przekłady?",
    },
    {
        "title": "Ojcowie Kościoła",
        "q": "Jak Ojcowie Kościoła rozumieli „uczyńmy człowieka” z Rdz 1,26?",
    },
    {
        "title": "Starożytny Bliski Wschód",
        "q": "Co łączy opis potopu w Rdz 6–9 z eposem o Gilgameszu i czym się od niego różni?",
    },
    {
        "title": "Tekst w oryginale",
        "q": "Jak brzmi Rdz 1,2 po hebrajsku i grecku i co znaczy tohu wabohu?",
    },
    {
        "title": "Historia",
        "q": "Czy Abraham istniał historycznie? Co mówią minimaliści i maksymaliści?",
    },
    {
        "title": "Języki Ewangelii",
        "q": "Czy Ewangelia Mateusza była napisana po hebrajsku?",
    },
    {
        "title": "Przekłady",
        "q": "Czym różni się Septuaginta od tekstu hebrajskiego i dlaczego to ma znaczenie dla przekładów?",
    },
    {
        "title": "Krytyka tekstu",
        "q": "Czy Pwt 32,8-9 to ślad politeizmu? Co mówią Septuaginta i Qumran o „synach Bożych” i jak zmienił to tekst masorecki?",
        "access": "licensed",
    },
    {
        "title": "Kanon",
        "q": "Dlaczego List Barnaby i Pasterz Hermasa były w Kodeksie Synajskim, a nie ma ich w dzisiejszej Biblii?",
        "access": "licensed",
    },
    {
        "title": "Imiona Boga",
        "q": "Jakie imiona Boga zna Biblia i co znaczy, że Elohim jest w liczbie mnogiej?",
        "access": "licensed",
    },
]


def _user(request: HttpRequest):
    return request.user if request.user.is_authenticated else None


def index(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated and not settings.ALLOW_ANONYMOUS:
        return redirect(f"{settings.LOGIN_URL}?next=/")
    user = _user(request)
    modes = allowed_modes(user)
    default_mode = (
        settings.RAG_DEFAULT_MODE if settings.RAG_DEFAULT_MODE in modes else modes[0]
    )
    return render(
        request,
        "rag/index.html",
        {
            "examples_json": json.dumps(
                [
                    e
                    for e in EXAMPLES
                    if not e.get("access") or e["access"] in access_for_user(user)
                ],
                ensure_ascii=False,
            ),
            "authors": Author.objects.filter(
                documents__in=visible_documents(user)
            ).distinct(),
            "works": corpus_svc.active_works(access=access_for_user(user)),
            "default_works": list(settings.RAG_VERSE_WORKS),
            "default_mode": default_mode,
            "allowed_modes": modes,
            "access_levels": access_for_user(user),
            "demo_token_required": bool(settings.DEMO_TOKEN),
            "user": request.user,
            "allow_anonymous": settings.ALLOW_ANONYMOUS,
        },
    )


def _client_ip(request: HttpRequest) -> str:
    # Deployment must overwrite X-Forwarded-For at a trusted ingress; never expose
    # this endpoint directly with client-controlled forwarding headers.
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") if settings.BEHIND_PROXY else ""
    return (
        xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")
    ) or "?"


_RATE_LOCK = threading.Lock()
log = logging.getLogger(__name__)


@contextmanager
def _file_rate_lock(backend, key):
    """Serialize file-cache RMW across threads/processes on ONE Linux host.

    All workers must share the cache directory and this code. Persistent striped
    lock files must not be deleted while workers run (unlink breaks locking).
    This is not a distributed lock for a multi-host/NFS deployment.
    """
    import fcntl

    stripe = int(hashlib.sha256(key.encode()).hexdigest(), 16) % 64
    path = Path(backend._dir) / f".rate-lock-{stripe}"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 2
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("rate-limit lock timeout") from None
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


def _rate_ok(request: HttpRequest, user) -> bool:
    """Fixed UTC hourly buckets; unavailable/unsupported cache fails closed.

    File cache: flock on one host. LocMem: thread-safe, process-local only.
    Redis/Memcached: backend-native atomic increment, shared across workers.
    Eviction/clearing loses quotas: cache capacity must cover active buckets.
    """
    from django.core.cache import caches
    from django.core.cache.backends.filebased import FileBasedCache
    from django.core.cache.backends.locmem import LocMemCache
    from django.core.cache.backends.memcached import BaseMemcachedCache
    from django.core.cache.backends.redis import RedisCache

    identity, limit = (
        (f"user:{user.id}", settings.RATE_LIMIT_USER)
        if user is not None
        else (f"ip:{_client_ip(request)}", settings.RATE_LIMIT_ANON)
    )
    if limit <= 0:
        return True
    now = time.time()
    window = int(now // 3600)
    key = f"rate:v2:{window}:{hashlib.sha256(identity.encode()).hexdigest()}"
    timeout = max(1, math.ceil((window + 1) * 3600 - now))

    def increment_locked():
        count = backend.get(key, 0)
        if count >= limit:
            return False
        backend.set(key, count + 1, timeout=timeout)
        return True

    try:
        backend = caches["default"]
        if isinstance(backend, FileBasedCache):
            with _file_rate_lock(backend, key):
                return increment_locked()
        if isinstance(backend, LocMemCache):
            with _RATE_LOCK:
                return increment_locked()
        if isinstance(backend, (RedisCache, BaseMemcachedCache)):
            backend.add(key, 0, timeout=timeout)
            return backend.incr(key) <= limit
        raise TypeError("Unsupported rate-limit cache backend")
    except Exception:  # noqa: BLE001 — nie obchodzimy limitu przy awarii
        log.exception("Rate limiter unavailable; request denied")
        return False


def _validate_ask_payload(payload) -> None:
    if not isinstance(payload, dict):
        raise ValueError("JSON musi być obiektem")
    if not isinstance(payload.get("question", ""), str):
        raise ValueError("question musi być tekstem")
    for key in ("authors", "works"):
        value = payload.get(key, [])
        if (
            not isinstance(value, list)
            or len(value) > 100
            or not all(isinstance(v, str) and len(v) <= 300 for v in value)
        ):
            raise ValueError(f"{key} musi być listą tekstów (maks. 100)")
    mode = payload.get("mode")
    if mode is not None and mode not in ("popular", "scientific"):
        raise ValueError("Niepoprawny mode")
    for key in ("personal_only", "include_patristics", "include_ane"):
        value = payload.get(key)
        if value is not None and type(value) is not bool:
            raise ValueError(f"{key} musi być wartością logiczną")
    conv_id = payload.get("conversation_id")
    if conv_id is not None and (
        type(conv_id) is not int or not 0 < conv_id <= 2**63 - 1
    ):
        raise ValueError("Niepoprawny conversation_id")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@csrf_protect
@require_POST
def ask_stream(request: HttpRequest) -> HttpResponse:
    if (
        settings.DEMO_TOKEN
        and request.headers.get("X-Demo-Token") != settings.DEMO_TOKEN
    ):
        return JsonResponse({"detail": "Nieprawidłowy token dostępu"}, status=401)
    user = _user(request)
    if user is None and not settings.ALLOW_ANONYMOUS:
        return JsonResponse({"detail": "Wymagane logowanie"}, status=401)
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError, RecursionError):
        return JsonResponse({"detail": "Niepoprawny JSON"}, status=400)
    try:
        _validate_ask_payload(payload)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    question = payload.get("question", "").strip()
    if len(question) < 3:
        return JsonResponse({"detail": "Pytanie jest za krótkie"}, status=400)
    if len(question) > settings.QUESTION_MAX_CHARS:
        return JsonResponse(
            {
                "detail": f"Pytanie jest za długie (limit {settings.QUESTION_MAX_CHARS} znaków)"
            },
            status=400,
        )
    if not _rate_ok(request, user):
        return JsonResponse(
            {
                "detail": "Limit pytań na tę godzinę wyczerpany — spróbuj później lub zaloguj się."
            },
            status=429,
        )
    authors = [a for a in payload.get("authors", []) if a]
    works = payload.get("works")
    modes = allowed_modes(user)
    mode = payload.get("mode") or settings.RAG_DEFAULT_MODE
    if mode not in modes:
        mode = modes[0]
    access = access_for_user(user)

    conversation = None
    if user is not None:
        conv_id = payload.get("conversation_id")
        if conv_id:
            conversation = Conversation.objects.filter(id=conv_id, user=user).first()
        if conversation is None:
            conversation = Conversation.objects.create(
                user=user, title=question[:200], mode=mode
            )
        Message.objects.create(
            conversation=conversation, role="user", content=question,
            meta={"authors": authors, "works": works, "mode": mode},
        )  # fmt: skip

    def gen():
        sources_payload: dict = {}
        for event, data in service.ask(
            question,
            authors=authors or None,
            works=works,
            mode=mode,
            access=access,
            user_id=user.id if user else None,
            personal_only=bool(payload.get("personal_only")) and user is not None,
            include_patristics=(
                None
                if payload.get("include_patristics") is None
                else bool(payload.get("include_patristics"))
            ),
            include_ane=(
                None
                if payload.get("include_ane") is None
                else bool(payload.get("include_ane"))
            ),
        ):
            if event == "sources":
                sources_payload = data
            if event == "done" and conversation is not None:
                Message.objects.create(
                    conversation=conversation, role="assistant", content=data.get("answer", ""),
                    meta={"result": data, "sources": sources_payload},
                )  # fmt: skip
                conversation.save(update_fields=["modified"])
                data = {**data, "conversation_id": conversation.id}
            yield _sse(event, data)

    resp = StreamingHttpResponse(gen(), content_type="text/event-stream; charset=utf-8")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"  # nginx: nie buforuj strumienia
    return resp


@login_required
@require_GET
def conversations(request: HttpRequest) -> JsonResponse:
    rows = [
        {
            "id": c.id,
            "title": c.title,
            "mode": c.mode,
            "modified": c.modified.isoformat(timespec="minutes"),
        }
        for c in request.user.conversations.all()[:50]
    ]
    return JsonResponse({"conversations": rows})


@login_required
@require_GET
def conversation_detail(request: HttpRequest, pk: int) -> JsonResponse:
    conv = get_object_or_404(Conversation, pk=pk, user=request.user)
    msgs = [
        {"role": m.role, "content": m.content, "meta": m.meta}
        for m in conv.messages.all()
    ]
    return JsonResponse(
        {"id": conv.id, "title": conv.title, "mode": conv.mode, "messages": msgs}
    )


@login_required
@require_POST
def conversation_delete(request: HttpRequest, pk: int) -> JsonResponse:
    conv = get_object_or_404(Conversation, pk=pk, user=request.user)
    conv.delete()
    return JsonResponse({"deleted": pk})


def _saved_index_message(request: HttpRequest, doc, saved: str) -> None:
    """SQL success is independent of index status; never expose index errors."""
    if doc.index_status == IndexStatus.INDEXED:
        messages.success(request, f"{saved} Indeks wyszukiwania jest aktualny.")
    elif doc.index_status == IndexStatus.NOT_REQUIRED:
        messages.success(
            request,
            f"{saved} Indeks zewnętrzny nie jest wymagany dla wyszukiwania w bazie.",
        )
    elif doc.index_status == IndexStatus.FAILED:
        messages.warning(
            request,
            f"{saved} Nie udało się zaktualizować indeksu wyszukiwania; "
            "zapis w bazie pozostaje ważny. Możesz ponowić indeksowanie.",
        )
    else:
        messages.warning(
            request,
            f"{saved} Indeks wyszukiwania oczekuje na aktualizację; "
            "wyniki mogą być niepełne. Możesz ponowić indeksowanie.",
        )


@login_required
@csrf_protect
def account(request: HttpRequest) -> HttpResponse:
    """Konto: profil, materiały osobiste i owner-only ponawianie indeksu."""
    user = request.user
    profile_form = ProfileForm(instance=user)
    password_form = PasswordChangeForm(user)
    upload_form = UploadForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile":
            profile_form = ProfileForm(request.POST, instance=user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Dane zapisane.")
                return redirect("rag:account")
        elif action == "password":
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Hasło zmienione.")
                return redirect("rag:account")
        elif action == "upload":
            upload_form = UploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                try:
                    doc = ingest_personal(user, upload_form)
                    _saved_index_message(
                        request,
                        doc,
                        f"Dodano do bazy „{doc.title}” ({doc.chunk_count} fragmentów).",
                    )
                    return redirect("rag:account")
                except ValidationError as exc:
                    detail = (
                        "Ten plik jest już w Twoich materiałach."
                        if getattr(exc, "code", None) == "duplicate_file"
                        else "Nie udało się zapisać materiału. Sprawdź plik, limity konta "
                        "i czy plik nie został już dodany."
                    )
                    upload_form.add_error(None, detail)
                except Exception:  # noqa: BLE001 — szczegóły tylko w logach serwera
                    log.exception("Personal upload failed for user %s", user.pk)
                    upload_form.add_error(
                        None,
                        "Nie udało się przetworzyć pliku. Spróbuj ponownie później.",
                    )
        elif action == "share":
            share_form = ShareForm(request.POST)
            if share_form.is_valid():
                try:
                    doc = share_personal(
                        user, int(request.POST.get("doc_id", 0)), share_form
                    )
                    _saved_index_message(
                        request,
                        doc,
                        f"Zgoda i udostępnienie „{doc.title}” zapisane w bazie.",
                    )
                except Exception:  # noqa: BLE001
                    messages.error(request, "Nie udało się udostępnić materiału.")
            else:
                messages.error(request, "Udostępnienie wymaga akceptacji oświadczenia.")
            return redirect("rag:account")
        elif action == "unshare":
            try:
                doc = unshare_personal(user, int(request.POST.get("doc_id", 0)))
                _saved_index_message(
                    request,
                    doc,
                    f"Wycofanie zgody zapisane w bazie — „{doc.title}” jest materiałem osobistym.",
                )
            except Exception:  # noqa: BLE001
                messages.error(request, "Nie znaleziono udostępnionego materiału.")
            return redirect("rag:account")
        elif action == "retry_index":
            try:
                doc_id = int(request.POST.get("doc_id", ""))
                if not 0 < doc_id <= 2**63 - 1:
                    raise ValueError
            except ValueError:
                return JsonResponse({"detail": "Niepoprawny doc_id"}, status=400)
            # Check ownership before calling a service, even for a superuser.
            # The service repeats the SQL check to handle concurrent mutations.
            get_object_or_404(user.documents, pk=doc_id)
            try:
                doc = retry_personal_index(user, doc_id)
                _saved_index_message(
                    request, doc, f"Materiał „{doc.title}” pozostaje zapisany w bazie."
                )
            except Exception:  # noqa: BLE001 — nie ujawniamy szczegółów backendu
                log.exception("Personal index retry failed for user %s", user.pk)
                messages.error(
                    request,
                    "Nie udało się ponowić indeksowania. Spróbuj ponownie później.",
                )
            return redirect("rag:account")
        elif action == "delete":
            try:
                delete_personal(user, int(request.POST.get("doc_id", 0)))
                messages.success(request, "Materiał usunięty.")
            except Exception:  # noqa: BLE001
                messages.error(request, "Nie znaleziono materiału.")
            return redirect("rag:account")
    docs = user.documents.order_by("-created")
    return render(
        request,
        "rag/account.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
            "upload_form": upload_form,
            "docs": docs,
            "access_levels": access_for_user(user),
            "limits": {
                "mb": settings.PERSONAL_MAX_MB,
                "docs": settings.PERSONAL_MAX_DOCS,
            },
            "share_form": ShareForm(),
            "consent_statement": CONSENT_STATEMENT,
        },
    )


@csrf_protect
@require_POST
def export_citations(request: HttpRequest) -> HttpResponse:
    """POST JSON {sources, answer, format: bib|ris} -> plik z cytowaniami źródeł odpowiedzi."""
    from rag.citations import build_entries, to_bibtex, to_ris

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError, RecursionError):
        return JsonResponse({"detail": "zły JSON"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "JSON musi być obiektem"}, status=400)
    fmt = payload.get("format", "bib")
    if not isinstance(fmt, str) or fmt.lower() not in ("bib", "ris"):
        return JsonResponse({"detail": "Niepoprawny format"}, status=400)
    fmt = fmt.lower()
    try:
        entries = build_entries(
            payload.get("sources", {}), payload.get("answer", ""), user=_user(request)
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    if not entries:
        return JsonResponse({"detail": "brak źródeł do eksportu"}, status=404)
    if fmt == "ris":
        body, ctype, name = (
            to_ris(entries),
            "application/x-research-info-systems",
            "scriptura.ris",
        )
    else:
        body, ctype, name = to_bibtex(entries), "application/x-bibtex", "scriptura.bib"
    resp = HttpResponse(body, content_type=f"{ctype}; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp
