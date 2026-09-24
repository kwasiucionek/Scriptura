"""Import cross-references OpenBible.info (CC BY): TSV „From Verse<TAB>To Verse<TAB>Votes”.

Identyfikatory w formacie OSIS: Gen.1.1; zakresy Gen.1.1-Gen.1.3 (także z drugim skrótem
tej samej księgi lub bez niego). Księgi spoza kanonu (brak w BOOKS) są pomijane.
Mapowanie na ordinal = order·10^6 + rozdział·10^3 + werset (jak w corpus.models).
"""

import re
from collections.abc import Iterator
from pathlib import Path

from corpus.books import BOOKS

_BY_OSIS = {b.osis: b for b in BOOKS}
_REF_RE = re.compile(r"^([1-4]?[A-Za-z]+)\.(\d+)\.(\d+)$")


def ordinal_of(ref: str) -> int | None:
    m = _REF_RE.match(ref.strip())
    if not m:
        return None
    book = _BY_OSIS.get(m.group(1))
    if not book:
        return None
    return book.order * 1_000_000 + int(m.group(2)) * 1_000 + int(m.group(3))


def parse_range(ref: str) -> tuple[int, int] | None:
    ref = ref.strip()
    if "-" in ref:
        a, b = ref.split("-", 1)
        start = ordinal_of(a)
        if start is None:
            return None
        if _REF_RE.match(b):
            end = ordinal_of(b)
        else:  # skrócona forma "Gen.1.1-3" -> ten sam rozdział
            end = ordinal_of(f"{a.rsplit('.', 1)[0]}.{b}") if b.isdigit() else None
        return (start, end) if end is not None and end >= start else None
    o = ordinal_of(ref)
    return (o, o) if o is not None else None


def parse_file(
    path: Path, min_votes: int = -999
) -> Iterator[tuple[int, int, int, int]]:
    """(from_ordinal, to_start, to_end, votes); pomija nagłówek i wiersze spoza kanonu."""
    with path.open(encoding="utf-8-sig") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or parts[0].lower().startswith("from"):
                continue
            src = ordinal_of(parts[0])
            rng = parse_range(parts[1])
            if src is None or rng is None:
                continue
            try:
                votes = int(parts[2]) if len(parts) > 2 and parts[2].strip() else 0
            except ValueError:
                votes = 0
            if votes < min_votes:
                continue
            yield src, rng[0], rng[1], votes
