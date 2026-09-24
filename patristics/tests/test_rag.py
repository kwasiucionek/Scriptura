import json
import re

import pytest
from django.test import Client

from corpus.books import BOOKS
from patristics import importers
from patristics.models import PatPassage, PatRef, PatWork
from patristics.service import wants_patristics
from patristics.tests.test_thml import THML
from patristics.thml import parse_volume

pytestmark = pytest.mark.django_db
ORD = {b.osis: b.order for b in BOOKS}


def _events(resp):
    body = b"".join(resp.streaming_content).decode()
    return [
        (e, json.loads(d)) for e, d in re.findall(r"event: (\w+)\ndata: (.*)\n", body)
    ]


def _ask(c, q, **kw):
    return _events(
        c.post(
            "/ask/stream",
            data=json.dumps({"question": q, **kw}),
            content_type="application/json",
        )
    )


def test_save_volume_and_verse_channel(settings):
    settings.SEARCH_BACKEND = "db"
    works = parse_volume(THML, max_chars=200)
    nw, npass = importers.save_volume("anf01", works)
    assert nw == 1 and npass >= 2
    w = PatWork.objects.get()
    assert (
        w.author == "Irenaeus"
        and w.author_pl == "Ireneusz z Lyonu"
        and w.series == "ANF"
        and w.volume == 1
    )
    assert PatRef.objects.filter(start=ORD["Gen"] * 10**6 + 1026).exists()
    p = PatPassage.objects.first()
    assert (
        p.ref.startswith("Ireneusz z Lyonu, Against Heresies, Book III / Chapter XXI")
        and "(ANF 1, s. 451)" in p.ref
    )

    assert wants_patristics(
        "Jak Ojcowie Kościoła rozumieli Rdz 1,26?"
    ) and wants_patristics("Co Orygenes mówi o alegorii?")
    assert not wants_patristics("Co znaczy miszkan w Wj 40?")

    c = Client(HTTP_HOST="localhost")
    # pytanie z siglum -> kanał po wersecie (via_verse), bo pytanie wymienia Ojców
    ev = _ask(c, "Jak Ojcowie Kościoła komentowali Rdz 1,26?")
    pats = ev[0][1]["patristics"]
    assert pats and pats[0]["via_verse"] and pats[0]["author"] == "Ireneusz z Lyonu"
    assert ev[-1][1]["refused"] is False
    # bez wyzwalacza i bez opcji — nic; z opcją — kanał po wersecie działa nawet bez słowa „Ojcowie”
    assert _ask(c, "Co mówi Rdz 1,26?")[0][1]["patristics"] == []
    assert _ask(c, "Co mówi Rdz 1,26?", include_patristics=True)[0][1]["patristics"]
    assert (
        _ask(c, "Jak Ojcowie rozumieli Rdz 1,26?", include_patristics=False)[0][1][
            "patristics"
        ]
        == []
    )


def test_guess_author():
    cands = importers.VOLUMES["anf01"][2]
    assert importers.guess_author("Irenaeus Against Heresies", cands, "") == "Irenaeus"
    assert (
        importers.guess_author("The Epistles of Ignatius", cands, "Polycarp")
        == "Ignatius"
    )
    assert importers.guess_author("Fragments", cands, "Papias") == "Papias"
    assert importers.guess_author("Anything", ["Augustine"], "") == "Augustine"
