"""Warstwa serwisowa warstwy tekstu — logika zapytań poza widokami.

parallel()     siglum -> wersety × dzieła (z tokenami dla oryginałów)
concordance()  lemat / Strong / forma -> wystąpienia + rozkład po księgach
lexical()      wyszukiwanie słów (OpenSearch; fallback: Postgres FTS / SQLite LIKE)
"""

import re
from dataclasses import dataclass, field
from html import escape

from django.conf import settings
from django.db import connection
from django.db.models import Count, Prefetch, Q, QuerySet
from django.utils.safestring import mark_safe

from corpus.books import ParsedRef
from corpus.models import Token, Verse, VerseLink, VerseText, Work
from corpus.normalize import normalize
from corpus.search.grammar import LexicalQuery, parse_query
from corpus.search.queries import Hit, LexicalResult, lexical_body, parse_response
from corpus.sigla import ordinal_range, parse

DEFAULT_WORKS = ("WLC", "LXX", "SBLGNT", "BG1632")


class ReferenceError(ValueError):
    """Nie udało się rozpoznać siglum."""


def resolve(text: str) -> list[ParsedRef]:
    refs = parse(text)
    if not refs:
        raise ReferenceError(f"Nie rozpoznano siglum: {text!r}")
    return refs


# --------------------------------------------------------------------------
# Widok równoległy
# --------------------------------------------------------------------------


@dataclass
class VerseRow:
    verse: Verse
    texts: dict[str, VerseText] = field(default_factory=dict)  # work.code -> tekst


def active_works(codes: list[str] | None = None) -> list[Work]:
    """Dzieła do pokazania, w kolejności: oryginały, potem przekłady."""
    qs = Work.objects.all()
    if codes:
        qs = qs.filter(code__in=codes)
    return sorted(qs, key=lambda w: (w.kind != "original", w.code))


def parallel(refs: list[ParsedRef], works: list[Work]) -> list[VerseRow]:
    """Wersety z zakresów sigli, każdy z tekstami we wskazanych dziełach."""
    q = Q()
    for ref in refs:
        start, end = ordinal_range(ref)
        q |= Q(ordinal__range=(start, end))
    verses = Verse.objects.filter(q).select_related("book").order_by("ordinal")

    tokens_qs = Token.objects.order_by("position")
    texts = (
        VerseText.objects.filter(verse__in=verses, work__in=works)
        .select_related("work")
        .prefetch_related(Prefetch("tokens", queryset=tokens_qs))
    )
    by_verse: dict[int, dict[str, VerseText]] = {}
    for vt in texts:
        by_verse.setdefault(vt.verse_id, {})[vt.work.code] = vt

    return [VerseRow(verse=v, texts=by_verse.get(v.id, {})) for v in verses]


# --------------------------------------------------------------------------
# Konkordancja
# --------------------------------------------------------------------------


@dataclass
class Concordance:
    label: str
    total: int
    by_book: list[dict]  # [{"abbr": "Mk", "n": 12}, ...]
    tokens: QuerySet


def concordance(
    lemma: str = "", strong: str = "", form: str = "", language: str = ""
) -> Concordance:
    qs = Token.objects.select_related("verse_text__verse__book", "verse_text__work")
    if lemma:
        norm = normalize(lemma, language or _guess_language(lemma))
        qs = qs.filter(lemma_norm=norm)
        label = lemma
    elif strong:
        qs = qs.filter(strong=strong.upper())
        label = strong.upper()
    elif form:
        norm = normalize(form, language or _guess_language(form))
        qs = qs.filter(surface_norm=norm)
        label = form
    else:
        return Concordance("", 0, [], Token.objects.none())

    by_book = [
        {
            "abbr": r["verse_text__verse__book__abbr"],
            "order": r["verse_text__verse__book__order"],
            "n": r["n"],
        }
        for r in qs.values(
            "verse_text__verse__book__abbr", "verse_text__verse__book__order"
        )
        .annotate(n=Count("id"))
        .order_by("verse_text__verse__book__order")
    ]
    total = sum(b["n"] for b in by_book)
    return Concordance(
        label, total, by_book, qs.order_by("verse_text__verse__ordinal", "position")
    )


def _guess_language(text: str) -> str:
    if re.search(r"[\u0590-\u05ff]", text):
        return "hbo"
    if re.search(r"[\u0370-\u03ff\u1f00-\u1fff]", text):
        return "grc"
    return "pl"


# --------------------------------------------------------------------------
# Wyszukiwanie leksykalne — backend wg settings.SEARCH_BACKEND
# --------------------------------------------------------------------------


def lexical(
    q: str, works: list[Work], limit: int = 200, offset: int = 0
) -> LexicalResult:
    lq = parse_query(q)
    if lq.is_empty:
        return LexicalResult(total=0)
    if settings.SEARCH_BACKEND == "opensearch":
        return _lexical_opensearch(lq, works, limit, offset)
    return _lexical_db(lq, works, limit, offset)


def _lexical_opensearch(
    lq: LexicalQuery, works: list[Work], limit: int, offset: int
) -> LexicalResult:
    from corpus.search.client import get_client, index_name

    body = lexical_body(lq, [w.code for w in works], size=limit, from_=offset)
    resp = get_client().search(index=index_name("verses"), body=body)
    return parse_response(resp)


def _lexical_db(
    lq: LexicalQuery, works: list[Work], limit: int, offset: int
) -> LexicalResult:
    """Fallback bez OpenSearch: Strong/lemat po tokenach, słowa po text_norm.
    NEAR degraduje do AND; frazy = dokładne podciągi znormalizowanego tekstu."""
    base = VerseText.objects.filter(work__in=works).select_related(
        "verse__book", "work"
    )

    if lq.strongs or lq.lemmas:
        tok = Q()
        for s in lq.strongs:
            tok |= Q(strong=s)
        for lemma in lq.lemmas:
            tok |= Q(lemma_norm=normalize(lemma, _guess_language(lemma)))
        verse_ids = Token.objects.filter(tok).values("verse_text__verse_id")
        base = base.filter(verse_id__in=verse_ids)

    words = list(lq.words) + [w for pair in lq.near for w in pair[:2]]
    if lq.has_text:
        per_work = Q()
        for work in works:
            cond = Q(work=work) & _text_condition(
                [normalize(w, work.language) for w in words],
                [normalize(p, work.language) for p in lq.phrases],
                [normalize(x, work.language) for x in lq.excluded],
            )
            per_work |= cond
        base = base.filter(per_work)

    base = base.order_by("verse__ordinal", "work__code")
    total = base.count()
    page = list(base[offset : offset + limit])
    highlight_words = words + lq.phrases
    hits = [
        Hit(
            ref=str(vt.verse),
            osis_id=vt.verse.osis_id,
            ordinal=vt.verse.ordinal,
            work=vt.work.code,
            language=vt.work.language,
            text=vt.text,
            html=_mark(vt.text, highlight_words),
        )
        for vt in page
    ]
    by_book_raw = (
        base.values("verse__book__abbr", "verse__book__order")
        .annotate(n=Count("id"))
        .order_by("verse__book__order")
    )
    by_book = [{"abbr": r["verse__book__abbr"], "n": r["n"]} for r in by_book_raw]
    by_work = [
        {"code": r["work__code"], "n": r["n"]}
        for r in base.values("work__code")
        .annotate(n=Count("id"))
        .order_by("work__code")
    ]
    return LexicalResult(total=total, hits=hits, by_book=by_book, by_work=by_work)


def _mark(text: str, words: list[str]) -> str:
    """Podświetlenie po prostym dopasowaniu bez uwzględniania fleksji (fallback)."""
    html = escape(text)
    for w in words:
        if w:
            html = re.sub(f"(?i)({re.escape(escape(w))})", r"<mark>\1</mark>", html)
    return mark_safe(html)


def _text_condition(words: list[str], phrases: list[str], excluded: list[str]) -> Q:
    """Postgres: to_tsquery('simple') na text_norm; SQLite/inne: LIKE."""
    if connection.vendor == "postgresql":
        from django.contrib.postgres.search import SearchQuery

        cond = Q()
        for w in words:
            if w:
                cond &= Q(
                    text_norm__search=SearchQuery(
                        w, config="simple", search_type="plain"
                    )
                )
        for p in phrases:
            if p:
                cond &= Q(
                    text_norm__search=SearchQuery(
                        p, config="simple", search_type="phrase"
                    )
                )
        for x in excluded:
            if x:
                cond &= ~Q(
                    text_norm__search=SearchQuery(
                        x, config="simple", search_type="plain"
                    )
                )
        return cond

    cond = Q()
    for w in words:
        if w:
            cond &= Q(text_norm__contains=w)
    for p in phrases:
        if p:
            cond &= Q(text_norm__contains=p)
    for x in excluded:
        if x:
            cond &= ~Q(text_norm__contains=x)
    return cond


@dataclass
class RelatedVerse:
    ref: str
    votes: int
    text: str = ""  # brzmienie w pierwszym dostępnym dziele (do promptu / podglądu)
    work: str = ""


def related_verses(
    ranges: list[tuple[int, int]], works: list[Work] | None = None, limit: int = 8
) -> list[RelatedVerse]:
    """Powiązane wersety (OpenBible cross-references) dla zakresów ordinali, wg głosów.
    Zakres docelowy skracany do pierwszego wersetu (tak działa większość powiązań);
    powiązania wstecz do samego zakresu pytania są pomijane."""
    if not ranges:
        return []
    q = Q()
    for a, b in ranges:
        q |= Q(from_ordinal__gte=a, from_ordinal__lte=b)
    links = VerseLink.objects.filter(q).order_by("-votes")[: limit * 4]
    seen: set[int] = set()
    picked: list[VerseLink] = []
    for ln in links:
        if ln.to_start in seen or any(a <= ln.to_start <= b for a, b in ranges):
            continue
        seen.add(ln.to_start)
        picked.append(ln)
        if len(picked) >= limit:
            break
    if not picked:
        return []
    verses = {
        v.ordinal: v
        for v in Verse.objects.filter(
            ordinal__in=[p.to_start for p in picked]
        ).select_related("book")
    }
    work_objs = works or active_works()
    texts = {
        (vt.verse_id, vt.work.code): vt.text
        for vt in VerseText.objects.filter(
            verse__in=verses.values(), work__in=work_objs
        ).select_related("work")
    }
    out = []
    for p in picked:
        v = verses.get(p.to_start)
        if not v:
            continue
        text, code = "", ""
        for w in work_objs:
            if (v.id, w.code) in texts:
                text, code = texts[(v.id, w.code)], w.code
                break
        label = str(v) + (f"-{p.to_end % 1000}" if p.to_end > p.to_start else "")
        out.append(RelatedVerse(ref=label, votes=p.votes, text=text, work=code))
    return out
