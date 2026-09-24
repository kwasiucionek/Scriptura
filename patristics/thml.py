"""Parser ThML (CCEL): tom ANF/NPNF -> dzieła -> pasaże z odsyłaczami biblijnymi.

ThML to XML/HTML: <ThML><ThML.body> z zagnieżdżonymi <div1 title=…> … <div4>, akapitami <p>,
znacznikami stron <pb n="123"/> i odsyłaczami <scripRef passage="Gen. i. 26" osisRef="Bible:Gen.1.26">.
Heurystyka dzieł: div1 = dzieło (autor z atrybutu/ tytułu tomu lub nagłówka „Irenaeus Against Heresies”),
div2+ = sekcje. Pasaż = kolejne akapity do ~1500 znaków w obrębie sekcji.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from corpus.books import BOOKS

_BY_OSIS = {b.osis: b for b in BOOKS}
_EN_ABBR = {
    "Gen": "Gen", "Ex": "Exod", "Exod": "Exod", "Lev": "Lev", "Num": "Num", "Deut": "Deut", "Josh": "Josh",
    "Judg": "Judg", "Ruth": "Ruth", "1Sam": "1Sam", "2Sam": "2Sam", "1Kgs": "1Kgs", "2Kgs": "2Kgs",
    "1Chr": "1Chr", "2Chr": "2Chr", "Ezra": "Ezra", "Neh": "Neh", "Tob": "Tob", "Jdt": "Jdt", "Esth": "Esth",
    "1Macc": "1Macc", "2Macc": "2Macc", "Job": "Job", "Ps": "Ps", "Prov": "Prov", "Eccl": "Eccl", "Song": "Song",
    "Wis": "Wis", "Sir": "Sir", "Isa": "Isa", "Jer": "Jer", "Lam": "Lam", "Bar": "Bar", "Ezek": "Ezek",
    "Dan": "Dan", "Hos": "Hos", "Joel": "Joel", "Amos": "Amos", "Obad": "Obad", "Jonah": "Jonah", "Mic": "Mic",
    "Nah": "Nah", "Hab": "Hab", "Zeph": "Zeph", "Hag": "Hag", "Zech": "Zech", "Mal": "Mal", "Matt": "Matt",
    "Mark": "Mark", "Luke": "Luke", "John": "John", "Acts": "Acts", "Rom": "Rom", "1Cor": "1Cor", "2Cor": "2Cor",
    "Gal": "Gal", "Eph": "Eph", "Phil": "Phil", "Col": "Col", "1Thess": "1Thess", "2Thess": "2Thess",
    "1Tim": "1Tim", "2Tim": "2Tim", "Titus": "Titus", "Phlm": "Phlm", "Heb": "Heb", "Jas": "Jas", "1Pet": "1Pet",
    "2Pet": "2Pet", "1John": "1John", "2John": "2John", "3John": "3John", "Jude": "Jude", "Rev": "Rev",
}  # fmt: skip
# skróty w atrybucie passage („Gen. i. 26”, „1 Cor. xv. 3”, „Cant. ii. 1”, „Ecclus. xxiv”) -> OSIS
_TEXT_ABBR = {
    "gen": "Gen", "ex": "Exod", "exod": "Exod", "lev": "Lev", "num": "Num", "deut": "Deut", "josh": "Josh",
    "judg": "Judg", "ruth": "Ruth", "1sam": "1Sam", "2sam": "2Sam", "1kings": "1Kgs", "2kings": "2Kgs",
    "1kgs": "1Kgs", "2kgs": "2Kgs", "1chron": "1Chr", "2chron": "2Chr", "1chr": "1Chr", "2chr": "2Chr",
    "ezra": "Ezra", "neh": "Neh", "tob": "Tob", "tobit": "Tob", "judith": "Jdt", "jdt": "Jdt", "esth": "Esth",
    "esther": "Esth", "1macc": "1Macc", "2macc": "2Macc", "job": "Job", "ps": "Ps", "psa": "Ps", "psalm": "Ps",
    "prov": "Prov", "eccles": "Eccl", "eccl": "Eccl", "cant": "Song", "song": "Song", "wisd": "Wis", "wis": "Wis",
    "ecclus": "Sir", "sir": "Sir", "isa": "Isa", "jer": "Jer", "lam": "Lam", "bar": "Bar", "ezek": "Ezek",
    "dan": "Dan", "hos": "Hos", "joel": "Joel", "amos": "Amos", "obad": "Obad", "jonah": "Jonah", "mic": "Mic",
    "nah": "Nah", "hab": "Hab", "zeph": "Zeph", "hag": "Hag", "zech": "Zech", "mal": "Mal", "matt": "Matt",
    "mark": "Mark", "luke": "Luke", "john": "John", "acts": "Acts", "rom": "Rom", "1cor": "1Cor", "2cor": "2Cor",
    "gal": "Gal", "eph": "Eph", "phil": "Phil", "col": "Col", "1thess": "1Thess", "2thess": "2Thess",
    "1tim": "1Tim", "2tim": "2Tim", "tit": "Titus", "titus": "Titus", "philem": "Phlm", "phlm": "Phlm", "heb": "Heb",
    "jas": "Jas", "james": "Jas", "1pet": "1Pet", "2pet": "2Pet", "1john": "1John", "2john": "2John",
    "3john": "3John", "jude": "Jude", "rev": "Rev", "apoc": "Rev",
}  # fmt: skip
_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
_PASSAGE_RE = re.compile(
    r"^\s*([1-3]?\s?[A-Za-z]+)\.?\s+([ivxlc]+|\d+)\.?\s*(?:(\d+)\s*(?:[-–,]\s*(\d+))?)?",
    re.I,
)


def _roman(text: str) -> int | None:
    text = text.lower()
    if text.isdigit():
        return int(text)
    total, prev = 0, 0
    for ch in reversed(text):
        v = _ROMAN.get(ch)
        if v is None:
            return None
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def parse_parsed_attr(parsed: str) -> tuple[int, int] | None:
    """CCEL parsed="|Gen|1|26|1|27|" -> zakres ordinali."""
    parts = [x for x in parsed.split("|") if x != ""]
    if len(parts) < 3:
        return None
    book = _EN_ABBR.get(parts[0]) or _TEXT_ABBR.get(parts[0].lower().replace(" ", ""))
    try:
        ch, vs = int(parts[1]), int(parts[2])
        ch2 = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else ch
        vs2 = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else vs
    except ValueError:
        return None
    if not book:
        return None
    s, e = _ordinal(book, ch, vs), _ordinal(book, ch2, vs2)
    return (s, e) if s and e and e >= s else None


def parse_passage_text(text: str) -> tuple[int, int] | None:
    """„Gen. i. 26”, „Isa. vii. 14”, „1 Cor. xv. 3-5”, „Matt. v” -> zakres ordinali (tylko pierwsze odniesienie)."""
    m = _PASSAGE_RE.match(text.replace("\u00a0", " "))
    if not m:
        return None
    book = _TEXT_ABBR.get(m.group(1).lower().replace(" ", "").rstrip("."))
    ch = _roman(m.group(2))
    if not book or not ch:
        return None
    if not m.group(3):
        s, e = _ordinal(book, ch, 1), _ordinal(book, ch, 999)
    else:
        s = _ordinal(book, ch, int(m.group(3)))
        e = _ordinal(book, ch, int(m.group(4))) if m.group(4) else s
    return (s, e) if s and e and e >= s else None


_OSIS_RE = re.compile(
    r"(?:Bible:)?([1-3]?[A-Za-z]+)\.(\d+)(?:\.(\d+))?(?:-(?:[1-3]?[A-Za-z]+\.)?(?:(\d+)\.)?(\d+))?"
)


def _ordinal(osis: str, ch: int, vs: int) -> int | None:
    book = _BY_OSIS.get(_EN_ABBR.get(osis, osis))
    return book.order * 1_000_000 + ch * 1_000 + vs if book else None


def parse_osis_ref(ref: str) -> tuple[int, int] | None:
    """'Bible:Gen.1.26' | 'Gen.1.26-Gen.1.28' | 'Gen.1' -> (start, end) ordinali; None gdy nieznane."""
    m = _OSIS_RE.match(ref.strip())
    if not m:
        return None
    book, ch, vs, ch2, vs2 = (
        m.group(1),
        int(m.group(2)),
        m.group(3),
        m.group(4),
        m.group(5),
    )
    if vs is None:  # cały rozdział
        s, e = _ordinal(book, ch, 1), _ordinal(book, ch, 999)
    else:
        s = _ordinal(book, ch, int(vs))
        e = _ordinal(book, int(ch2) if ch2 else ch, int(vs2)) if vs2 else s
    return (s, e) if s and e and e >= s else None


@dataclass
class PassageDraft:
    section: str
    page: str
    text: str
    refs: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class WorkDraft:
    title: str
    author: str
    passages: list[PassageDraft] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text_of(el: ET.Element, state: dict) -> str:
    """Tekst elementu z pominięciem przypisów; aktualizuje state['page'] po <pb>."""
    out = []
    if _local(el.tag) in ("note", "sup"):
        # przypis: treść pomijamy, ale odsyłacze biblijne z niego liczą się dla pasażu
        for sr in el.iter():
            if _local(sr.tag) == "scripref":
                rng = scripref_range(sr)
                if rng:
                    state.setdefault("refs", []).append(rng)
        return el.tail or ""
    if _local(el.tag) == "pb":
        state["page"] = el.get("n", state.get("page", ""))
        return el.tail or ""
    if _local(el.tag) == "scripref":
        rng = scripref_range(el)
        if rng:
            state.setdefault("refs", []).append(rng)
    out.append(el.text or "")
    for ch in el:
        out.append(_text_of(ch, state))
    return (
        "".join(out) + (el.tail or "") if el is not state.get("_root") else "".join(out)
    )


def scripref_range(el: ET.Element) -> tuple[int, int] | None:
    """osisRef -> parsed -> passage/tekst elementu."""
    osis = el.get("osisRef") or el.get("osisref") or ""
    if osis:
        r = parse_osis_ref(osis)
        if r:
            return r
    parsed = el.get("parsed") or ""
    if parsed:
        r = parse_parsed_attr(parsed)
        if r:
            return r
    text = el.get("passage") or "".join(el.itertext())
    return parse_passage_text(text) if text else None


def _fold(text: str) -> str:
    t = unicodedata.normalize(
        "NFKD", text.replace("Æ", "AE").replace("æ", "ae").replace("Œ", "OE")
    )
    return "".join(c for c in t if not unicodedata.combining(c))


def parse_volume(
    xml_bytes: bytes, default_author: str = "", max_chars: int = 1500
) -> list[WorkDraft]:
    root = ET.fromstring(xml_bytes)
    body = next((e for e in root.iter() if _local(e.tag) == "thml.body"), root)
    works: list[WorkDraft] = []
    for div1 in body:
        if not _local(div1.tag).startswith("div"):
            continue
        title = _fold((div1.get("title") or "").strip())
        if not title or _SKIP_RE.search(title):
            continue
        author = (
            title.title() if title.isupper() else default_author
        )  # CCEL: div1 = AUTOR (wielkimi literami)
        subs = [
            d
            for d in div1
            if _local(d.tag).startswith("div") and (d.get("title") or "").strip()
        ]
        if author != default_author and subs:  # dzieła = div2 pod autorem
            for d2 in subs:
                t2 = _fold(d2.get("title").strip())
                if _SKIP_RE.search(t2):
                    continue
                work = WorkDraft(title=t2, author=author)
                _walk(d2, work, [], {"page": ""}, max_chars)
                if work.passages:
                    works.append(work)
        else:
            work = WorkDraft(title=title, author=author)
            _walk(div1, work, [], {"page": ""}, max_chars)
            if work.passages:
                works.append(work)
    return works


_SKIP_RE = re.compile(
    r"^(title page|contents|index|indexes|preface|introductory note|translator|bibliograph|general index|elucidations?$)",
    re.I,
)


def _walk(
    div: ET.Element, work: WorkDraft, path: list[str], state: dict, max_chars: int
) -> None:
    buf: list[str] = []
    refs: list[tuple[int, int]] = []
    page = ""  # strona, na której zaczyna się bieżący pasaż

    def flush():
        nonlocal buf, refs, page
        text = re.sub(r"\s+", " ", " ".join(buf)).strip()
        if len(text) > 40:
            work.passages.append(
                PassageDraft(
                    section=" / ".join(path), page=page, text=text, refs=list(refs)
                )
            )
        buf, refs, page = [], [], ""

    for el in div:
        tag = _local(el.tag)
        if tag.startswith("div"):
            flush()
            sub = (el.get("title") or "").strip()
            _walk(el, work, path + ([sub] if sub else []), state, max_chars)
            continue
        if tag == "pb":
            state["page"] = el.get("n", state.get("page", ""))
            continue
        if tag in ("p", "verse", "l", "blockquote"):
            st = {"page": state.get("page", ""), "refs": [], "_root": el}
            text = _text_of(el, st)
            if not buf:
                page = state.get("page", "")
            state["page"] = st["page"]
            refs.extend(st["refs"])
            buf.append(text)
            if sum(len(b) for b in buf) >= max_chars:
                flush()
    flush()
