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


def test_select_works_splits_treatise_and_keeps_other_works():
    # NPNF2 6: traktat Hieronima jest sekcją działu „Treatises.” — import tylko jego
    from patristics.thml import PassageDraft, WorkDraft

    treatises = WorkDraft(
        title="Treatises.",
        author="",
        passages=[
            PassageDraft(section="Against Jovinianus. / Book I", page="346", text="a"),
            PassageDraft(section="The Perpetual Virginity of Blessed Mary. / 9", page="338", text="b", refs=[(1, 2)]),
            PassageDraft(section="The Perpetual Virginity of Blessed Mary. / 10", page="339", text="c"),
        ],
    )  # fmt: skip
    letters = WorkDraft(title="The Letters of St. Jerome.", author="", passages=[])
    (w,) = importers.select_works([letters, treatises], "perpetual virginity")
    assert w.title == "The Perpetual Virginity of Blessed Mary"
    assert [p.section for p in w.passages] == ["9", "10"] and w.passages[0].refs == [
        (1, 2)
    ]
    assert importers.select_works([letters, treatises], "letters") == [letters]

    importers.save_volume("npnf206", [letters, treatises])
    importers.save_volume("npnf206", [w], replace_volume=False)
    titles = set(
        PatWork.objects.filter(ccel_id="npnf206").values_list("title", flat=True)
    )
    assert titles == {
        "The Letters of St. Jerome.",
        "Treatises.",
        "The Perpetual Virginity of Blessed Mary",
    }
    work = PatWork.objects.get(title="The Perpetual Virginity of Blessed Mary")
    assert (
        work.author == "Jerome"
        and work.author_pl == "Hieronim"
        and work.passages.count() == 2
    )
    importers.save_volume("npnf206", [w])  # import całego tomu nadal zastępuje tom
    assert PatWork.objects.filter(ccel_id="npnf206").count() == 1


def test_select_works_deeper_level_and_author_from_parent():
    # list Cypriana leży dwa poziomy niżej: „Cyprian.” / „The Epistles of Cyprian.” / list
    from patristics.thml import PassageDraft, WorkDraft

    cyprian = WorkDraft(
        title="Cyprian.",
        author="",
        passages=[
            PassageDraft(section="The Epistles of Cyprian. / To Donatus.", page="275", text="a"),
            PassageDraft(section="The Epistles of Cyprian. / To Cornelius, Concerning Fortunatus. / 14", page="344", text="b"),
            PassageDraft(section="The Treatises of Cyprian. / On the Unity of the Church. / 4", page="422", text="c"),
        ],
    )  # fmt: skip
    works = importers.select_works(
        [cyprian],
        r"^On the Unity of the Church\.$|^To Cornelius, Concerning Fortunatus",
    )
    assert [(w.title, [p.section for p in w.passages]) for w in works] == [
        ("To Cornelius, Concerning Fortunatus", ["14"]),
        ("On the Unity of the Church", ["4"]),
    ]
    importers.save_volume("anf05", works, replace_volume=False)
    assert set(PatWork.objects.values_list("author", flat=True)) == {"Cyprian"}


def test_doctrinal_questions_trigger_patristics():
    assert wants_patristics("Czy Jezus miał rodzeństwo? Kim byli bracia Pańscy?")
    assert wants_patristics("Na czym opiera się prymat Piotra?")
    assert wants_patristics("Czy Mt 16,18 mówi o prymacie Piotra?")
    assert wants_patristics("Co Helwidiusz twierdził o Maryi?")
