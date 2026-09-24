"""Endpointy czatu: GUI, strumień SSE /ask/stream, historia rozmów, logowanie."""

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from corpus.services import text as corpus_svc
from library.models import Author
from rag import service
from rag.access import access_for_user, allowed_modes
from rag.models import Conversation, Message
from rag.personal import (
    CONSENT_STATEMENT,
    ProfileForm,
    ShareForm,
    UploadForm,
    delete_personal,
    ingest_personal,
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
            "authors": Author.objects.all(),
            "works": corpus_svc.active_works(),
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
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR", "")
    ) or "?"


def _rate_ok(request: HttpRequest, user) -> bool:
    """Licznik pytań na godzinę w cache: per konto (zalogowani) albo per IP (anonimowi)."""
    from django.core.cache import cache

    if user is not None:
        key, limit = f"rate:user:{user.id}", settings.RATE_LIMIT_USER
    else:
        key, limit = f"rate:ip:{_client_ip(request)}", settings.RATE_LIMIT_ANON
    if limit <= 0:
        return True
    try:
        count = cache.get(key, 0)
        if count >= limit:
            return False
        cache.set(key, count + 1, timeout=3600)
    except Exception:  # noqa: BLE001 — awaria cache nie blokuje serwisu
        return True
    return True


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@csrf_exempt
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
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Niepoprawny JSON"}, status=400)
    question = (payload.get("question") or "").strip()
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
    authors = [a for a in (payload.get("authors") or []) if a]
    works = [w for w in (payload.get("works") or []) if w]
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
            works=works or None,
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


@login_required
def account(request: HttpRequest) -> HttpResponse:
    """Konto: dane, zmiana hasła, materiały osobiste (wgrywanie / usuwanie)."""
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
                    messages.success(
                        request, f"Dodano „{doc.title}” ({doc.chunk_count} fragmentów)."
                    )
                    return redirect("rag:account")
                except ValidationError as exc:
                    upload_form.add_error(None, exc)
                except Exception as exc:  # noqa: BLE001 — błąd parsowania/indeksu pokazujemy użytkownikowi
                    upload_form.add_error(
                        None, f"Nie udało się przetworzyć pliku: {exc}"
                    )
        elif action == "share":
            share_form = ShareForm(request.POST)
            if share_form.is_valid():
                try:
                    doc = share_personal(
                        user, int(request.POST.get("doc_id", 0)), share_form
                    )
                    messages.success(
                        request,
                        f"„{doc.title}” udostępnione we wspólnym korpusie (ze zgodą).",
                    )
                except Exception:  # noqa: BLE001
                    messages.error(request, "Nie udało się udostępnić materiału.")
            else:
                messages.error(request, "Udostępnienie wymaga akceptacji oświadczenia.")
            return redirect("rag:account")
        elif action == "unshare":
            try:
                doc = unshare_personal(user, int(request.POST.get("doc_id", 0)))
                messages.success(
                    request,
                    f"Zgoda wycofana — „{doc.title}” wróciło do materiałów osobistych.",
                )
            except Exception:  # noqa: BLE001
                messages.error(request, "Nie znaleziono udostępnionego materiału.")
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


@csrf_exempt
@require_POST
def export_citations(request: HttpRequest) -> HttpResponse:
    """POST JSON {sources, answer, format: bib|ris} -> plik z cytowaniami źródeł odpowiedzi."""
    from rag.citations import build_entries, to_bibtex, to_ris

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "zły JSON"}, status=400)
    fmt = (payload.get("format") or "bib").lower()
    entries = build_entries(payload.get("sources") or {}, payload.get("answer") or "")
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
