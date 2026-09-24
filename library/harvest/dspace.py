"""Harvester DSpace 7 (REST API /server/api) — np. OPEN ICM UW: https://open.icm.edu.pl

Wyszukiwanie: /server/api/discover/search/objects?query=...&dsoType=item
Rekord:       /server/api/core/items/<uuid>
Pliki:        /server/api/core/items/<uuid>/bundles -> bundle ORIGINAL -> bitstreams
              -> /server/api/core/bitstreams/<uuid>/content
Metadane DSpace to słownik {"dc.title": [{"value": ...}], ...}.
"""

import json
import urllib.parse
import urllib.request

from library.harvest.manifest import ManifestEntry, normalize_license

UA = "scriptura-harvest/0.1 (+kontakt w .env: HARVEST_MAILTO)"


def _get(url: str, mailto: str = "") -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": f"{UA} {mailto}".strip()},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.load(resp)


def md(item: dict, key: str) -> list[str]:
    """Wartości pola metadanych, np. md(item, 'dc.contributor.author')."""
    return [
        m.get("value", "")
        for m in item.get("metadata", {}).get(key, [])
        if m.get("value")
    ]


def first(item: dict, *keys: str) -> str:
    for k in keys:
        vals = md(item, k)
        if vals:
            return vals[0]
    return ""


def item_to_entry(base_url: str, item: dict, pdf_url: str = "") -> ManifestEntry:
    uuid = item.get("uuid") or item.get("id", "")
    year_raw = first(item, "dc.date.issued", "dc.date")[:4]
    license_label, access = normalize_license(
        first(item, "dc.rights"),
        first(item, "dc.rights.uri"),
        first(item, "dc.rights.license"),
    )
    dtype = first(item, "dc.type").lower()
    doc_type = (
        "book"
        if "book" in dtype or "monograf" in dtype or "książk" in dtype
        else "article"
    )
    if "chapter" in dtype or "rozdzia" in dtype:
        doc_type = "book"
    return ManifestEntry(
        source="dspace",
        source_id=uuid,
        title=first(item, "dc.title"),
        authors=md(item, "dc.contributor.author"),
        doc_type=doc_type,
        journal=first(item, "dc.publisher", "dc.relation.ispartof"),
        year=int(year_raw) if year_raw.isdigit() else None,
        doi=first(item, "dc.identifier.doi").replace("https://doi.org/", ""),
        url=f"{base_url}/items/{uuid}",
        pdf_url=pdf_url,
        license=license_label,
        license_url=first(item, "dc.rights.uri"),
        access=access,
        language=(first(item, "dc.language.iso") or "pl")[:2],
        note="",
        abstract=first(item, "dc.description.abstract")[:1500],
    )


def search_items(
    base_url: str,
    query: str,
    mailto: str = "",
    page_size: int = 50,
    max_pages: int = 10,
) -> list[dict]:
    """Wszystkie itemy pasujące do zapytania (np. 'author:"Majewski, Marcin"')."""
    items: list[dict] = []
    for page in range(max_pages):
        q = urllib.parse.urlencode(
            {"query": query, "dsoType": "item", "size": page_size, "page": page}
        )
        data = _get(f"{base_url}/server/api/discover/search/objects?{q}", mailto)
        objs = (
            data.get("_embedded", {})
            .get("searchResult", {})
            .get("_embedded", {})
            .get("objects", [])
        )
        if not objs:
            break
        items += [
            o["_embedded"]["indexableObject"]
            for o in objs
            if "indexableObject" in o.get("_embedded", {})
        ]
        if len(objs) < page_size:
            break
    return items


def pdf_bitstream_url(base_url: str, item_uuid: str, mailto: str = "") -> str:
    """URL treści pierwszego PDF-a z bundle ORIGINAL (pusty, gdy brak)."""
    bundles = _get(f"{base_url}/server/api/core/items/{item_uuid}/bundles", mailto)
    for b in bundles.get("_embedded", {}).get("bundles", []):
        if b.get("name") != "ORIGINAL":
            continue
        bits = _get(
            f"{base_url}/server/api/core/bundles/{b['uuid']}/bitstreams", mailto
        )
        for bs in bits.get("_embedded", {}).get("bitstreams", []):
            name = (bs.get("name") or "").lower()
            fmt = (bs.get("_embedded", {}).get("format") or {}).get("mimetype", "")
            if name.endswith(".pdf") or fmt == "application/pdf":
                return f"{base_url}/server/api/core/bitstreams/{bs['uuid']}/content"
    return ""


def harvest(base_url: str, query: str, mailto: str = "") -> list[ManifestEntry]:
    entries = []
    for item in search_items(base_url, query, mailto):
        uuid = item.get("uuid") or item.get("id")
        pdf = pdf_bitstream_url(base_url, uuid, mailto) if uuid else ""
        entries.append(item_to_entry(base_url, item, pdf))
    return entries


def browse_author_items(
    base_url: str,
    author_value: str,
    mailto: str = "",
    page_size: int = 50,
    max_pages: int = 20,
) -> list[dict]:
    """Itemy autora po kanonicznej wartości (jak /browse/author?value=...), dokładne dopasowanie.
    Endpoint: /server/api/discover/browses/author/items?filterValue=<value>"""
    items: list[dict] = []
    for page in range(max_pages):
        q = urllib.parse.urlencode(
            {"filterValue": author_value, "size": page_size, "page": page}
        )
        data = _get(f"{base_url}/server/api/discover/browses/author/items?{q}", mailto)
        objs = data.get("_embedded", {}).get("items", [])
        if not objs:
            break
        items += objs
        if len(objs) < page_size:
            break
    return items


def harvest_author(
    base_url: str, author_value: str, mailto: str = ""
) -> list[ManifestEntry]:
    entries = []
    for item in browse_author_items(base_url, author_value, mailto):
        uuid = item.get("uuid") or item.get("id")
        pdf = pdf_bitstream_url(base_url, uuid, mailto) if uuid else ""
        entries.append(item_to_entry(base_url, item, pdf))
    return entries
