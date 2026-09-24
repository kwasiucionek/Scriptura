"""Import przekładu w formacie JSON bible-api-io (np. Biblia Gdańska 1632, MIT-0).

Źródło BG: https://github.com/bible-api-io/bible-api-version-bg (bg.json)
Struktura: booksData[<en_name>].chaptersData[ch][v] (indeks 0 = null)
"""

import json
from collections.abc import Iterator
from pathlib import Path

from corpus.books import BY_EN_NAME
from corpus.importers.base import VerseRecord


def parse_file(path: Path, only: set[str] | None = None) -> Iterator[VerseRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for en_name, book in data["booksData"].items():
        spec = BY_EN_NAME.get(en_name)
        if spec is None or (only and spec.osis not in only):
            continue
        for ch, verses in enumerate(book["chaptersData"]):
            if not verses:
                continue
            for v, text in enumerate(verses):
                if v == 0 or not text:
                    continue
                yield VerseRecord(
                    osis=spec.osis, chapter=ch, verse=v, text=text.strip()
                )


def metadata(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: data.get(k) for k in ("shortName", "longName", "year")}
