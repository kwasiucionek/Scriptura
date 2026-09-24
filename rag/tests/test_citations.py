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
