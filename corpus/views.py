"""Widoki warstwy tekstu. Każdy widok zwraca pełną stronę lub sam fragment
(gdy żądanie przyszło z HTMX — nagłówek HX-Request)."""

from dataclasses import asdict

from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse

from corpus.models import Work
from corpus.services import text as svc
from corpus.sigla import format_ref
from places.service import places_for_ranges
from rag.access import access_for_user

PAGE_SIZE = 50


def _template(request: HttpRequest, name: str) -> str:
    """Fragment dla HTMX, pełna strona dla zwykłego żądania."""
    if request.headers.get("HX-Request"):
        return f"corpus/partials/{name}.html"
    return f"corpus/{name}.html"


def _selected_works(request: HttpRequest) -> list[Work]:
    raw = request.GET.get("works", "")
    codes = [c.strip() for c in raw.split(",") if c.strip()] or list(svc.DEFAULT_WORKS)
    return svc.active_works(codes, access=access_for_user(request.user))


def index(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "corpus/index.html",
        {
            "works": svc.active_works(access=access_for_user(request.user)),
            "selected": list(svc.DEFAULT_WORKS),
        },
    )


def reference(request: HttpRequest) -> HttpResponse:
    """?q=Mk 1,1-8&works=WLC,SBLGNT,BG1632 (przycisk „Interlinia” dokłada widok=interlinia)"""
    if request.GET.get("widok") == "interlinia":
        return interlinear(request)
    q = request.GET.get("q", "").strip()
    works = _selected_works(request)
    access = access_for_user(request.user)
    ctx = {"q": q, "works": works, "all_works": svc.active_works(access=access)}
    if not q:
        ctx["error"] = "Podaj siglum, np. Mk 1,1-8 albo Rdz 1."
        return render(request, _template(request, "reference"), ctx)
    try:
        refs = svc.resolve(q)
    except ValueError as exc:
        ctx["error"] = str(exc)
        return render(request, _template(request, "reference"), ctx, status=400)

    rows = svc.parallel(refs, works, access=access)
    from corpus.sigla import ordinal_range

    related = svc.related_verses(
        [ordinal_range(r) for r in refs], works, limit=10, access=access
    )
    ctx.update(
        {
            "refs": [format_ref(r) for r in refs],
            "rows": rows,
            "selected": [w.code for w in works],
            "related": related,
            "places": [
                asdict(p)
                for p in places_for_ranges([ordinal_range(r) for r in refs], limit=20)
            ],
        }
    )
    return render(request, _template(request, "reference"), ctx)


def _page_number(request: HttpRequest) -> int:
    try:
        page = int(request.GET.get("page", "1"))
    except (ValueError, TypeError):
        raise ValueError("Niepoprawny numer strony") from None
    if not 1 <= page <= 1_000_000:
        raise ValueError("Niepoprawny numer strony")
    return page


def concordance(request: HttpRequest) -> HttpResponse:
    """?lemma=λόγος | ?strong=H430 | ?form=בראשית ; &page=N"""
    lemma = request.GET.get("lemma", "").strip()
    strong = request.GET.get("strong", "").strip()
    form = request.GET.get("form", "").strip()
    try:
        page_number = _page_number(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    result = svc.concordance(
        lemma=lemma, strong=strong, form=form, access=access_for_user(request.user)
    )
    from corpus.models import Lexeme

    lexeme = None
    if strong:
        lexeme = Lexeme.objects.filter(strong=strong).first()
    elif lemma:
        from corpus.normalize import normalize

        lexeme = Lexeme.objects.filter(
            lemma_norm=normalize(
                lemma, "grc" if any("\u0370" <= c <= "\u03ff" for c in lemma) else "hbo"
            )
        ).first()

    paginator = Paginator(result.tokens, PAGE_SIZE)
    page = paginator.get_page(page_number)
    ctx = {
        "result": result,
        "page": page,
        "page_works": list(
            {t.verse_text.work.code: t.verse_text.work for t in page}.values()
        ),
        "params": {"lemma": lemma, "strong": strong, "form": form},
        "lexeme": lexeme,
    }
    return render(request, _template(request, "concordance"), ctx)


def search(request: HttpRequest) -> HttpResponse:
    """?q=słowa "fraza" -wykluczone H430 lemma:λόγος&works=..."""
    q = request.GET.get("q", "").strip()
    works = _selected_works(request)
    access = access_for_user(request.user)
    try:
        page = _page_number(request)
        result = svc.lexical(
            q, works, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, access=access
        )
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    ctx = {
        "q": q,
        "works": works,
        "all_works": svc.active_works(access=access),
        "selected": [w.code for w in works],
        "result": result,
        "page": page,
        "pages": max((result.total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
        "backend": settings.SEARCH_BACKEND,
    }
    return render(request, _template(request, "search"), ctx)


def interlinear(request: HttpRequest) -> HttpResponse:
    """?q=Flp 2,5-11[&tekst=LXX] — interlinia: oryginał, Strong, transliteracja, glosa, morfologia."""
    from corpus.services import interlinear as ilsvc

    q = request.GET.get("q", "").strip() or "J 1,1-18"
    access = access_for_user(request.user)
    ctx = {"q": q, "selected": list(svc.DEFAULT_WORKS)}
    try:
        refs = svc.resolve(q)
    except ValueError as exc:
        ctx["error"] = str(exc)
        return render(request, _template(request, "interlinear"), ctx, status=400)
    data = ilsvc.build(
        refs,
        request.GET.get("tekst", ""),
        access=access,
        translation=request.GET.get("pl", ""),
    )
    ctx.update(
        {
            "refs": [format_ref(r) for r in refs],
            "il": data,
            "model": settings.INTERLINEAR_MODEL.removesuffix(":cloud"),
        }
    )
    return render(request, _template(request, "interlinear"), ctx)


def lexicon_search(request: HttpRequest) -> HttpResponse:
    """?q=G725 | ἁρπαγμός | harpagmos | grabież — jedno trafienie: przekierowanie na hasło."""
    from corpus.services import lexicon as lexsvc

    q = request.GET.get("q", "").strip()[:100]
    results = lexsvc.search(q) if q else []
    if len(results) == 1 and not request.headers.get("HX-Request"):
        return redirect("corpus:lexicon", strong=results[0].strong)
    ctx = {"q": q, "results": results, "selected": list(svc.DEFAULT_WORKS)}
    return render(request, _template(request, "lexicon_search"), ctx)


def lexicon(request: HttpRequest, strong: str) -> HttpResponse:
    """Hasło słownika: STEP + Strong + polskie odpowiedniki + użycie w tekście i literaturze."""
    from corpus.services import lexicon as lexsvc

    lex = lexsvc.find_lexeme(strong)
    if lex is None:
        raise Http404("Nie ma takiego hasła")
    if lex.strong != strong:
        return redirect(reverse("corpus:lexicon", args=[lex.strong]))
    try:
        page_number = _page_number(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    access = access_for_user(request.user)
    entry = lexsvc.build_entry(
        lex,
        access,
        page_number,
        literature_access=access,
        user_id=request.user.id if request.user.is_authenticated else None,
        with_literature=page_number == 1,
    )
    ctx = {"entry": entry, "lex": lex, "selected": list(svc.DEFAULT_WORKS)}
    return render(request, _template(request, "lexicon"), ctx)
