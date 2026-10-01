import json

import pytest
from django.test import Client

from library.models import Author, Document
from rag.citations import (
    build_entries,
    canonical_name,
    cited_numbers,
    to_bibtex,
    to_ris,
)
from rag.quotes import check_citations

pytestmark = pytest.mark.django_db


def test_cited_numbers_and_names():
    lit, pat, ane = cited_numbers(
        "Tak [1], por. [2, 4] i [6-7]; nie [999-1999]; Ojcowie [P1] i [P2, P3]; ANE [A2]"
    )
    assert lit == {1, 2, 4, 6, 7} and pat == {1, 2, 3} and ane == {2}
    assert (
        canonical_name("Marcin Majewski")
        == "Majewski, Marcin"
        == canonical_name("Majewski, Marcin")
    )
    assert canonical_name("Justyn") == "Justyn"


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("[P1, 2]", (set(), {1, 2}, set())),
        ("[P1; P2]", (set(), {1, 2}, set())),
        ("[1—2]", ({1, 2}, set(), set())),
        ("[ P1 ; 2–3 ]", (set(), {1, 2, 3}, set())),
        ("[A1, 2—3]", (set(), set(), {1, 2, 3})),
        ("[P1, A2; 3]", (set(), {1}, {2, 3})),
        ("[1; P2, 3]", ({1}, {2, 3}, set())),
        ("[P1-P3] [A2–A3]", (set(), {1, 2, 3}, {2, 3})),
        ("[P1, 2] [A1] [3]", ({3}, {1, 2}, {1})),
        ("[1—200]", (set(range(1, 201)), set(), set())),
        ("[1—201]", (set(), set(), set())),
        ("[P1—A2]", (set(), set(), set())),
        ("[1—P2]", (set(), set(), set())),
        ("[P3—1]", (set(), set(), set())),
        ("[P1, typo]", (set(), set(), set())),
        ("[P1;]", (set(), set(), set())),
        ("[P1, typo] [A2]", (set(), set(), {2})),
        ("[P 1]", (set(), set(), set())),
        ("[" + "9" * 5000 + "]", (set(), set(), set())),
    ],
)
def test_cited_numbers_share_quote_parser_grammar(answer, expected):
    assert cited_numbers(answer) == expected
    labels = {
        f"[{prefix}{n}]"
        for prefix, numbers in zip(("", "P", "A"), expected, strict=True)
        for n in numbers
    }
    parsed = {
        ref
        for marker in check_citations(answer, labels)
        if marker.reason in ("", "missing_source")
        for ref in marker.refs
    }
    assert parsed == labels


@pytest.fixture
def numbered_export_sources():
    documents = [Document.objects.create(title=f"Literatura{n}") for n in range(1, 4)]
    return {
        "chunks": [{"n": n, "document_id": d.pk} for n, d in enumerate(documents, 1)],
        "patristics": [
            {"author": f"Ojciec{n}", "work": f"Pat{n}"} for n in range(1, 4)
        ],
        "ane": [{"text": f"ANE{n}"} for n in range(1, 4)],
    }


@pytest.mark.parametrize(
    "answer,titles",
    [
        ("[P1, 2]", {"Pat1", "Pat2"}),
        ("[P1; P2]", {"Pat1", "Pat2"}),
        ("[1—2]", {"Literatura1", "Literatura2"}),
        ("[A1, 2—3]", {"ANE1", "ANE2", "ANE3"}),
        ("[P1; A2, 3]", {"Pat1", "ANE2", "ANE3"}),
        ("[P1, typo] [A2]", {"ANE2"}),
        ("[P1—A2] [2]", {"Literatura2"}),
    ],
)
def test_exports_only_selected_families(numbered_export_sources, answer, titles):
    entries = build_entries(numbered_export_sources, answer)
    assert {e["title"] for e in entries} == titles
    for exported in (to_bibtex(entries), to_ris(entries)):
        for title in titles:
            assert title in exported
        for title in {
            f"{family}{n}"
            for family in ("Literatura", "Pat", "ANE")
            for n in range(1, 4)
        } - titles:
            assert title not in exported


@pytest.mark.parametrize(
    "answer",
    [
        "[P1, typo]",
        "[P1;]",
        "[P 1]",
        "[P1—A2]",
        "[1—P2]",
        "[2—1]",
        "[1—201]",
        "[9999999999]",
        "[1",
        "[1,\n2]",
        "[niepoprawne]",
        "Odpowiedź bez odsyłaczy.",
    ],
)
def test_nonempty_answer_never_falls_back_to_all_sources(
    numbered_export_sources, answer
):
    assert build_entries(numbered_export_sources, answer) == []


@pytest.mark.parametrize("answer", ["", " \n\t"])
def test_empty_historical_answer_preserves_all_source_fallback(
    numbered_export_sources, answer
):
    assert {e["title"] for e in build_entries(numbered_export_sources, answer)} == {
        f"{family}{n}" for family in ("Literatura", "Pat", "ANE") for n in range(1, 4)
    }


def test_export_bibtex_ris_filters_cited_and_includes_patristics():
    a = Author.objects.create(name="Marcin Majewski", slug="majewski")
    d1 = Document.objects.create(
        title="Przybytek czy Mieszkanie? Hebrajski termin משכן",
        doc_type="article",
        year=2010,
        journal="Polonia Sacra",
        volume="27",
        pages="139-150",
        doi="10.1/x",
        access="open",
    )
    d1.authors.add(a)
    d2 = Document.objects.create(
        title="Mieszkanie Chwały",
        doc_type="book",
        year=2008,
        journal="Tyniec",
        access="open",
    )
    a2 = Author.objects.create(
        name="Majewski, Marcin", slug="majewski-marcin"
    )  # dublet w innej formie
    d2.authors.add(a2)
    sources = {
        "chunks": [
            {
                "n": 1,
                "document_id": d1.id,
                "title": d1.title,
                "authors": ["Marcin Majewski"],
                "year": 2010,
                "doc_type": "article",
            },
            {
                "n": 2,
                "document_id": d2.id,
                "title": d2.title,
                "authors": ["Majewski, Marcin"],
                "year": 2008,
                "doc_type": "book",
            },
            {
                "n": 3,
                "document_id": d1.id,
                "title": d1.title,
                "authors": ["Marcin Majewski"],
                "year": 2010,
                "doc_type": "article",
            },
        ],
        "patristics": [
            {
                "author": "Ireneusz z Lyonu",
                "work": "Against Heresies",
                "ref": "Ireneusz z Lyonu, Against Heresies, Book III (ANF 1, s. 451)",
                "url": "https://www.ccel.org/ccel/schaff/anf01.html",
            },
            {
                "author": "Ireneusz z Lyonu",
                "work": "Against Heresies",
                "ref": "Ireneusz z Lyonu, Against Heresies, Book IV (ANF 1, s. 521)",
                "url": "https://www.ccel.org/ccel/schaff/anf01.html",
            },
            {
                "author": "Pseudo-Barnaba",
                "work": "The Epistle of Barnabas",
                "ref": "Pseudo-Barnaba, The Epistle of Barnabas, Chapter V (ANF 1, s. 139)",
                "url": "https://www.ccel.org/ccel/schaff/anf01.html",
            },
        ],
        "ane": [
            {
                "text": "Gilgamesz",
                "url": "https://www.ebl.lmu.de/library/L/1/4",
                "license": "CC BY-NC-SA 4.0",
            }
        ],
    }
    entries = build_entries(
        sources,
        "Termin miszkan [1] oznacza mieszkanie; Ireneusz [P1, P2] i Barnaba [P3]; Gilgamesz [A1].",
    )
    assert [e["type"] for e in entries] == [
        "article",
        "patristic",
        "patristic",
        "ane",
    ]  # [2] nieprzywołane -> pominięte
    bib = to_bibtex(entries)
    assert (
        "@article{majewski2010przybytek," in bib
        and "author = {Majewski, Marcin}" in bib
    )
    assert (
        "journal = {Polonia Sacra}" in bib
        and "pages = {139-150}" in bib
        and "doi = {10.1/x}" in bib
    )
    assert (
        "@incollection{ireneusz1885against," in bib
        and "booktitle = {Ante-Nicene Fathers, vol. 1}" in bib
    )
    assert (
        "editor = {Alexander Roberts and James Donaldson}" in bib
        and "pages = {451, 521}" in bib
    )
    assert (
        "@incollection{anon1885epistle," in bib
        and "author = {Pseudo-Barnaba}" not in bib
        and "note = {Pseudo-Barnaba;" in bib
    )
    assert "@misc{" in bib and "Babylonian" in bib
    ris = to_ris(entries)
    assert (
        "TY  - JOUR" in ris
        and "AU  - Majewski, Marcin" in ris
        and "SP  - 139" in ris
        and "EP  - 150" in ris
    )
    assert (
        "TY  - CHAP" in ris
        and "A2  - Alexander Roberts" in ris
        and "SP  - 451, 521" in ris
    )

    entries_all = build_entries(sources, "")
    assert [e["key"] for e in entries_all][:2] == [
        "majewski2010przybytek",
        "majewski2008mieszkanie",
    ]
    assert entries_all[1]["authors"] == ["Majewski, Marcin"]

    c = Client(HTTP_HOST="localhost")
    r = c.post(
        "/export/citations",
        data=json.dumps({"sources": sources, "answer": "", "format": "ris"}),
        content_type="application/json",
    )
    assert (
        r.status_code == 200
        and r["Content-Disposition"].endswith('"scriptura.ris"')
        and b"TY  - BOOK" in r.content
    )
    r = c.post(
        "/export/citations",
        data=json.dumps({"sources": {}, "format": "bib"}),
        content_type="application/json",
    )
    assert r.status_code == 404


def test_merge_authors_command():
    from io import StringIO

    from django.core.management import call_command

    a1 = Author.objects.create(name="Mariusz Rosik", slug="rosik")
    a2 = Author.objects.create(name="Rosik, Mariusz", slug="rosik-mariusz")
    d1 = Document.objects.create(title="A", doc_type="article", access="open")
    d1.authors.add(a1)
    d2 = Document.objects.create(title="B", doc_type="article", access="open")
    d2.authors.add(a2)
    out = StringIO()
    call_command("merge_authors", "--apply", stdout=out)
    assert Author.objects.count() == 1 and Author.objects.get().name == "Mariusz Rosik"
    assert set(
        Document.objects.get(title="B").authors.values_list("name", flat=True)
    ) == {"Mariusz Rosik"}
