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
from patristics.models import PatPassage, PatRef, PatWork
from rag.access import access_for_user, document_access_q


def _corpus_stats(levels: list[str], user_id: int | None = None) -> dict:
    # Cached objects would retain titles/authors after an ACL change or deletion.
    shared = Document.objects.filter(owner__isnull=True)
    visible = Document.objects.filter(document_access_q(levels, user_id))
    by_type = dict(
        visible.values_list("doc_type")
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
                filter=Q(documents__access="open"),
            ),
            n_visible=Count(
                "documents",
                filter=Q(documents__in=visible),
            ),
        )
        .filter(n_visible__gt=0)
        .order_by("-n_visible", "name")
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
        "docs_total": visible.count(),
        "docs_open": visible.filter(access="open").count(),
        "docs_hidden": hidden,
        "chunks_total": sum(visible.values_list("chunk_count", flat=True)),
        "type_rows": type_rows,
        "authors": list(authors),
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
