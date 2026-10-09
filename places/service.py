"""Miejsca dla pytania / sigli: baza powiązana do panelu „Miejsca" i mapy.

Kanał odwrotny do sigli: zakresy ordinali z pytania -> PlaceRef -> Place z kandydatami
na lokalizację. Drugi kanał: nazwa miejsca w treści pytania („gdzie leżał Charan”) —
dopasowanie po name/name_pl/aliasach jako całych słowach, z prostym odmianowaniem
(prefiks ≥ 4 znaków), bez LLM. Wynik to zwykłe dict-y (JSON do SSE i do historii rozmów).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from django.conf import settings
from django.db.models import Q

from corpus.books import BOOKS
from corpus.models import Verse
from places.models import Place, PlaceLocation, PlaceRef

ATTRIBUTION = "OpenBible.info Bible Geocoding Data, CC BY 4.0"


@dataclass
class LocationOut:
    name: str
    lon: float
    lat: float
    confidence: float
    geometry: str = "point"
    radius_m: int | None = None
    type: str = ""


@dataclass
class PlaceOut:
    id: int
    name: str  # etykieta (PL, gdy jest)
    name_en: str
    kind: str
    types: list[str]
    refs: list[str]  # sigla wystąpień w zakresie pytania (do ~6)
    verse_count: int  # wszystkie wystąpienia w Biblii
    url: str
    wikidata: str = ""
    locations: list[LocationOut] = field(
        default_factory=list
    )  # [0] = najbardziej prawdopodobna
    via: str = "verse"  # verse | name


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


@lru_cache(maxsize=1)
def _name_index() -> list[tuple[str, int]]:
    """(nazwa po foldzie, place_id) dla name, name_pl i aliasów; tylko miejsca z lokalizacją."""
    out: list[tuple[str, int]] = []
    qs = (
        Place.objects.filter(locations__isnull=False)
        .distinct()
        .values_list("id", "name", "name_pl", "aliases")
    )
    for pid, name, name_pl, aliases in qs:
        for n in {name, name_pl, *(aliases or [])}:
            if n and len(n) >= 4:
                out.append((_fold(n), pid))
    return out


def reset_name_cache() -> None:
    _name_index.cache_clear()


_BY_ORDER = {b.order: b for b in BOOKS}


def _ref_label(ordinal: int, verses: dict[int, Verse]) -> str:
    """Siglum z bazy, a gdy wersetu nie ma w korpusie — z samego ordinalu (order·10^6+ch·10^3+v)."""
    v = verses.get(ordinal)
    if v:
        return str(v)
    book = _BY_ORDER.get(ordinal // 1_000_000)
    ch, vs = (ordinal // 1_000) % 1_000, ordinal % 1_000
    return f"{book.abbr} {ch},{vs}" if book else str(ordinal)


def _pack(
    places: list[Place], refs_by_place: dict[int, list[int]], via: str
) -> list[PlaceOut]:
    ordinals = sorted({o for lst in refs_by_place.values() for o in lst[:6]})
    verses = {
        v.ordinal: v
        for v in Verse.objects.filter(ordinal__in=ordinals).select_related("book")
    }
    locs: dict[int, list[PlaceLocation]] = {}
    for loc in PlaceLocation.objects.filter(place__in=places).order_by(
        "place_id", "order"
    ):
        locs.setdefault(loc.place_id, []).append(loc)
    out: list[PlaceOut] = []
    for p in places:
        if not locs.get(p.id):
            continue  # miejsce bez współrzędnych nie trafia na mapę
        out.append(
            PlaceOut(
                id=p.id,
                name=p.label,
                name_en=p.name,
                kind=p.kind,
                types=list(p.types or []),
                refs=[_ref_label(o, verses) for o in refs_by_place.get(p.id, [])[:6]],
                verse_count=p.verse_count,
                url=p.url,
                wikidata=p.wikidata,
                locations=[
                    LocationOut(
                        name=loc.name,
                        lon=loc.lon,
                        lat=loc.lat,
                        confidence=loc.confidence,
                        geometry=loc.geometry,
                        radius_m=loc.radius_m,
                        type=loc.type,
                    )  # fmt: skip
                    for loc in locs[p.id]
                ],
                via=via,
            )
        )
    return out


def places_for_ranges(ranges: list[tuple[int, int]], limit: int = 12) -> list[PlaceOut]:
    """Miejsca wymienione w wersetach z zakresów, w kolejności pierwszego wystąpienia."""
    if not ranges or limit <= 0:
        return []
    q = Q()
    for a, b in ranges:
        q |= Q(ordinal__gte=a, ordinal__lte=b)
    refs_by_place: dict[int, list[int]] = {}
    order: list[int] = []
    for ref in (
        PlaceRef.objects.filter(q)
        .order_by("ordinal")
        .values_list("place_id", "ordinal")
    ):
        pid, ordinal = ref
        if pid not in refs_by_place:
            refs_by_place[pid] = []
            order.append(pid)
        refs_by_place[pid].append(ordinal)
    if not order:
        return []
    places = {p.id: p for p in Place.objects.filter(id__in=order)}
    ordered = [places[pid] for pid in order if pid in places]
    return _pack(ordered, refs_by_place, "verse")[:limit]


_WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)


def places_for_names(question: str, limit: int = 6) -> list[PlaceOut]:
    """Miejsca, których nazwa (PL/EN/alias) pada w pytaniu: słowo równe nazwie albo nazwa + końcówka
    fleksyjna do 3 znaków („Charanu”, „Betelem”, „Sychemie”). Przy kilku miejscach o tej samej
    nazwie (Betel 1, 2, 3) zostają najwyżej dwa najczęstsze w Biblii."""
    words = {_fold(w) for w in _WORD_RE.findall(question)}
    if not words:
        return []
    hits: dict[int, int] = {}
    for name, pid in _name_index():
        for w in words:
            if w == name or (w.startswith(name) and len(w) - len(name) <= 3):
                hits[pid] = max(hits.get(pid, 0), len(name))
    if not hits:
        return []
    places = list(Place.objects.filter(id__in=hits).order_by("-verse_count"))
    per_name: dict[str, int] = {}
    picked: list[Place] = []
    for p in places:
        if per_name.get(p.name, 0) >= 2:
            continue
        per_name[p.name] = per_name.get(p.name, 0) + 1
        picked.append(p)
        if len(picked) >= limit:
            break
    refs_by_place = {
        p.id: list(
            PlaceRef.objects.filter(place=p)
            .order_by("ordinal")
            .values_list("ordinal", flat=True)[:6]
        )
        for p in picked
    }
    return _pack(picked, refs_by_place, "name")


def places_for_question(question: str, ranges: list[tuple[int, int]]) -> list[dict]:
    """Panel „Miejsca”: najpierw miejsca z wersetów pytania, potem wymienione z nazwy."""
    limit = int(getattr(settings, "RAG_PLACES", 12) or 0)
    if limit <= 0:
        return []
    out = places_for_ranges(ranges, limit)
    seen = {p.id for p in out}
    for p in places_for_names(question):
        if p.id not in seen and len(out) < limit:
            out.append(p)
            seen.add(p.id)
    return [asdict(p) for p in out]
