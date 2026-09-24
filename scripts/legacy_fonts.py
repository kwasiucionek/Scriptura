"""Diagnostyka czcionek w PDF: które fonty niosą hebrajski/grekę w kodowaniu legacy
i czy da się je zdekodować z nazw glifów (Adobe Glyph List).

    python scripts/legacy_fonts.py data/library/dspace-<uuid>.pdf [--samples 20]

Wypisuje: fonty (nazwa, liczba spanów, próbki tekstu), a dla fontów podejrzanych
(nazwa zawiera heb/bw/sbl/greek/grk/times new roman hebrew itp. lub próbki wyglądają na
kodowanie klawiszowe) — tabelę cmap: kod -> nazwa glifu -> Unicode wg AGL, o ile się da.
"""

import argparse
import io
import re
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf
from fontTools import agl
from fontTools.ttLib import TTFont

SUSPECT = re.compile(
    r"heb|bw|sbl|grk|greek|semit|ugar|hebraica|aramaic|syriac|copt", re.I
)


def font_spans(doc: pymupdf.Document, max_pages: int | None = None):
    for pno, page in enumerate(doc):
        if max_pages and pno >= max_pages:
            break
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        yield pno + 1, span["font"], span["text"]


def _is_target(ch: str) -> bool:
    o = ord(ch)
    return 0x0590 <= o <= 0x05FF or 0x0370 <= o <= 0x03FF or 0x1F00 <= o <= 0x1FFF


def glyph_table(doc: pymupdf.Document, font_name: str) -> dict[int, str] | None:
    """cmap fontu osadzonego -> {kod: unicode}, przez nazwy glifów (AGL). None, gdy brak."""
    for page in doc:
        for xref, ext, _ftype, basefont, *_ in page.get_fonts(full=True):
            if basefont.split("+")[-1] != font_name.split("+")[-1]:
                continue
            name, ext, _, buf = doc.extract_font(xref)
            if not buf or ext not in ("ttf", "otf", "cff", "pfb", "pfa"):
                return None
            try:
                tt = TTFont(io.BytesIO(buf), fontNumber=0)
                best = tt.getBestCmap() or {}
            except Exception:  # font bez tabeli cmap albo uszkodzony
                return None
            out: dict[int, str] = {}
            for code, gname in best.items():
                uni = agl.toUnicode(gname)
                if uni and (
                    0x0590 <= ord(uni[0]) <= 0x05FF
                    or 0x0370 <= ord(uni[0]) <= 0x03FF
                    or 0x1F00 <= ord(uni[0]) <= 0x1FFF
                ):
                    out[code] = uni
            return out
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--samples", type=int, default=15)
    ap.add_argument("--pages", type=int, default=None, help="ogranicz liczbę stron")
    ap.add_argument(
        "--dump",
        help="wypisz wszystkie ciągi w tym foncie z częstością (do tabeli ręcznej)",
    )
    a = ap.parse_args()

    if a.dump:
        doc = pymupdf.open(a.pdf)
        words: Counter[str] = Counter()
        chars: Counter[str] = Counter()
        for _pno, font, text in font_spans(doc, a.pages):
            if a.dump.lower() in font.lower():
                for w in text.split():
                    words[w] += 1
                    chars.update(w)
        print(
            f"font ~{a.dump}: {sum(words.values())} słów, {len(words)} różnych, {len(chars)} znaków\n"
        )
        print("znaki:", " ".join(f"{c!r}:{n}" for c, n in chars.most_common()))
        print("\nnajczęstsze ciągi (jak w PDF, od lewej) -> odwrócone:")
        for w, n in words.most_common(60):
            print(f"{n:5}  {w!r:20} {w[::-1]!r}")
        return

    doc = pymupdf.open(a.pdf)
    counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    for _, font, text in font_spans(doc, a.pages):
        counts[font] += 1
        if len(samples[font]) < a.samples and len(text.strip()) > 1:
            samples[font].append(text.strip())

    print(f"{Path(a.pdf).name}: {len(doc)} stron, {len(counts)} fontów\n")
    for font, n in counts.most_common():
        flag = "PODEJRZANY" if SUSPECT.search(font) else ""
        print(f"{n:6}  {font:40} {flag}")
        print("        ", " | ".join(samples[font][:8])[:200])
        if flag:
            table = glyph_table(doc, font)
            if table is None:
                print(
                    "         cmap: brak (font nieosadzony lub nieczytelny) -> tabela ręczna albo OCR"
                )
            elif not table:
                print(
                    "         cmap: nazwy glifów bez odpowiedników AGL -> tabela ręczna"
                )
            else:
                print(
                    f"         cmap: {len(table)} kodów z Unicode, np.",
                    ", ".join(
                        f"{chr(c)!r}->{u}" for c, u in sorted(table.items())[:16]
                    ),
                )
                for s in samples[font][:5]:
                    decoded = "".join(table.get(ord(ch), ch) for ch in s)
                    print(f"         {s!r:24} -> {decoded[::-1]!r} (odwrócone)")
        print()


if __name__ == "__main__":
    main()
