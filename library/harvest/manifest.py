"""Manifest: lista prac do ingestii, przeglądana ręcznie przed pobraniem.

Jedna linia JSONL = jedna praca:
  {"source": "dspace|openalex|oai|manual", "source_id": "...", "title": "...",
   "authors": ["Marcin Majewski"], "doc_type": "book|article|...", "journal": "...",
   "year": 2010, "doi": "", "url": "https://...", "pdf_url": "https://.../content",
   "license": "CC BY-NC-ND 4.0", "license_url": "...", "access": "open|licensed|private|skip",
   "language": "pl", "note": ""}

`access` ustawia harvester wg licencji (heurystyka niżej), ale ostatnia decyzja jest
ręczna: `skip` wyłącza pozycję z ingestii.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

_CC_RE = re.compile(
    r"cc[- ]?(by(?:[- ]?(?:nc|nd|sa))*)(?:[- ]?(\d\.\d))?", re.IGNORECASE
)


@dataclass
class ManifestEntry:
    source: str
    source_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    doc_type: str = "article"
    journal: str = ""
    year: int | None = None
    doi: str = ""
    url: str = ""
    pdf_url: str = ""
    license: str = ""
    license_url: str = ""
    access: str = "licensed"
    language: str = "pl"
    note: str = ""
    register: str = ""  # scientific | popular | mixed; puste = wg doc_type
    abstract: str = ""  # streszczenie (OpenAlex/DSpace) — kontekst dla kuratora
    local_file: str = ""  # gotowy plik tekstowy (.md/.txt) zapisany przez harvest (blog) — bez pobierania


def normalize_license(*texts: str) -> tuple[str, str]:
    """Z dowolnych pól rights/license -> (etykieta 'CC BY-NC-ND 4.0' | oryginał, access)."""
    joined = " ".join(t for t in texts if t)
    m = _CC_RE.search(
        joined.replace("creativecommons.org/licenses/", "cc-").replace("/", " ")
    )
    if m:
        parts = m.group(1).upper().replace("-", " ").replace("_", " ").split()
        label = "CC " + "-".join(parts) + (f" {m.group(2)}" if m.group(2) else "")
        return label, "open"
    if re.search(r"cc0|public domain|domena publiczna", joined, re.IGNORECASE):
        return "CC0 / domena publiczna", "open"
    if joined.strip():
        return joined.strip()[:120], "licensed"  # jest jakiś zapis praw, ale nie CC
    return "", "licensed"  # brak informacji -> do decyzji ręcznej


def write(path: Path, entries: list[ManifestEntry]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(asdict(e), ensure_ascii=False) + "\n")
    return len(entries)


def read(path: Path) -> list[ManifestEntry]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(ManifestEntry(**json.loads(line)))
    return out
