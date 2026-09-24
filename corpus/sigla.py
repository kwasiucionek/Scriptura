"""Parser sigli biblijnych w polskiej konwencji (BT) z tolerancją na inne.

Obsługiwane formy:
    Mk 1,1-8          zakres wersetów
    Mk 1,1-2,5        zakres międzyrozdziałowy
    Mk 1,1.5.9-12     lista wersetów (kropki)
    J 3,16n / 3,16nn  werset następny / wersety następne
    Mk 1,1a           podział wersetu na części (litera ignorowana)
    Rdz 1–3           zakres rozdziałów (myślnik, półpauza, pauza)
    Ps 23             cały rozdział
    Mt 5,3-12; 7,21   kilka miejsc w tej samej księdze
    Mk 1:1-8          konwencja z dwukropkiem
    1 Kor 13 / 1Kor 13 / I Kor 13   numerowane księgi w różnych zapisach

Dwa wejścia:
    parse(text)   — tekst jest siglum (lub listą sigli); zwraca list[ParsedRef]
    extract(text) — wolny tekst (artykuł); zwraca list[SiglumMatch] z pozycjami

Zakres wersetów przeliczany jest na parę `ordinal` (patrz Verse.ordinal),
bez zaglądania do bazy: "cały rozdział" i "nn" kończą się na 999.
"""

import re
from dataclasses import dataclass

from corpus.books import (
    FREE_TEXT_EXCLUDE,
    BookSpec,
    ParsedRef,
    all_abbrs_sorted,
    lookup,
)

DASH = r"[-\u2013\u2014]"  # -, –, —
VERSE_CAP = 999  # górna granica numeru wersetu w ordinal
MAX_CHAPTER = 150  # Ps — numer powyżej to sklejony numer przypisu, nie siglum
MAX_VERSE = 176  # Ps 119

_VERSE_ITEM = re.compile(
    rf"""
    (?P<v1>\d+)[ab]?
    (?:\s*{DASH}\s*(?P<v2>\d+)(?:[,:](?P<v3>\d+))?[ab]?)?
    (?P<n>nn?)?
    """,
    re.VERBOSE,
)


def _book_regex(abbrs: list[str]) -> str:
    return "|".join(re.escape(a) for a in abbrs)


# Ogon numeryczny po skrócie księgi: "1,1-8; 2,3.5nn" itd.
_TAIL = (
    r"\d+[ab]?(?:nn?)?"
    r"(?:[,:.]\d+[ab]?(?:nn?)?"  # przecinek/dwukropek/kropka — bez spacji
    r"|\s*;\s*\d+[ab]?(?:nn?)?"  # średnik — spacje dozwolone
    r"|\s*" + DASH + r"\s*\d+[ab]?(?:nn?)?)*"
)

_ALL_ABBRS = all_abbrs_sorted()
_FREE_ABBRS = [a for a in _ALL_ABBRS if a not in FREE_TEXT_EXCLUDE]

_PARSE_RE = re.compile(
    rf"(?P<book>{_book_regex(_ALL_ABBRS)})\.?\s*(?P<tail>{_TAIL})",
    re.IGNORECASE,
)
_EXTRACT_RE = re.compile(
    rf"(?<![\w])(?P<book>{_book_regex(_FREE_ABBRS)})\.?\s+(?P<tail>{_TAIL})(?![\w])",
)


@dataclass
class SiglumMatch:
    refs: list[ParsedRef]
    start: int
    end: int
    text: str


def _split_segments(tail: str) -> list[str]:
    """'1,1-8; 2,3' -> ['1,1-8', '2,3']"""
    return [s.strip() for s in tail.split(";") if s.strip()]


def _parse_segment(book: BookSpec, segment: str, raw: str) -> list[ParsedRef]:
    """Jeden segment: 'rozdział' | 'rozdział,wersety' | 'rozdział-rozdział'."""
    segment = segment.replace("\u00a0", " ").strip()

    # Zakres rozdziałów bez wersetów: "1-3", "1–3"
    m = re.fullmatch(rf"(\d+)\s*{DASH}\s*(\d+)", segment)
    if m:
        c1, c2 = int(m.group(1)), int(m.group(2))
        if (
            c2 < c1 or c2 > MAX_CHAPTER
        ):  # odwrócony albo z przyklejonym numerem przypisu
            return []
        return [ParsedRef(book, c1, None, chapter_end=c2, verse_end=-1, raw=raw)]

    # Sam rozdział: "23"
    if re.fullmatch(r"\d+", segment):
        chapter = int(segment)
        return (
            [ParsedRef(book, chapter, None, raw=raw)] if chapter <= MAX_CHAPTER else []
        )

    # Rozdział + wersety: "1,1-8", "1:1.5.9-12", "1,1-2,5"
    m = re.match(r"(\d+)\s*[,:]\s*(.+)", segment)
    if not m:
        return []
    chapter = int(m.group(1))
    if chapter > MAX_CHAPTER:
        return []
    verses_part = m.group(2)
    refs: list[ParsedRef] = []
    pos = 0
    while pos < len(verses_part):
        vm = _VERSE_ITEM.match(verses_part, pos)
        if not vm or vm.end() == pos:
            pos += 1
            continue
        v1 = int(vm.group("v1"))
        v2 = vm.group("v2")
        v3 = vm.group("v3")
        suffix = vm.group("n")
        if v1 > MAX_VERSE or (v2 is not None and v3 is None and int(v2) > MAX_VERSE):
            pos = vm.end()  # przyklejony numer przypisu ("Ps 119,1776")
            continue
        if v3 is not None:
            # zakres międzyrozdziałowy: v1 -> rozdział v2, werset v3
            if int(v2) < chapter:  # odwrócony — to nie siglum
                pos = vm.end()
                continue
            refs.append(
                ParsedRef(
                    book, chapter, v1, chapter_end=int(v2), verse_end=int(v3), raw=raw
                )
            )
        elif v2 is not None:
            if int(v2) < v1:  # odwrócony zakres wersetów (np. numery stron w przypisie)
                pos = vm.end()
                continue
            refs.append(ParsedRef(book, chapter, v1, verse_end=int(v2), raw=raw))
        elif suffix == "nn":
            refs.append(ParsedRef(book, chapter, v1, verse_end=-1, raw=raw))
        elif suffix == "n":
            refs.append(ParsedRef(book, chapter, v1, verse_end=v1 + 1, raw=raw))
        else:
            refs.append(ParsedRef(book, chapter, v1, verse_end=v1, raw=raw))
        pos = vm.end()
        # przeskocz separator listy (kropka) lub śmieci
        while pos < len(verses_part) and verses_part[pos] in " .":
            pos += 1
    return refs


def _refs_from_match(book_str: str, tail: str, raw: str) -> list[ParsedRef]:
    book = lookup(book_str)
    if book is None:
        return []
    refs: list[ParsedRef] = []
    for seg in _split_segments(tail):
        refs.extend(_parse_segment(book, seg, raw))
    return refs


def parse(text: str) -> list[ParsedRef]:
    """Parsuj tekst będący siglum lub listą sigli ('Mk 1,1-8; Łk 3,1')."""
    text = text.replace("\u00a0", " ")
    refs: list[ParsedRef] = []
    for m in _PARSE_RE.finditer(text):
        refs.extend(
            _refs_from_match(m.group("book"), m.group("tail"), m.group(0).strip())
        )
    return refs


def extract(text: str) -> list[SiglumMatch]:
    """Znajdź sigla w wolnym tekście (artykuł, skrypt, transkrypcja)."""
    text = text.replace("\u00a0", " ")
    out: list[SiglumMatch] = []
    for m in _EXTRACT_RE.finditer(text):
        raw = m.group(0).strip()
        refs = _refs_from_match(m.group("book"), m.group("tail"), raw)
        if refs:
            out.append(SiglumMatch(refs=refs, start=m.start(), end=m.end(), text=raw))
    return out


def ordinal(book_order: int, chapter: int, verse: int) -> int:
    """Sortowalny klucz wersetu, zgodny z Verse.ordinal."""
    return book_order * 1_000_000 + chapter * 1000 + verse


def ordinal_range(ref: ParsedRef) -> tuple[int, int]:
    """Zakres [start, end] w ordinal; bez odwołania do bazy."""
    order = ref.book.order
    if ref.verse_start is None:
        # cały rozdział lub zakres rozdziałów
        end_ch = ref.chapter_end or ref.chapter
        return ordinal(order, ref.chapter, 0), ordinal(order, end_ch, VERSE_CAP)
    start = ordinal(order, ref.chapter, ref.verse_start)
    end_ch = ref.chapter_end or ref.chapter
    if ref.verse_end is None:
        end_v = ref.verse_start
    elif ref.verse_end == -1:
        end_v = VERSE_CAP
    else:
        end_v = ref.verse_end
    return start, ordinal(order, end_ch, end_v)


def format_ref(ref: ParsedRef) -> str:
    """Zapis w konwencji BT: 'Mk 1,1-8', 'Rdz 1-3', 'J 3,16nn'."""
    b = ref.book.abbr
    if ref.verse_start is None:
        if ref.chapter_end and ref.chapter_end != ref.chapter:
            return f"{b} {ref.chapter}-{ref.chapter_end}"
        return f"{b} {ref.chapter}"
    s = f"{b} {ref.chapter},{ref.verse_start}"
    if ref.chapter_end and ref.chapter_end != ref.chapter:
        return f"{s}-{ref.chapter_end},{ref.verse_end}"
    if ref.verse_end == -1:
        return f"{s}nn"
    if ref.verse_end and ref.verse_end != ref.verse_start:
        return f"{s}-{ref.verse_end}"
    return s
