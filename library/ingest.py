"""Ingestia dokumentu: plik -> strony -> sekcje -> chunki (+ sigla).

Obsługa: .pdf (PyMuPDF), .txt / .md (nagłówki Markdown = sekcje).
Chunkowanie po akapitach do ~MAX_CHARS z nakładką ostatnich zdań;
każdy chunk dostaje listę sigli (corpus.sigla.extract) jako zakresy ordinal.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from corpus.sigla import extract, format_ref, ordinal_range

MAX_CHARS = 1400
MIN_CHARS = 300
OVERLAP_SENTENCES = 2

# Nagłówek numerowany: "3." / "2.1" / "2.1.3." + tytuł; nienumerowany: krótka linia WERSALIKAMI.
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:\d+\.|\d+(?:\.\d+)+\.?)\s+[A-ZĄĆĘŁŃÓŚŹŻ\u0370-\u03ff\u0590-\u05ff„\"].{2,80}$"
)  # "3. Tytuł" / "2.1 Tytuł" / "2.1.3. Tytuł" — ale nie "1010 W. Brueggemann" (bez kropki po numerze)
_CAPS_HEADING_RE = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻ][A-ZĄĆĘŁŃÓŚŹŻ \-–:,()\d]{5,80}$")
# Linia przypisu: numer + tekst z typowymi markerami bibliograficznymi
_FOOTNOTE_LINE_RE = re.compile(
    r"^\d{1,4}\s+(?:[A-ZĄĆĘŁŃÓŚŹŻ]\.\s?[A-ZĄĆĘŁŃÓŚŹŻ]|Por\.|Zob\.|Tamże|Ibid|Cf\.|Dz\.\s?cyt|Op\.\s?cit|jw\.)"
    r"|^\d{1,4}\s+.{0,200}\b(?:jw\.|tamże|op\. cit\.|dz\. cyt\.|ibid\.|s\. \d|ss\. \d|\(\d{4}\),? \d)",
    re.IGNORECASE,
)
_RUNNING_HEADER_MIN_PAGES = 4
_RUNNING_HEADER_RATIO = 0.25


def is_heading(line: str) -> bool:
    if len(line) > 90 or _FOOTNOTE_LINE_RE.search(line):
        return False
    if line.startswith("#"):
        return True
    if _NUMBERED_HEADING_RE.match(line) and not line.rstrip().endswith((".", ",", ";")):
        return True
    return bool(_CAPS_HEADING_RE.match(line)) and len(line.split()) <= 12


_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ„(\[])")
_SOFT_HYPHEN_RE = re.compile(r"(\w)-\n(\w)")
# odsyłacz do przypisu: cyfry przyklejone do litery/kropki/nawiasu ("tekstu12", "Marka.3") —
# celowo NIE po przecinku/dwukropku, żeby nie ruszać sigli ("Mk 16,8", "J 3:16")
_FOOTNOTE_MARK_RE = re.compile(r"(?<=[a-ząćęłńóśźż.)\]])\d{1,3}(?=\s|$)")


@dataclass
class Page:
    number: int | None
    text: str


@dataclass
class ChunkDraft:
    order: int
    section: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    sigla: list[dict] = field(default_factory=list)


def page_text_with_legacy_fonts(page) -> str:
    """Tekst strony jak get_text("text"), ale spany w fontach legacy (Bwhebb) zdekodowane
    do Unicode. Bloki -> linie -> spany; spany w linii sklejane bez separatora."""
    from library.legacy_fonts import decode_bwhebb, is_legacy_hebrew_font

    lines: list[str] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            parts = []
            for span in line["spans"]:
                text = span["text"]
                if text and is_legacy_hebrew_font(span["font"]):
                    text = decode_bwhebb(text)
                parts.append(text)
            lines.append("".join(parts))
        lines.append("")  # granica bloku = pusta linia (jak w trybie "text")
    return "\n".join(lines)


def read_pages(path: Path) -> list[Page]:
    if path.suffix.lower() == ".pdf":
        import pymupdf

        pages = []
        with pymupdf.open(path) as doc:
            for i, page in enumerate(doc, start=1):
                pages.append(Page(i, clean_text(page_text_with_legacy_fonts(page))))
        return strip_running_headers(pages)
    return [Page(None, clean_text(path.read_text(encoding="utf-8")))]


def _header_key(line: str) -> str:
    """Linia bez cyfr i bieli — żywa pagina „97 WYROCZNIA EFODU…" i „98 WYROCZNIA EFODU…" to ta sama."""
    return re.sub(r"[\d\s]+", " ", line).strip().lower()


def strip_running_headers(pages: list[Page]) -> list[Page]:
    """Usuwa linie powtarzające się w pierwszych/ostatnich 3 liniach wielu stron (żywe paginy,
    numery stron). Także wystąpienia tych samych linii sklejone w środku akapitu."""
    if len(pages) < _RUNNING_HEADER_MIN_PAGES:
        return pages
    counts: dict[str, int] = {}
    for p in pages:
        lines = [ln.strip() for ln in p.text.split("\n") if ln.strip()]
        for ln in {*lines[:3], *lines[-3:]}:
            k = _header_key(ln)
            if len(k) >= 4:
                counts[k] = counts.get(k, 0) + 1
    threshold = max(_RUNNING_HEADER_MIN_PAGES, int(len(pages) * _RUNNING_HEADER_RATIO))
    headers = {k for k, n in counts.items() if n >= threshold}
    if not headers:
        return pages
    out = []
    for p in pages:
        kept = []
        for ln in p.text.split("\n"):
            st = ln.strip()
            if st and (_header_key(st) in headers or re.fullmatch(r"\d{1,4}", st)):
                continue
            kept.append(ln)
        out.append(Page(p.number, "\n".join(kept)))
    return out


def clean_text(text: str) -> str:
    """Łączenie przeniesień, usuwanie odsyłaczy do przypisów, normalizacja bieli."""
    text = _SOFT_HYPHEN_RE.sub(r"\1\2", text)
    text = _FOOTNOTE_MARK_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sections(pages: list[Page]) -> list[tuple[str, str, int | None, int | None]]:
    """-> [(tytuł sekcji, tekst, strona_od, strona_do)]"""
    sections: list[tuple[str, list[str], int | None, int | None]] = []
    current_title, buf, p_start, p_end = "", [], None, None
    for page in pages:
        for raw in page.text.split("\n"):
            line = raw.strip()
            if not line:
                buf.append("")
                continue
            if is_heading(line):
                if any(buf):
                    sections.append((current_title, buf, p_start, p_end))
                line = line.lstrip("# ").strip()  # nagłówek Markdown bez krzyżyków
                current_title, buf, p_start = line, [], page.number
            buf.append(line)
            p_end = page.number
            if p_start is None:
                p_start = page.number
    if any(buf):
        sections.append((current_title, buf, p_start, p_end))
    return [(t, "\n".join(b).strip(), ps, pe) for t, b, ps, pe in sections]


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def chunk_section(title: str, text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Akapity łączone do max_chars; za długi akapit tnie się po zdaniach z nakładką."""
    units: list[str] = []
    for para in _paragraphs(text):
        para = re.sub(r"\s*\n\s*", " ", para)
        if len(para) <= max_chars:
            units.append(para)
            continue
        sents = _SENT_SPLIT.split(para)
        cur: list[str] = []
        for s in sents:
            if sum(len(x) for x in cur) + len(s) > max_chars and cur:
                units.append(" ".join(cur))
                cur = cur[-OVERLAP_SENTENCES:]
            cur.append(s)
        if cur:
            units.append(" ".join(cur))

    chunks: list[str] = []
    cur_text = ""
    for u in units:
        if cur_text and len(cur_text) + len(u) + 1 > max_chars:
            chunks.append(cur_text)
            cur_text = u
        else:
            cur_text = f"{cur_text}\n{u}".strip()
    if cur_text:
        if chunks and len(cur_text) < MIN_CHARS:
            chunks[-1] = f"{chunks[-1]}\n{cur_text}"
        else:
            chunks.append(cur_text)
    return chunks


def sigla_for(text: str) -> list[dict]:
    out, seen = [], set()
    for m in extract(text):
        for ref in m.refs:
            start, end = ordinal_range(ref)
            key = (start, end)
            if key in seen:
                continue
            seen.add(key)
            out.append({"ref": format_ref(ref), "start": start, "end": end})
    return out


def build_chunks(path: Path) -> list[ChunkDraft]:
    pages = read_pages(path)
    drafts: list[ChunkDraft] = []
    order = 0
    carry = ""  # zbyt krótka sekcja (np. sam tytuł) dokleja się do następnej
    sections = split_sections(pages)
    for i, (title, text, ps, pe) in enumerate(sections):
        if len(text) < MIN_CHARS and i < len(sections) - 1:
            carry = f"{carry}\n{text}".strip()
            continue
        for piece in chunk_section(title, text):
            body = (
                f"{title}\n{piece}" if title and not piece.startswith(title) else piece
            )
            if carry:
                body, carry = f"{carry}\n{body}", ""
            drafts.append(ChunkDraft(order, title, body, ps, pe, sigla_for(body)))
            order += 1
    if carry:
        drafts.append(ChunkDraft(order, "", carry, None, None, sigla_for(carry)))
    return drafts
