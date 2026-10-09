"""Duża mapa: wszystkie miejsca z lokalizacją + wybór początkowy z ?q= (sigla) albo ?ids=.

Dane całej mapy (ok. 1300 punktów) idą jednym JSON-em z cache (godzina) — filtrowanie
i wyszukiwanie po nazwie dzieje się w przeglądarce.
"""

from dataclasses import asdict

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from corpus.sigla import extract, format_ref, ordinal_range
from places.models import Place, PlaceLocation
from places.service import ATTRIBUTION, places_for_ranges

DATA_TTL = 3600


def _all_places() -> list[dict]:
    data = cache.get("places:map-data")
    if data:
        return data
    locs: dict[int, list[PlaceLocation]] = {}
    for loc in PlaceLocation.objects.order_by("place_id", "order"):
        locs.setdefault(loc.place_id, []).append(loc)
    data = []
    for p in Place.objects.filter(id__in=locs).order_by("name"):
        data.append(
            {
                "id": p.id,
                "name": p.label,
                "name_en": p.name,
                "aliases": p.aliases,
                "kind": p.kind,
                "types": p.types,
                "verse_count": p.verse_count,
                "url": p.url,
                "wikidata": p.wikidata,
                "locations": [
                    {
                        "name": loc.name,
                        "lon": loc.lon,
                        "lat": loc.lat,
                        "confidence": loc.confidence,
                        "geometry": loc.geometry,
                        "radius_m": loc.radius_m,
                        "type": loc.type,
                    }  # fmt: skip
                    for loc in locs[p.id]
                ],
            }
        )
    cache.set("places:map-data", data, DATA_TTL)
    return data


def map_data(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"places": _all_places(), "attribution": ATTRIBUTION})


def map_page(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()
    ids = [int(x) for x in request.GET.get("ids", "").split(",") if x.strip().isdigit()]
    selected: list[dict] = []
    title = ""
    if q:
        refs = [r for m in extract(q) for r in m.refs]
        if refs:
            title = "; ".join(format_ref(r) for r in refs)
            selected = [
                asdict(p)
                for p in places_for_ranges([ordinal_range(r) for r in refs], limit=60)
            ]
    elif ids:
        by_id = {p["id"]: p for p in _all_places()}
        selected = [by_id[i] for i in ids if i in by_id]
    return render(
        request,
        "places/map.html",
        {
            "active": "map",
            "q": q,
            "title": title,
            "selected": selected,
            "selected_ids": [p["id"] for p in selected],
            "total": Place.objects.filter(locations__isnull=False).distinct().count(),
        },
    )
