"""Import BibTeX (np. eksport z profilu Google Scholar: zaznacz wszystko -> Eksportuj -> BibTeX).

Minimalny parser: wpisy @type{key, pole = {…} | "…" | liczba, …}. Bez zagnieżdżeń
w wartościach poza jednym poziomem nawiasów (Scholar tak eksportuje).
Pozycje dostają access=licensed i pusty pdf_url — to lista „co istnieje", nie „co wolno".
"""

import re

from library.harvest.manifest import ManifestEntry

_ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", re.DOTALL)
_FIELD_RE = re.compile(
    r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|\d+)\s*,?", re.DOTALL
)
_TYPE_MAP = {
    "book": "book",
    "incollection": "book",
    "inbook": "book",
    "phdthesis": "book",
    "article": "article",
    "inproceedings": "article",
    "misc": "article",
}


def _clean(v: str) -> str:
    v = v.strip()
    if v[:1] in '{"':
        v = v[1:-1]
    return re.sub(r"\s+", " ", v.replace("{", "").replace("}", "")).strip()


def _authors(raw: str) -> list[str]:
    out = []
    for a in re.split(r"\s+and\s+", raw):
        a = a.strip()
        if "," in a:  # "Majewski, Marcin" -> "Marcin Majewski"
            last, first = [p.strip() for p in a.split(",", 1)]
            a = f"{first} {last}".strip()
        if a:
            out.append(a)
    return out


def parse(text: str) -> list[ManifestEntry]:
    entries = []
    for m in _ENTRY_RE.finditer(text):
        etype, key, body = m.group(1).lower(), m.group(2), m.group(3)
        fields = {k.lower(): _clean(v) for k, v in _FIELD_RE.findall(body)}
        year = fields.get("year", "")
        entries.append(
            ManifestEntry(
                source="bibtex",
                source_id=key,
                title=fields.get("title", ""),
                authors=_authors(fields.get("author", "")),
                doc_type=_TYPE_MAP.get(etype, "article"),
                journal=fields.get("journal")
                or fields.get("booktitle")
                or fields.get("publisher", ""),
                year=int(year) if year.isdigit() else None,
                doi=fields.get("doi", ""),
                url=fields.get("url", ""),
                pdf_url="",
                license="",
                access="licensed",
                note=f"bibtex:{etype}",
            )
        )
    return entries
