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
    if not isinstance(text, str) or len(text) > 4096:
        raise ReferenceError("Niepoprawne lub za długie siglum")
    try:
        refs = parse(text)
    except ValueError:
        raise ReferenceError("Niepoprawne siglum") from None
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


def _work_levels(access: list[str] | None) -> list[str]:
    # Work has no owner: personal can never be granted by a group or access list.
    return [a for a in (["open"] if access is None else access) if a != "personal"]


def active_works(
    codes: list[str] | None = None, *, access: list[str] | None = None
) -> list[Work]:
    """SQL-authorized works, originals first. None codes = all; [] = none.

    `access` must come from rag.access.access_for_user(user), not request JSON.
    Omitted access is open-only; [] grants nothing. Selection of inaccessible or
    unknown codes returns no works, never falls back to the full corpus.
    """
    qs = Work.objects.filter(access__in=_work_levels(access))
    if codes is not None:
        qs = qs.filter(code__in=codes)
    return sorted(qs, key=lambda w: (w.kind != "original", w.code))


def visible_works(works: list[Work], *, access: list[str] | None = None) -> list[Work]:
    """Reauthorize supplied (possibly stale) Work objects in SQL; preserve order."""
    current = {
        w.pk: w
        for w in Work.objects.filter(
            pk__in=[w.pk for w in works], access__in=_work_levels(access)
        )
    }
    return [current[w.pk] for w in works if w.pk in current]


def parallel(
    refs: list[ParsedRef], works: list[Work], *, access: list[str] | None = None
) -> list[VerseRow]:
    """Wersety z zakresów sigli, każdy z tekstami w uprawnionych dziełach."""
    if not refs:
        return []
    works = visible_works(works, access=access)
    q = Q()
    for ref in refs:
        start, end = ordinal_range(ref)
        q |= Q(ordinal__range=(start, end))
    verses = Verse.objects.filter(q).select_related("book").order_by("ordinal")

    tokens_qs = Token.objects.order_by("position")
    texts = (
        VerseText.objects.filter(
            verse__in=verses, work__in=works, work__access__in=_work_levels(access)
        )
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
    lemma: str = "",
    strong: str = "",
    form: str = "",
    language: str = "",
    *,
    access: list[str] | None = None,
) -> Concordance:
    qs = Token.objects.filter(
        verse_text__work__access__in=_work_levels(access)
    ).select_related("verse_text__verse__book", "verse_text__work")
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
    q: str,
    works: list[Work],
    limit: int = 200,
    offset: int = 0,
    *,
    access: list[str] | None = None,
) -> LexicalResult:
    if not isinstance(q, str) or len(q) > 4096:
        raise ValueError("Niepoprawne lub za długie zapytanie")
    lq = parse_query(q)
    if any(not 0 <= gap <= 1000 for _, _, gap in lq.near):
        raise ValueError("NEAR wymaga odległości od 0 do 1000")
    works = visible_works(works, access=access)
    if not works:
        return LexicalResult(total=0)
    if lq.is_empty:
        return LexicalResult(total=0)
    if settings.SEARCH_BACKEND == "opensearch":
        return _lexical_opensearch(lq, works, limit, offset, access=access)
    return _lexical_db(lq, works, limit, offset, access=access)


def _lexical_opensearch(
    lq: LexicalQuery,
    works: list[Work],
    limit: int,
    offset: int,
    *,
    access: list[str] | None = None,
) -> LexicalResult:
    from corpus.search.client import get_client, index_name

    body = lexical_body(lq, [w.code for w in works], size=limit, from_=offset)
    resp = get_client().search(index=index_name("verses"), body=body)
    result = parse_response(resp)
    current = visible_works(works, access=access)
    codes = {w.code for w in current}
    if (
        codes != {w.code for w in works}
        or any(h.work not in codes for h in result.hits)
        or any(r["code"] not in codes for r in result.by_work)
    ):
        # ACL changed during search: don't return stale text or aggregations.
        if not current:
            return LexicalResult(total=0)
        return _lexical_db(lq, current, limit, offset, access=access)
    return result


def _lexical_db(
    lq: LexicalQuery,
    works: list[Work],
    limit: int,
    offset: int,
    *,
    access: list[str] | None = None,
) -> LexicalResult:
    """Fallback bez OpenSearch: Strong/lemat po tokenach, słowa po text_norm.
    NEAR degraduje do AND; frazy = dokładne podciągi znormalizowanego tekstu."""
    base = VerseText.objects.filter(
        work__in=works, work__access__in=_work_levels(access)
    ).select_related("verse__book", "work")

    if lq.strongs or lq.lemmas:
        tok = Q()
        for s in lq.strongs:
            tok |= Q(strong=s)
        for lemma in lq.lemmas:
            tok |= Q(lemma_norm=normalize(lemma, _guess_language(lemma)))
        verse_ids = Token.objects.filter(
            tok, verse_text__work__access__in=_work_levels(access)
        ).values("verse_text__verse_id")
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
    ranges: list[tuple[int, int]],
    works: list[Work] | None = None,
    limit: int = 8,
    *,
    access: list[str] | None = None,
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
    work_objs = (
        active_works(access=access)
        if works is None
        else visible_works(works, access=access)
    )
    texts = {
        (vt.verse_id, vt.work.code): vt.text
        for vt in VerseText.objects.filter(
            verse__in=verses.values(),
            work__in=work_objs,
            work__access__in=_work_levels(access),
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
