"""Import MorphGNT (SBLGNT + morfologia, CC BY-SA 3.0).

Źródło: https://github.com/morphgnt/sblgnt (pliki NN-Xx-morphgnt.txt)
Kolumny: bcv pos parsing text word normalized lemma
  bcv = BBCCVV (BB = 01..27 w kolejności NT)
"""

from collections.abc import Iterator
from pathlib import Path

from corpus.books import BY_MORPHGNT
from corpus.importers.base import TokenRecord, VerseRecord

# Znaki aparatu krytycznego SBLGNT (⸀ ⸂ ⸃ ⸄ ⸅ ⟦ ⟧) — usuwamy z formy wyświetlanej
_APPARATUS = str.maketrans("", "", "\u2e00\u2e02\u2e03\u2e04\u2e05\u27e6\u27e7")


def parse_file(path: Path) -> Iterator[VerseRecord]:
    current: VerseRecord | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        cols = line.split()
        if len(cols) < 7:
            continue
        bcv, pos, parsing, text, word, _normalized, lemma = cols[:7]
        spec = BY_MORPHGNT.get(bcv[:2])
        if spec is None:
            continue
        ch, v = int(bcv[2:4]), int(bcv[4:6])
        if current is None or (current.chapter, current.verse) != (ch, v):
            if current is not None:
                current.text = " ".join(t.surface for t in current.tokens)
                yield current
            current = VerseRecord(osis=spec.osis, chapter=ch, verse=v, text="")
        current.tokens.append(
            TokenRecord(
                surface=text.translate(_APPARATUS), lemma=lemma, morph=parsing, pos=pos
            )
        )
    if current is not None:
        current.text = " ".join(t.surface for t in current.tokens)
        yield current


def parse_dir(gnt_dir: Path, only: set[str] | None = None) -> Iterator[VerseRecord]:
    for path in sorted(gnt_dir.glob("*-morphgnt.txt")):
        code = path.name[:2]
        # nazwa pliku 61..87 -> kod księgi 01..27
        book_code = f"{int(code) - 60:02d}"
        spec = BY_MORPHGNT.get(book_code)
        if spec is None or (only and spec.osis not in only):
            continue
        yield from parse_file(path)
