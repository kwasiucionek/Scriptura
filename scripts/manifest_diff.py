"""Porównanie manifestów po tytule: co z listy „istnieje" (Scholar/BibTeX) nie ma odpowiednika
w listach „wolno" (DSpace, OpenAlex). Wynik = lista pozycji do rozmowy z autorem.

    python scripts/manifest_diff.py data/manifests/majewski-scholar.jsonl \
        data/manifests/majewski-open.jsonl data/manifests/majewski-openalex.jsonl
"""

import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from library.harvest.manifest import read  # noqa: E402


def key(title: str) -> str:
    t = unicodedata.normalize("NFKD", title.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def main() -> None:
    base = read(Path(sys.argv[1]))
    others = [e for p in sys.argv[2:] for e in read(Path(p))]
    other_keys = [(key(e.title), e) for e in others]
    missing, matched = [], []
    for e in base:
        k = key(e.title)
        best = max(
            ((SequenceMatcher(None, k, ok).ratio(), oe) for ok, oe in other_keys),
            default=(0, None),
        )
        (matched if best[0] >= 0.85 else missing).append((e, best))
    print(
        f"{len(base)} pozycji bazowych, {len(matched)} znalezionych w źródłach otwartych, {len(missing)} brak:\n"
    )
    for e, _ in sorted(missing, key=lambda t: -(t[0].year or 0)):
        print(f"  {e.year or '----'}  {e.title[:90]}  [{e.note}]")


if __name__ == "__main__":
    main()
