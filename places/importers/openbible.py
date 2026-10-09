"""Parser OpenBible.info Bible Geocoding Data (ancient.jsonl, CC BY 4.0).

Z każdego miejsca bierzemy: nazwę (friendly_id bez numeru rozróżniającego), pisownie
z przekładów EN, klasę i typy, Wikidata, wersety (OSIS -> ordinal) oraz kandydatów
na lokalizację: każde `resolution` z `lonlat`, niezależnie od tego, czy identyfikacja
jest „modern" (wprost miejsce), czy „ancient" (inna nazwa tego samego miejsca, już
rozwiązana w łańcuchu). Identyfikacje „special" (osoba, nie-miejsce) są pomijane.
Ocena: `best_path_score` rozwiązania (głosy źródeł wzdłuż ścieżki identyfikacji);
pewność = udział w sumie ocen kandydatów danego miejsca. Zostaje MAX_LOCATIONS
najlepszych; te same współrzędne liczone raz.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from corpus.importers.xref import ordinal_of

MAX_LOCATIONS = 5
_TAG_RE = re.compile(r"<[^>]+>")
_NUM_SUFFIX_RE = re.compile(r"\s+\d+$")
WIKIDATA_SOURCE = "s7cc8b2"  # id źródła Wikidata w linked_data
OPENBIBLE_URL = "https://www.openbible.info/geo/ancient/{id}/{slug}"


@dataclass
class LocationRow:
    name: str
    lon: float
    lat: float
    geometry: str
    radius_m: int | None
    type: str
    score: int
    confidence: float = 0.0
    modern_id: str = ""


@dataclass
class PlaceRow:
    openbible_id: str
    slug: str
    name: str
    aliases: list[str]
    kind: str
    types: list[str]
    wikidata: str
    url: str
    ordinals: list[int]
    locations: list[LocationRow] = field(default_factory=list)


def strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _geometry(res: dict, ident: dict) -> tuple[str, int | None]:
    lt = res.get("lonlat_type") or "point"
    radius = ident.get("geometry_radius_meters")
    if lt in ("center", "representative point") or ident.get("geometry_id"):
        return "region", int(radius) if radius else None
    if ident.get("modifier") == "along":
        return "path", None
    return "point", int(radius) if radius else None


def parse_place(raw: dict) -> PlaceRow | None:
    ordinals = sorted(
        {
            o
            for v in raw.get("verses", [])
            if (o := ordinal_of(v.get("osis", ""))) is not None
        }
    )
    if not ordinals:  # księgi spoza kanonu / brak odniesień -> poza bazą
        return None
    friendly = raw.get("friendly_id", "") or raw.get("id", "")
    name = _NUM_SUFFIX_RE.sub("", friendly).strip()
    aliases = sorted(
        a for a in (raw.get("translation_name_counts") or {}) if a and a != name
    )
    kinds = {
        i.get("class", "")
        for i in raw.get("identifications", [])
        if i.get("id_source") != "special"
    }
    kind = (
        "human,natural"
        if {"human", "natural"} <= kinds
        else (next(iter(kinds), "") if kinds else "")
    )
    wikidata = ((raw.get("linked_data") or {}).get(WIKIDATA_SOURCE) or {}).get("id", "")

    seen: set[tuple[float, float]] = set()
    locs: list[LocationRow] = []
    for ident in raw.get("identifications", []):
        if ident.get("id_source") == "special":
            continue
        for res in ident.get("resolutions", []):
            lonlat = res.get("lonlat")
            if not lonlat or res.get("special"):
                continue
            try:
                lon, lat = (float(x) for x in lonlat.split(","))
            except ValueError:
                continue
            key = (round(lon, 4), round(lat, 4))
            if key in seen:
                continue
            seen.add(key)
            geometry, radius = _geometry(res, ident)
            locs.append(
                LocationRow(
                    name=strip_tags(
                        res.get("description") or ident.get("description") or name
                    ),
                    lon=lon,
                    lat=lat,
                    geometry=geometry,
                    radius_m=radius,
                    type=res.get("type") or (ident.get("types") or [""])[0],
                    score=int(
                        res.get("best_path_score")
                        or (ident.get("score") or {}).get("vote_total")
                        or 0
                    ),
                    modern_id=res.get("modern_basis_id") or "",
                )
            )
    locs.sort(key=lambda loc: -loc.score)
    locs = locs[:MAX_LOCATIONS]
    total = sum(loc.score for loc in locs) or 1
    for loc in locs:
        loc.confidence = round(loc.score / total, 3)
    slug = raw.get("url_slug") or raw.get("id", "")
    return PlaceRow(
        openbible_id=raw["id"],
        slug=slug,
        name=name,
        aliases=aliases,
        kind=kind,
        types=list(raw.get("types") or []),
        wikidata=wikidata,
        url=OPENBIBLE_URL.format(id=raw["id"], slug=slug),
        ordinals=ordinals,
        locations=locs,
    )


def parse_file(path: Path) -> Iterator[PlaceRow]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = parse_place(json.loads(line))
            if row is not None:
                yield row
