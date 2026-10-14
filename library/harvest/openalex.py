"""Harvester OpenAlex — prace autora albo całego czasopisma z informacją o OA i licencji.

  /authors?search=<name>            -> kandydaci (ujednoznacznienie po afiliacji)
  /sources?search=<name>            -> kandydaci czasopism (ISSN, wydawca, liczba prac)
  /works?filter=author.id:<A...>    -> prace autora
  /works?filter=primary_location.source.id:<S...>  -> prace czasopisma
                                       (open_access, licencja, pdf_url, DOI, rok, abstrakt)
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


def find_sources(name: str, mailto: str = "") -> list[dict]:
    """Kandydaci czasopism: [{id, display_name, works_count, issn, publisher}]"""
    data = _get("/sources", {"search": name, "per-page": 10}, mailto)
    return [
        {
            "id": src["id"],
            "display_name": src.get("display_name", ""),
            "works_count": src.get("works_count", 0),
            "issn": src.get("issn_l") or "",
            "publisher": src.get("host_organization_name") or "",
        }
        for src in data.get("results", [])
    ]


def _norm_id(raw: str, prefix: str) -> str:
    """Akceptuj A…/S… i https://openalex.org/A…"""
    value = raw.rsplit("/", 1)[-1].strip()
    if not re.fullmatch(rf"{prefix}\d+", value):
        raise ValueError(f"Niepoprawne id OpenAlex ({prefix}…): {raw!r}")
    return value


def _works(flt: str, mailto: str = "") -> list[ManifestEntry]:
    """Wszystkie prace dla filtra (paginacja kursorem)."""
    entries: list[ManifestEntry] = []
    cursor = "*"
    while cursor:
        data = _get(
            "/works", {"filter": flt, "per-page": 100, "cursor": cursor}, mailto
        )
        results = data.get("results", [])
        entries += [work_to_entry(w) for w in results]
        cursor = (data.get("meta") or {}).get("next_cursor") if results else None
    return entries


def works_for_author(
    author_id: str, mailto: str = "", only_oa: bool = False
) -> list[ManifestEntry]:
    flt = f"author.id:{_norm_id(author_id, 'A')}"
    return _works(flt + (",is_oa:true" if only_oa else ""), mailto)


def works_for_source(
    source_id: str,
    mailto: str = "",
    only_oa: bool = False,
    from_year: int | None = None,
) -> list[ManifestEntry]:
    """Prace czasopisma (np. Verbum Vitae); from_year zawęża do nowszych roczników."""
    flt = f"primary_location.source.id:{_norm_id(source_id, 'S')}"
    if only_oa:
        flt += ",is_oa:true"
    if from_year:
        flt += f",from_publication_date:{int(from_year)}-01-01"
    return _works(flt, mailto)
