"""Polskie etykiety miejsc z Wikidata (CC0): nazwa, warianty, krótki opis, artykuł w plwiki.

API wbgetentities: do 50 identyfikatorów na żądanie, tylko język polski i polska Wikipedia.
"""

import json
import urllib.parse
import urllib.request

API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "Scriptura/0.1 (https://scriptura.cytr.us)"
BATCH = 50


def fetch(ids: list[str], timeout: int = 30) -> dict:
    """Encje Wikidata dla identyfikatorów Q… (słownik id -> encja)."""
    params = {
        "action": "wbgetentities",
        "ids": "|".join(ids),
        "props": "labels|aliases|descriptions|sitelinks/urls",
        "languages": "pl",
        "sitefilter": "plwiki",
        "format": "json",
    }
    req = urllib.request.Request(
        f"{API}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — stały adres API
        return json.load(resp).get("entities", {})


def parse_entity(entity: dict) -> dict:
    """{label, aliases, description, wiki} po polsku; brak danych -> puste wartości."""
    label = (entity.get("labels", {}).get("pl") or {}).get("value", "")
    aliases = [a.get("value", "") for a in entity.get("aliases", {}).get("pl", [])]
    description = (entity.get("descriptions", {}).get("pl") or {}).get("value", "")
    site = entity.get("sitelinks", {}).get("plwiki") or {}
    wiki = site.get("url", "")
    if not wiki and site.get("title"):
        title = urllib.parse.quote(site["title"].replace(" ", "_"))
        wiki = f"https://pl.wikipedia.org/wiki/{title}"
    return {
        "label": label.strip(),
        "aliases": [a.strip() for a in aliases if a.strip()],
        "description": description.strip(),
        "wiki": wiki,
    }
