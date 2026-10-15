"""Import przekładu z modułu MySword (`*.bbl.mybible`, SQLite: Bible(Book, Chapter, Verse, Scripture)).

Scripture to tekst ze znacznikami GBF i HTML. Rozpoznajemy:
  <RF q=[12]>…<Rf>   przypis tłumacza (etykieta 12)      <RF q=ⓐ>…<Rf>   odsyłacze (ⓐ)
  <TS>…<Ts>, <h>…</h> tytuł perykopy                    <CM>            początek akapitu
  <FR>…<Fr>          słowa Jezusa                        <FI>…<Fi>       słowa dodane (kursywa)
  <PI0><PF1>         początek wersu poezji               <a href='b19.51.12'>  odsyłacz do wersetu
Wynik: czysty tekst wersetu (wyszukiwanie, prompt), oczyszczony HTML do wyświetlania (markup)
i lista przypisów. HTML z modułu przechodzi przez białą listę znaczników — reszta to tekst.
Numeracja ksiąg MySword: 1–66 w porządku protestanckim (KJV); wersyfikacja jak w źródle.
"""

import html
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

from corpus.books import BY_OSIS

# MySword: numer księgi -> OSIS (kanon protestancki, kolejność KJV)
BOOK_NUMBERS = [
    "Gen", "Exod", "Lev", "Num", "Deut", "Josh", "Judg", "Ruth", "1Sam", "2Sam", "1Kgs",
    "2Kgs", "1Chr", "2Chr", "Ezra", "Neh", "Esth", "Job", "Ps", "Prov", "Eccl", "Song",
    "Isa", "Jer", "Lam", "Ezek", "Dan", "Hos", "Joel", "Amos", "Obad", "Jonah", "Mic", "Nah",
    "Hab", "Zeph", "Hag", "Zech", "Mal", "Matt", "Mark", "Luke", "John", "Acts", "Rom",
    "1Cor", "2Cor", "Gal", "Eph", "Phil", "Col", "1Thess", "2Thess", "1Tim", "2Tim", "Titus",
    "Phlm", "Heb", "Jas", "1Pet", "2Pet", "1John", "2John", "3John", "Jude", "Rev",
]  # fmt: skip

_NOTE_RE = re.compile(r"<RF(?:\s+q=([^>]*))?>(.*?)<Rf>", re.DOTALL)
_TITLE_RE = re.compile(r"<TS>(.*?)<Ts>|<h>(.*?)</h>", re.DOTALL | re.IGNORECASE)
_POETRY_RE = re.compile(r"(?:<P[IF]\d>)+")
_LINK_RE = re.compile(r"^b(\d+)\.(\d+)\.(\d+)(?:-(?:b(\d+)\.)?(\d+)(?:\.(\d+))?)?$")
_PLACEHOLDER = "\x00{}\x00"
_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")


def link_siglum(href: str) -> str:
    """'b19.51.12' -> 'Ps 51,12'; 'b19.104.2-32' -> 'Ps 104,2-32';
    'b10.15.13-b10.17.22' -> '2 Sm 15,13-17,22'; nieznany zapis -> ''."""
    m = _LINK_RE.match(href.strip())
    if not m:
        return ""
    book_no, ch, v, book2, a, b = m.groups()
    n = int(book_no)
    if not 1 <= n <= len(BOOK_NUMBERS):
        return ""
    spec = BY_OSIS.get(BOOK_NUMBERS[n - 1])
    if spec is None:
        return ""
    out = f"{spec.abbr} {int(ch)},{int(v)}"
    if a and b:  # koniec w innym rozdziale: b10.17.22 albo 17.22
        out += f"-{int(a)},{int(b)}"
    elif a:
        out += f"-{int(a)}"
    return out


class _Sanitizer(HTMLParser):
    """Biała lista znaczników; wszystko inne zostaje tekstem (escape)."""

    SIMPLE = {"em": "em", "i": "i", "b": "b", "strong": "b", "sup": "sup", "sub": "sub"}

    def __init__(self, ref_url: str):
        super().__init__(convert_charrefs=True)
        self.ref_url = ref_url
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag in self.SIMPLE:
            name = self.SIMPLE[tag]
            self.out.append(f"<{name}>")
        elif tag == "br":
            self.out.append("<br>")
            return
        elif tag == "span" and "tl" in cls:  # transliteracja
            name = "i"
            self.out.append('<i class="tl">')
        elif tag == "span" and "jesus" in cls:
            name = "span"
            self.out.append('<span class="jesus">')
        elif tag == "a" and a.get("href"):
            siglum = link_siglum(a["href"])
            if not siglum:
                self.stack.append("")
                return
            name = "a"
            href = f"{self.ref_url}?{urlencode({'q': siglum})}"
            self.out.append(
                f'<a class="ref" href="{html.escape(href)}" title="{html.escape(siglum)}">'
            )
        else:
            self.stack.append("")
            return
        self.stack.append(name)

    def handle_endtag(self, tag):
        if tag == "br" or not self.stack:
            return
        name = self.stack.pop()
        if name:
            self.out.append(f"</{name}>")

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:  # domknięcie niezamkniętych znaczników
            name = self.stack.pop()
            if name:
                self.out.append(f"</{name}>")
        return "".join(self.out)


def sanitize(fragment: str, ref_url: str = "/text/ref/") -> str:
    s = _Sanitizer(ref_url)
    s.feed(fragment)
    s.close()
    return s.result().strip()


def plain(fragment_html: str) -> str:
    text = re.sub(r"<br>", " ", fragment_html)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class ParsedNote:
    kind: str  # note | xref
    label: str
    html: str
    text: str


@dataclass
class ParsedVerse:
    osis: str
    chapter: int
    verse: int
    text: str  # czysty tekst bez przypisów i tytułów
    markup: str  # HTML do wyświetlania
    notes: list[ParsedNote] = field(default_factory=list)


def parse_scripture(
    raw: str, ref_url: str = "/text/ref/"
) -> tuple[str, str, list[ParsedNote]]:
    """(czysty tekst, markup, przypisy) z pola Scripture."""
    notes: list[ParsedNote] = []

    def take_note(m: re.Match) -> str:
        q = (m.group(1) or "").strip().strip("'\"")
        is_note = q.startswith("[") or not q
        label = q.strip("[]") or str(len(notes) + 1)
        body = sanitize(m.group(2), ref_url)
        notes.append(
            ParsedNote("note" if is_note else "xref", label[:16], body, plain(body))
        )
        return _PLACEHOLDER.format(len(notes) - 1)

    s = _NOTE_RE.sub(take_note, raw)
    titles: list[str] = []

    def take_title(m: re.Match) -> str:
        titles.append(plain(sanitize(m.group(1) or m.group(2) or "", ref_url)))
        return ""

    s = _TITLE_RE.sub(take_title, s)
    s = s.replace("<CM>", " ").replace("<cm>", " ")
    s = re.sub(r"<FR>", '<span class="jesus">', s)
    s = re.sub(r"<Fr>", "</span>", s)
    s = re.sub(r"<FI>", "<i>", s)
    s = re.sub(r"<Fi>", "</i>", s)
    s = s.strip()
    # wersy poezji: pierwszy znacznik na początku wersetu znika, kolejne = nowa linia
    s = _POETRY_RE.sub(lambda m: "" if not s[: m.start()].strip() else "<br>", s)
    body = sanitize(s, ref_url)
    text = plain(_PLACEHOLDER_RE.sub("", body))

    def marker(m: re.Match) -> str:
        n = notes[int(m.group(1))]
        cls = "fn" if n.kind == "note" else "xr"
        return f'<sup class="{cls}">{html.escape(n.label)}</sup>'

    markup = _PLACEHOLDER_RE.sub(marker, body)
    markup = re.sub(r"\s+", " ", markup).strip()
    if titles:
        heads = "".join(
            f'<span class="pericope">{html.escape(t)}</span>' for t in titles if t
        )
        markup = heads + markup
    return text, markup, notes


def details(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM Details").fetchone()
        return {k: row[k] for k in row.keys()} if row else {}
    finally:
        con.close()


def read_rows(path: Path) -> tuple[list[tuple[int, int, int, str]], list[str]]:
    """Wiersze modułu (księga, rozdział, werset, tekst) i lista usterek. Werset bez numeru
    dostaje jedyny brakujący numer w swoim rozdziale (jeśli luka jest dokładnie jedna)."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        raw = con.execute(
            "SELECT Book, Chapter, Verse, Scripture FROM Bible"
        ).fetchall()
    finally:
        con.close()
    issues: list[str] = []
    chapters: dict[tuple[int, int], set[int]] = {}
    for b, c, v, _ in raw:
        if b is not None and c is not None and v is not None:
            chapters.setdefault((int(b), int(c)), set()).add(int(v))
    rows = []
    for b, c, v, text in raw:
        if b is None or c is None:
            issues.append(f"wiersz bez księgi/rozdziału: {b} {c} {v}")
            continue
        b, c = int(b), int(c)
        name = BOOK_NUMBERS[b - 1] if 1 <= b <= len(BOOK_NUMBERS) else str(b)
        if v is None:
            have = chapters.get((b, c), set())
            gaps = sorted(set(range(1, max(have, default=0) + 2)) - have)
            if len(gaps) == 1 or (
                len(gaps) == 2 and gaps[-1] == max(have, default=0) + 1
            ):
                v = gaps[0]
                issues.append(f"{name} {c}: werset bez numeru — przyjęto {v}")
            else:
                issues.append(f"{name} {c}: werset bez numeru — pominięty")
                continue
        rows.append((b, c, int(v), text or ""))
    rows.sort(key=lambda r: r[:3])
    return rows, issues


def parse_module(
    path: Path,
    books: set[str] | None = None,
    ref_url: str = "/text/ref/",
    issues: list[str] | None = None,
) -> Iterator[ParsedVerse]:
    rows, found = read_rows(path)
    if issues is not None:
        issues.extend(found)
    for book_no, chapter, verse, scripture in rows:
        if not 1 <= book_no <= len(BOOK_NUMBERS):
            continue
        osis = BOOK_NUMBERS[book_no - 1]
        if books and osis not in books:
            continue
        text, markup, notes = parse_scripture(scripture, ref_url)
        if not text:
            continue
        yield ParsedVerse(osis, chapter, verse, text, markup, notes)
