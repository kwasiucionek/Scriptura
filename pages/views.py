"""Podstrony informacyjne: o projekcie, korpus (żywe liczby), dla autorów, autor.

Statystyki korpusu liczone z bieżącego SQL (bez cache metadanych); anonim widzi
tylko materiały `open`, zalogowany z rozszerzonym dostępem — także swoje poziomy.
Autorzy materiałów bez zgody nie są wymieniani z nazwiska — tylko liczba zbiorcza.
"""

from django.conf import settings
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ane.models import AneLine, AneText
from corpus.models import Lexeme, Token, Verse, VerseLink
from corpus.services.text import active_works
from library.models import Author, DocType, Document
from library.venues import journal_rows
from patristics.models import PatPassage, PatRef, PatWork
from places.models import Place, PlaceLocation, PlaceRef
from rag.access import access_for_user, document_access_q

# data wejścia w życie polityki prywatności i regulaminu (zmiana treści = nowa data)
LEGAL_VERSION = "20 października 2026"


def _church_form(d: Document) -> str:
    """Postać dokumentu Kościoła w korpusie — do kolumny „Tekst” na stronie Korpus."""
    if "przekład roboczy" in d.title:
        return "pełny przekład roboczy"
    if d.language.startswith("pl"):
        return "tekst polski"
    adjective = {"en": "angielski", "it": "włoski", "la": "łaciński", "de": "niemiecki"}
    return f"tekst {adjective.get(d.language[:2], d.language)} + fragmenty przekładu roboczego"


def _corpus_stats(levels: list[str], user_id: int | None = None) -> dict:
    # Cached objects would retain titles/authors after an ACL change or deletion.
    shared = Document.objects.filter(owner__isnull=True)
    visible = Document.objects.filter(document_access_q(levels, user_id))
    # dokumenty Kościoła mają osobną sekcję — nie mieszają się z literaturą biblistów
    literature = visible.exclude(doc_type=DocType.CHURCH)
    church = list(
        visible.filter(doc_type=DocType.CHURCH)
        .prefetch_related("authors")
        .order_by("year", "title")
    )
    by_type = dict(
        literature.values_list("doc_type")
        .annotate(n=Count("id"))
        .values_list("doc_type", "n")
    )
    type_rows = [
        {"label": DocType(code).label, "n": by_type.get(code, 0)}
        for code in DocType.values
        if by_type.get(code)
    ]
    authors = (
        Author.objects.annotate(
            n_open=Count(
                "documents",
                filter=Q(documents__access="open")
                & ~Q(documents__doc_type=DocType.CHURCH),
            ),
            n_visible=Count(
                "documents",
                filter=Q(documents__in=literature),
            ),
        )
        .filter(n_visible__gt=0)
        .order_by("-n_visible", "name")
    )
    journals = journal_rows(
        list(
            literature.exclude(journal="")
            .values("journal")
            .annotate(n=Count("id"))
            .order_by("-n", "journal")
        )
    )
    hidden = shared.exclude(
        access__in=levels
    ).count()  # oczekujące na zgodę / demo prywatne
    recent = list(visible.prefetch_related("authors").order_by("-created")[:6])

    works = active_works(access=levels)
    pat_volumes = (
        PatWork.objects.values("series", "volume")
        .annotate(n_works=Count("id"), n_passages=Count("passages", distinct=True))
        .order_by("series", "volume")
    )
    pat_works = list(PatWork.objects.order_by("series", "volume", "author", "title"))
    ane_texts = list(
        AneText.objects.annotate(n_lines=Count("chapters__lines")).order_by("name")
    )

    data = {
        "docs_total": literature.count(),
        "docs_open": literature.filter(access="open").count(),
        "docs_hidden": hidden,
        "chunks_total": sum(literature.values_list("chunk_count", flat=True)),
        "type_rows": type_rows,
        "authors": list(authors),
        "journals": journals,
        "church": [{"doc": d, "form": _church_form(d)} for d in church],
        "church_chunks": sum(d.chunk_count for d in church),
        "recent": recent,
        "works": works,
        "verses": Verse.objects.count(),
        "tokens": Token.objects.filter(verse_text__work__in=works).count(),
        "lexemes": Lexeme.objects.count(),
        "lexemes_hbo": Lexeme.objects.filter(language__in=["hbo", "arc"]).count(),
        "lexemes_grc": Lexeme.objects.filter(language="grc").count(),
        "links": VerseLink.objects.count(),
        "pat_volumes": list(pat_volumes),
        "pat_works": pat_works,
        "pat_works_n": len(pat_works),
        "pat_passages": PatPassage.objects.count(),
        "pat_refs": PatRef.objects.count(),
        "pat_translated": PatPassage.objects.exclude(text_pl="").count(),
        "ane_texts": ane_texts,
        "ane_lines": AneLine.objects.count(),
        "places": Place.objects.filter(locations__isnull=False).distinct().count(),
        "places_pl": Place.objects.exclude(name_pl="").count(),
        "place_locations": PlaceLocation.objects.count(),
        "place_refs": PlaceRef.objects.count(),
    }
    return data


def about(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/about.html", {"active": "about"})


def guide(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "pages/guide.html",
        {
            "active": "guide",
            "rate_anon": settings.RATE_LIMIT_ANON,
            "rate_user": settings.RATE_LIMIT_USER,
            "question_max": settings.QUESTION_MAX_CHARS,
            "allow_anonymous": settings.ALLOW_ANONYMOUS,
            "anon_popular_only": settings.ANONYMOUS_POPULAR_ONLY,
            "works": active_works(access=access_for_user(request.user)),
        },
    )


def corpus(request: HttpRequest) -> HttpResponse:
    levels = access_for_user(request.user if request.user.is_authenticated else None)
    return render(
        request,
        "pages/corpus.html",
        {
            "active": "corpus",
            "levels": levels,
            "extended": levels != ["open"],
            "s": _corpus_stats(
                levels, request.user.pk if request.user.is_authenticated else None
            ),
        },
    )


def for_authors(request: HttpRequest) -> HttpResponse:
    from rag.personal import CONSENT_STATEMENT

    return render(
        request,
        "pages/for_authors.html",
        {
            "active": "authors",
            "consent_statement": CONSENT_STATEMENT,
            "limits": {
                "docs": settings.PERSONAL_MAX_DOCS,
                "mb": settings.PERSONAL_MAX_MB,
            },
        },
    )


def author(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/author.html", {"active": "author"})


def _legal_context() -> dict:
    """Wspólne dane polityki prywatności i regulaminu: wersja, limity pytań, model."""
    return {
        "legal_version": LEGAL_VERSION,
        "rate_anon": settings.RATE_LIMIT_ANON,
        "rate_user": settings.RATE_LIMIT_USER,
        "chat_model": settings.OLLAMA_CHAT_MODEL,
        "limits": {"docs": settings.PERSONAL_MAX_DOCS, "mb": settings.PERSONAL_MAX_MB},
    }


def privacy(request: HttpRequest) -> HttpResponse:
    return render(
        request, "pages/privacy.html", {"active": "privacy", **_legal_context()}
    )


def terms(request: HttpRequest) -> HttpResponse:
    return render(request, "pages/terms.html", {"active": "terms", **_legal_context()})
