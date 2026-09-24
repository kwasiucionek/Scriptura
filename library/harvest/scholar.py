"""Profil Google Scholar przez SerpApi (engine=google_scholar_author).

Scholar nie ma API i blokuje scraping; SerpApi jest pośrednikiem (darmowo 100 zapytań/mies.,
profil = ceil(prac/100) zapytań). Wynik to lista „co istnieje": access=licensed, bez pdf_url.
Wymaga SERPAPI_KEY w .env. Alternatywa bez klucza: eksport BibTeX z profilu -> `harvest bibtex`.
"""

import json
import re
import urllib.parse
import urllib.request

from library.harvest.manifest import ManifestEntry

BASE = "https://serpapi.com/search.json"


def _get(params: dict) -> dict:
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
        return json.load(resp)


def article_to_entry(a: dict, author_name: str) -> ManifestEntry:
    pub = a.get("publication") or ""
    year = a.get("year") or ""
    # "Ruch Biblijny i Liturgiczny 72 (3), 245-260, 2019" -> czasopismo = do pierwszej cyfry/nawiasu
    journal = re.split(r"\s+\d|\s*\(", pub, maxsplit=1)[0].strip(" ,")
    doc_type = (
        "book"
        if not journal or re.search(r"wydawnictwo|press|verlag|petrus|wam\b", pub, re.I)
        else "article"
    )
    return ManifestEntry(
        source="scholar",
        source_id=a.get("citation_id") or a.get("link", ""),
        title=a.get("title", ""),
        authors=[
            x.strip() for x in (a.get("authors") or author_name).split(",") if x.strip()
        ],
        doc_type=doc_type,
        journal=journal,
        year=int(year) if str(year).isdigit() else None,
        url=a.get("link", ""),
        access="licensed",
        note=f"scholar; cytowań={a.get('cited_by', {}).get('value', 0)}; {pub}",
    )


def harvest_profile(
    author_id: str, api_key: str, max_pages: int = 5
) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    author_name = ""
    for page in range(max_pages):
        data = _get(
            {
                "engine": "google_scholar_author",
                "author_id": author_id,
                "hl": "pl",
                "num": 100,
                "start": page * 100,
                "api_key": api_key,
            }
        )
        author_name = author_name or (data.get("author") or {}).get("name", "")
        arts = data.get("articles") or []
        entries += [article_to_entry(a, author_name) for a in arts]
        if len(arts) < 100:
            break
    return entries
