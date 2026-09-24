"""Harvester OpenAlex — wyszukiwanie autora i jego prac z informacją o OA i licencji.

  /authors?search=<name>            -> kandydaci (ujednoznacznienie po afiliacji)
  /works?filter=author.id:<A...>    -> prace: open_access, primary_location.license,
                                       best_oa_location.pdf_url, DOI, czasopismo, rok
Bez klucza; `mailto` w parametrze daje szybszą pulę (polite pool).
"""

import json
import re
import urllib.parse
import urllib.request

from library.harvest.manifest import ManifestEntry, normalize_license

BASE = "https://api.openalex.org"


def _get(path: str, params: dict, mailto: str = "") -> dict:
    if mailto:
        params = {**params, "mailto": mailto}
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": f"scriptura-harvest/0.1 {mailto}".strip()}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.load(resp)


def find_authors(name: str, mailto: str = "") -> list[dict]:
    """Kandydaci: [{id, display_name, works_count, institutions: [...]}]"""
    data = _get("/authors", {"search": name, "per-page": 10}, mailto)
    out = []
    for a in data.get("results", []):
        insts = [
            i.get("display_name", "")
            for i in a.get("affiliations", [])[:3]
            for i in [i.get("institution", {})]
        ]
        out.append(
            {
                "id": a["id"],
                "display_name": a["display_name"],
                "works_count": a.get("works_count", 0),
                "institutions": insts,
            }
        )
    return out


def abstract_from_index(inv: dict | None) -> str:
    """OpenAlex trzyma abstrakt jako {słowo: [pozycje]}; odtwarzamy tekst."""
    if not inv:
        return ""
    positions: list[tuple[int, str]] = [
        (p, word) for word, ps in inv.items() for p in ps
    ]
    return " ".join(word for _, word in sorted(positions))[:1500]


def work_to_entry(w: dict) -> ManifestEntry:
    loc = w.get("primary_location") or {}
    best = w.get("best_oa_location") or {}
    oa = w.get("open_access") or {}
    license_label, access = normalize_license(
        loc.get("license") or "", best.get("license") or ""
    )
    if not oa.get("is_oa"):
        access = "licensed"
    source = (loc.get("source") or {}).get("display_name", "")
    wtype = w.get("type", "article")
    doc_type = "book" if wtype in ("book", "book-chapter", "monograph") else "article"
    return ManifestEntry(
        source="openalex",
        source_id=w["id"],
        title=w.get("display_name") or w.get("title") or "",
        authors=[
            a.get("author", {}).get("display_name", "")
            for a in w.get("authorships", [])
        ],
        doc_type=doc_type,
        journal=source,
        year=w.get("publication_year"),
        doi=(w.get("doi") or "").replace("https://doi.org/", ""),
        url=best.get("landing_page_url")
        or loc.get("landing_page_url")
        or w.get("doi")
        or "",
        pdf_url=best.get("pdf_url") or "",
        license=license_label,
        license_url="",
        access=access,
        language=(w.get("language") or "pl")[:2],
        note=f"oa_status={oa.get('oa_status', '')}",
        abstract=abstract_from_index(w.get("abstract_inverted_index")),
    )


def works_for_author(
    author_id: str, mailto: str = "", only_oa: bool = False
) -> list[ManifestEntry]:
    author_id = author_id.rsplit("/", 1)[
        -1
    ].strip()  # akceptuj A… i https://openalex.org/A…
    if not re.fullmatch(r"A\d+", author_id):
        raise ValueError(f"Niepoprawne id autora OpenAlex: {author_id!r}")
    entries: list[ManifestEntry] = []
    cursor = "*"
    while cursor:
        flt = f"author.id:{author_id}" + (",is_oa:true" if only_oa else "")
        data = _get(
            "/works", {"filter": flt, "per-page": 100, "cursor": cursor}, mailto
        )
        entries += [work_to_entry(w) for w in data.get("results", [])]
        cursor = (data.get("meta") or {}).get("next_cursor")
    return entries
