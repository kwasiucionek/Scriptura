"""Rozkłada ręcznie pobrane PDF-y pod nazwy oczekiwane przez ingest_manifest.

    python scripts/place_downloads.py data/manifests/majewski-open.jsonl ~/Pobrane [--apply]

Dopasowanie: nazwa pliku vs tytuł z manifestu (po normalizacji), a gdy nazwa nic nie mówi —
tekst pierwszej strony PDF-u vs tytuł. Bez --apply tylko pokazuje plan. Pliki są kopiowane
(nie przenoszone) do DATA_DIR/library/<source>-<source_id>.pdf.
"""

import argparse
import os
import re
import shutil
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from library.harvest.manifest import read  # noqa: E402


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def first_page_text(path: Path) -> str:
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            return doc[0].get_text("text")[:1500] if len(doc) else ""
    except Exception:
        return ""


def score(title: str, filename: str, page_text: str) -> float:
    t = norm(title)
    words = [w for w in t.split() if len(w) > 3]
    by_name = SequenceMatcher(None, t, norm(filename)).ratio()
    pt = norm(page_text)
    by_page = sum(1 for w in words if w in pt) / max(len(words), 1) if pt else 0.0
    return max(by_name, by_page)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("downloads_dir")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-score", type=float, default=0.6)
    a = ap.parse_args()

    entries = [e for e in read(Path(a.manifest)) if e.access != "skip"]
    pdfs = sorted(Path(a.downloads_dir).expanduser().glob("*.pdf"))
    pages = {p: first_page_text(p) for p in pdfs}
    out_dir: Path = settings.DATA_DIR / "library"
    out_dir.mkdir(parents=True, exist_ok=True)

    used: set[Path] = set()
    plan = []
    for e in entries:
        best = max(
            ((score(e.title, p.stem, pages[p]), p) for p in pdfs if p not in used),
            default=(0.0, None),
        )
        s, p = best
        if p is not None and s >= a.min_score:
            used.add(p)
            plan.append((e, p, s))
        else:
            plan.append((e, None, s))

    for e, p, s in plan:
        dest = out_dir / f"{e.source}-{e.source_id}.pdf"
        if p is None:
            print(f"  BRAK    {e.title[:70]}")
            continue
        flag = "jest" if dest.exists() else "kopiuj"
        print(f"  {s:.2f} {flag:6} {p.name[:45]:45} -> {dest.name}   [{e.title[:40]}]")
        if a.apply and not dest.exists():
            shutil.copy2(p, dest)
    unmatched = [p.name for p in pdfs if p not in used]
    if unmatched:
        print("\nPliki bez dopasowania:", *unmatched, sep="\n  ")
    if not a.apply:
        print("\n(plan; dodaj --apply, żeby skopiować)")


if __name__ == "__main__":
    main()
