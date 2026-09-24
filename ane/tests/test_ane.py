import json
import re

import pytest
from django.test import Client

from ane import importers
from ane.models import AnePassage, AneText
from ane.service import wants_ane

pytestmark = pytest.mark.django_db

CHAPTER = {
    "stage": "Standard Babylonian",
    "name": "XI",
    "title": [{"text": "The Flood", "type": "StringPart"}],
    "lines": [
        {
            "number": "11",
            "variants": [{"reconstruction": "%n šurippak | ālu || ša tīdûšu | attā"}],
            "translation": "#tr.en: The city of Šuruppak – a city you yourself know,",
        },
        {
            "number": "21",
            "variants": [{"reconstruction": "%n kikkiš | kikkiš || igār | igār"}],
            "translation": '// cf. L I.1 OB "III" 21\n#tr.en: “Reed fence, reed fence! Brick wall, brick wall!',
        },
        {"number": "22", "variants": [{"reconstruction": ""}], "translation": ""},
    ]
    + [
        {
            "number": str(30 + i),
            "variants": [{"reconstruction": f"%n linia {i}"}],
            "translation": f"#tr.en: Build a boat, line {i}, abandon wealth and seek life.",
        }
        for i in range(14)
    ],
}


def _events(resp):
    body = b"".join(resp.streaming_content).decode()
    return [
        (e, json.loads(d)) for e, d in re.findall(r"event: (\w+)\ndata: (.*)\n", body)
    ]


def test_parse_chapter_and_passages():
    draft = importers.parse_chapter(CHAPTER)
    assert (
        draft.stage == "Standard Babylonian"
        and draft.name == "XI"
        and draft.title == "The Flood"
    )
    assert len(draft.lines) == 16  # pusta linia 22 pominięta
    assert draft.lines[0].normalized == "šurippak ālu ša tīdûšu attā"
    assert draft.lines[1].translation_en.startswith("“Reed fence") and draft.lines[
        1
    ].note.startswith("cf. L I.1")
    text = importers.save_text(
        {"name": "Poem of Gilgameš", "intro": "x"},
        [draft],
        "L/1/4",
        name_pl="Gilgamesz",
    )
    assert str(text) == "Gilgamesz" and text.chapters.count() == 1
    passages = list(AnePassage.objects.all())
    assert (
        len(passages) == 2
        and passages[0].ref == "Gilgamesz SB XI 11–39"
        and "(11) The city of Šuruppak" in passages[0].translation_en
    )


def test_triggers_and_rag_section(settings):
    settings.SEARCH_BACKEND = "db"
    assert (
        wants_ane("Jak Gilgamesz opisuje potop?")
        and wants_ane("Czy Enuma Elisz wpłynęło na Rdz 1?")
        and wants_ane("kim była Tiamat")
    )
    assert not wants_ane("Co znaczy miszkan w Wj 40?") and not wants_ane(
        "Jak Majewski komentuje Rdz 1?"
    )
    draft = importers.parse_chapter(CHAPTER)
    importers.save_text(
        {"name": "Poem of Gilgameš"}, [draft], "L/1/4", name_pl="Gilgamesz"
    )
    AnePassage.objects.update(
        translation_pl="Zbuduj łódź, porzuć bogactwa, szukaj życia — potop nadchodzi."
    )
    c = Client(HTTP_HOST="localhost")
    ev = _events(
        c.post(
            "/ask/stream",
            data=json.dumps(
                {"question": "Jak Gilgamesz opisuje budowę łodzi i potop?"}
            ),
            content_type="application/json",
        )
    )
    ane = ev[0][1]["ane"]
    assert ane and ane[0]["text"] == "Gilgamesz" and "SB XI" in ane[0]["ref"]
    assert ev[-1][1]["refused"] is False  # sam pasaż ANE wystarcza, żeby nie odmawiać
    ev2 = _events(
        c.post(
            "/ask/stream",
            data=json.dumps({"question": "budowa łodzi i potop", "include_ane": False}),
            content_type="application/json",
        )
    )
    assert ev2[0][1]["ane"] == []
    ev3 = _events(
        c.post(
            "/ask/stream",
            data=json.dumps({"question": "budowa łodzi i potop", "include_ane": True}),
            content_type="application/json",
        )
    )
    assert ev3[0][1]["ane"]
    assert AneText.objects.count() == 1
