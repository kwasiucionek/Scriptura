"""Widoki warstwy tekstu. Każdy widok zwraca pełną stronę lub sam fragment
(gdy żądanie przyszło z HTMX — nagłówek HX-Request)."""

from django.conf import settings
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from corpus.models import Work
from corpus.services import text as svc
from corpus.sigla import format_ref

PAGE_SIZE = 50


def _template(request: HttpRequest, name: str) -> str:
    """Fragment dla HTMX, pełna strona dla zwykłego żądania."""
    if request.headers.get("HX-Request"):
        return f"corpus/partials/{name}.html"
    return f"corpus/{name}.html"


def _selected_works(request: HttpRequest) -> list[Work]:
    raw = request.GET.get("works", "")
    codes = [c for c in raw.split(",") if c] or list(svc.DEFAULT_WORKS)
    works = svc.active_works(codes)
    return works or svc.active_works()


def index(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "corpus/index.html",
        {"works": svc.active_works(), "selected": list(svc.DEFAULT_WORKS)},
    )


def reference(request: HttpRequest) -> HttpResponse:
    """?q=Mk 1,1-8&works=WLC,SBLGNT,BG1632"""
    q = request.GET.get("q", "").strip()
    works = _selected_works(request)
    ctx = {"q": q, "works": works, "all_works": svc.active_works()}
    if not q:
        ctx["error"] = "Podaj siglum, np. Mk 1,1-8 albo Rdz 1."
        return render(request, _template(request, "reference"), ctx)
    try:
        refs = svc.resolve(q)
    except svc.ReferenceError as exc:
        ctx["error"] = str(exc)
        return render(request, _template(request, "reference"), ctx)

    rows = svc.parallel(refs, works)
    from corpus.sigla import ordinal_range

    related = svc.related_verses([ordinal_range(r) for r in refs], works, limit=10)
    ctx.update(
        {
            "refs": [format_ref(r) for r in refs],
            "rows": rows,
            "selected": [w.code for w in works],
            "related": related,
        }
    )
    return render(request, _template(request, "reference"), ctx)


def concordance(request: HttpRequest) -> HttpResponse:
    """?lemma=λόγος | ?strong=H430 | ?form=בראשית ; &page=N"""
    lemma = request.GET.get("lemma", "").strip()
    strong = request.GET.get("strong", "").strip()
    form = request.GET.get("form", "").strip()
    result = svc.concordance(lemma=lemma, strong=strong, form=form)
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
    page = paginator.get_page(request.GET.get("page", 1))
    ctx = {
        "result": result,
        "page": page,
        "params": {"lemma": lemma, "strong": strong, "form": form},
        "lexeme": lexeme,
    }
    return render(request, _template(request, "concordance"), ctx)


def search(request: HttpRequest) -> HttpResponse:
    """?q=słowa "fraza" -wykluczone H430 lemma:λόγος&works=..."""
    q = request.GET.get("q", "").strip()
    works = _selected_works(request)
    page = max(int(request.GET.get("page", 1) or 1), 1)
    result = svc.lexical(q, works, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
    ctx = {
        "q": q,
        "works": works,
        "all_works": svc.active_works(),
        "selected": [w.code for w in works],
        "result": result,
        "page": page,
        "pages": max((result.total + PAGE_SIZE - 1) // PAGE_SIZE, 1),
        "backend": settings.SEARCH_BACKEND,
    }
    return render(request, _template(request, "search"), ctx)
