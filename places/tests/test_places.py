"""Geografia biblijna: parser OpenBible, serwis miejsc dla sigli i nazw, kanał `places` w /ask/stream."""

import json
import re

import pytest
from django.test import Client

from corpus.books import BY_OSIS
from places.importers import openbible
from places.models import Place, PlaceLocation, PlaceRef
from places.service import (
    places_for_names,
    places_for_question,
    places_for_ranges,
    reset_name_cache,
)

GEN = BY_OSIS["Gen"].order * 1_000_000
MARK = BY_OSIS["Mark"].order * 1_000_000

RAW_BETHEL = {
    "id": "a64f355",
    "friendly_id": "Bethel 1",
    "url_slug": "bethel-1",
    "types": ["settlement"],
    "translation_name_counts": {"Bethel": 560, "Beth-el": 57},
    "linked_data": {"s7cc8b2": {"id": "Q3876767"}},
    "verses": [{"osis": "Gen.12.8"}, {"osis": "Gen.13.3"}, {"osis": "1Esd.1.1"}],
    "identifications": [
        {
            "id_source": "modern",
            "class": "human",
            "description": '<modern id="me58522">Beitin</modern>',
            "resolutions": [
                {"lonlat": "35.241389,31.922778", "lonlat_type": "point", "best_path_score": 900, "type": "settlement", "modern_basis_id": "me58522"}
            ],
        },
        {
            "id_source": "modern",
            "class": "human",
            "description": '<modern id="mebe332">Al Bira</modern>',
            "resolutions": [
                {"lonlat": "35.214958,31.905142", "lonlat_type": "point", "best_path_score": 100, "type": "settlement", "modern_basis_id": "mebe332"}
            ],
        },
        {"id_source": "special", "class": "special", "description": "not a place", "resolutions": [{"special": "not_a_place"}]},
    ],
}  # fmt: skip

RAW_PERSON = {
    "id": "a95b373",
    "friendly_id": "Addon",
    "url_slug": "addon",
    "verses": [{"osis": "Ezra.2.59"}],
    "identifications": [{"id_source": "special", "class": "special", "resolutions": [{"special": "not_a_place"}]}],
}  # fmt: skip


def test_parse_place_scores_confidence_and_skips_special():
    row = openbible.parse_place(RAW_BETHEL)
    assert (
        row.name == "Bethel" and row.slug == "bethel-1" and row.wikidata == "Q3876767"
    )
    assert row.aliases == ["Beth-el"]
    assert row.ordinals == [
        GEN + 12_008,
        GEN + 13_003,
    ]  # 1 Ezd poza kanonem -> pominięty
    assert [loc.name for loc in row.locations] == ["Beitin", "Al Bira"]
    assert row.locations[0].confidence == 0.9 and row.locations[1].confidence == 0.1
    assert row.url.endswith("/a64f355/bethel-1")
    person = openbible.parse_place(RAW_PERSON)
    assert (
        person is not None and person.locations == []
    )  # bez współrzędnych: zostaje poza mapą


@pytest.fixture
def places_db():
    b = Place.objects.create(
        openbible_id="a64f355", slug="bethel-1", name="Bethel", name_pl="Betel",
        aliases=["Beth-el"], kind="human", types=["settlement"], verse_count=69,
        url="https://www.openbible.info/geo/ancient/a64f355/bethel-1",
    )  # fmt: skip
    PlaceLocation.objects.create(
        place=b,
        order=0,
        name="Beitin",
        lon=35.2414,
        lat=31.9228,
        score=900,
        confidence=0.9,
    )
    PlaceLocation.objects.create(
        place=b,
        order=1,
        name="Al Bira",
        lon=35.215,
        lat=31.905,
        score=100,
        confidence=0.1,
    )
    PlaceRef.objects.create(place=b, ordinal=GEN + 12_008)
    PlaceRef.objects.create(place=b, ordinal=GEN + 13_003)
    h = Place.objects.create(
        openbible_id="a1",
        slug="haran",
        name="Haran",
        name_pl="Charan",
        kind="human",
        verse_count=12,
    )
    PlaceLocation.objects.create(
        place=h, order=0, name="Harran", lon=39.03, lat=36.86, score=50, confidence=1.0
    )
    PlaceRef.objects.create(place=h, ordinal=GEN + 12_004)
    noloc = Place.objects.create(
        openbible_id="a2", slug="x", name="Nowhere", verse_count=1
    )
    PlaceRef.objects.create(place=noloc, ordinal=GEN + 12_005)
    reset_name_cache()
    yield
    reset_name_cache()


@pytest.mark.django_db
def test_places_for_ranges_orders_by_first_verse_and_skips_unlocated(places_db):
    out = places_for_ranges([(GEN + 12_001, GEN + 12_009)])
    assert [p.name for p in out] == [
        "Charan",
        "Betel",
    ]  # etykieta PL; Nowhere bez współrzędnych odpada
    assert out[1].refs == ["Rdz 12,8"]  # siglum z ordinalu, gdy brak wersetu w korpusie
    assert out[1].locations[0].name == "Beitin" and len(out[1].locations) == 2
    assert out[1].via == "verse"


@pytest.mark.django_db
def test_places_for_names_matches_polish_inflection(places_db):
    names = [
        p.name
        for p in places_for_names("Dlaczego Abraham wyruszył z Charanu do Betelu?")
    ]
    assert names == ["Betel", "Charan"]
    assert places_for_names("Czym jest hesed w Psalmach?") == []


@pytest.mark.django_db
def test_places_for_question_merges_channels(places_db, settings):
    out = places_for_question("Co znaczy Charan?", [(GEN + 12_008, GEN + 12_008)])
    assert [p["name"] for p in out] == ["Betel", "Charan"] and out[1]["via"] == "name"
    settings.RAG_PLACES = 0
    assert places_for_question("Charan", [(GEN + 12_008, GEN + 12_008)]) == []


@pytest.mark.django_db
def test_ask_stream_sources_include_places(places_db):
    PlaceRef.objects.create(
        place=Place.objects.get(slug="bethel-1"), ordinal=MARK + 16_009
    )
    c = Client(HTTP_HOST="localhost")
    resp = c.post(
        "/ask/stream",
        data=json.dumps({"question": "Co wiadomo o Mk 16,9-20?"}),
        content_type="application/json",
    )
    body = b"".join(resp.streaming_content).decode()
    events = [
        (e, json.loads(d)) for e, d in re.findall(r"event: (\w+)\ndata: (.*)\n", body)
    ]
    src = next(d for e, d in events if e == "sources")
    assert [p["name"] for p in src["places"]] == ["Betel"]
    assert src["places"][0]["locations"][0]["lat"] == pytest.approx(31.9228)


@pytest.mark.django_db
def test_map_page_and_data(places_db, client):
    r = client.get("/mapa/dane.json")
    names = [p["name"] for p in r.json()["places"]]
    assert r.status_code == 200 and names == [
        "Betel",
        "Charan",
    ]  # bez miejsc bez lokalizacji
    r = client.get("/mapa/?q=Rdz%2012,1-9")
    body = r.content.decode()
    assert (
        r.status_code == 200
        and "Miejsca: Rdz 12,1-9" in body
        and '"name": "Charan"' in body
    )
    haran_id = Place.objects.get(slug="haran").id
    r = client.get(f"/mapa/?ids={haran_id},999999")
    assert r.status_code == 200 and '"name": "Charan"' in r.content.decode()
    assert 'aria-current="page"' in client.get("/mapa/").content.decode()
