"""Testy harvestu na utrwalonych kształtach odpowiedzi DSpace 7 i OpenAlex (bez sieci)."""

from unittest import mock

from library.harvest import dspace, openalex
from library.harvest.manifest import ManifestEntry, normalize_license, read, write

DSPACE_ITEM = {
    "uuid": "c1e3cedc-dcca-44e9-9484-656a52a4568b",
    "metadata": {
        "dc.title": [
            {
                "value": "Ugarit – historia, religia, literatura, język na tle Biblii Starego Testamentu"
            }
        ],
        "dc.contributor.author": [{"value": "Majewski, Marcin"}],
        "dc.date.issued": [{"value": "2010"}],
        "dc.publisher": [{"value": "Wydawnictwo Naukowe UPJPII"}],
        "dc.type": [{"value": "book"}],
        "dc.rights": [
            {"value": "Attribution-NonCommercial-NoDerivatives 4.0 International"}
        ],
        "dc.rights.uri": [
            {"value": "http://creativecommons.org/licenses/by-nc-nd/4.0/"}
        ],
        "dc.language.iso": [{"value": "pl"}],
        "dc.description.abstract": [
            {"value": "Odkrycie w 1928 roku na tellu Ras Szamra…"}
        ],
    },
}
OPENALEX_WORK = {
    "id": "https://openalex.org/W123",
    "display_name": "Trudności translacyjne w Rdz 1",
    "publication_year": 2019,
    "type": "article",
    "language": "pl",
    "doi": "https://doi.org/10.15633/rbl.1234",
    "authorships": [{"author": {"display_name": "Marcin Majewski"}}],
    "open_access": {"is_oa": True, "oa_status": "gold"},
    "primary_location": {
        "license": "cc-by",
        "landing_page_url": "https://czasopisma.upjp2.edu.pl/rbl/1234",
        "source": {"display_name": "Ruch Biblijny i Liturgiczny"},
    },
    "best_oa_location": {
        "pdf_url": "https://czasopisma.upjp2.edu.pl/rbl/1234/pdf",
        "license": "cc-by",
    },
}


def test_normalize_license():
    assert normalize_license("http://creativecommons.org/licenses/by-nc-nd/4.0/") == (
        "CC BY-NC-ND 4.0",
        "open",
    )
    assert normalize_license("cc-by") == ("CC BY", "open")
    assert normalize_license("CC BY-SA 4.0") == ("CC BY-SA 4.0", "open")
    assert normalize_license("Wszelkie prawa zastrzeżone") == (
        "Wszelkie prawa zastrzeżone",
        "licensed",
    )
    assert normalize_license("") == ("", "licensed")


def test_dspace_item_to_entry():
    e = dspace.item_to_entry(
        "https://open.icm.edu.pl", DSPACE_ITEM, pdf_url="https://x/content"
    )
    assert e.source_id == "c1e3cedc-dcca-44e9-9484-656a52a4568b"
    assert e.authors == ["Majewski, Marcin"] and e.year == 2010 and e.doc_type == "book"
    assert e.license == "CC BY-NC-ND 4.0" and e.access == "open"
    assert e.url.endswith("/items/c1e3cedc-dcca-44e9-9484-656a52a4568b")


def test_dspace_pdf_bitstream_url():
    bundles = {
        "_embedded": {
            "bundles": [
                {"name": "LICENSE", "uuid": "l"},
                {"name": "ORIGINAL", "uuid": "o"},
            ]
        }
    }
    bits = {
        "_embedded": {
            "bitstreams": [
                {"uuid": "b1", "name": "okladka.jpg"},
                {"uuid": "b2", "name": "ugarit.pdf"},
            ]
        }
    }
    with mock.patch.object(dspace, "_get", side_effect=[bundles, bits]):
        assert (
            dspace.pdf_bitstream_url("https://h", "item")
            == "https://h/server/api/core/bitstreams/b2/content"
        )


def test_openalex_work_to_entry():
    e = openalex.work_to_entry(OPENALEX_WORK)
    assert e.doi == "10.15633/rbl.1234" and e.journal == "Ruch Biblijny i Liturgiczny"
    assert e.license == "CC BY" and e.access == "open" and e.pdf_url.endswith("/pdf")


def test_manifest_roundtrip(tmp_path):
    entries = [
        ManifestEntry("manual", "x", "Tytuł", authors=["A"], year=2020, access="skip")
    ]
    write(tmp_path / "m.jsonl", entries)
    assert read(tmp_path / "m.jsonl") == entries


BIB = """@book{majewski2010ugarit,
  title={Ugarit -- historia, religia, literatura, j{\\k{e}}zyk na tle Biblii Starego Testamentu},
  author={Majewski, Marcin},
  year={2010},
  publisher={Wydawnictwo Naukowe UPJPII}
}

@article{majewski2019rdz,
  title={Trudno{\\'s}ci translacyjne w Rdz 1},
  author={Majewski, Marcin and Kowalski, Jan},
  journal={Ruch Biblijny i Liturgiczny},
  volume={72},
  year={2019}
}
"""


def test_bibtex_parse():
    from library.harvest import bibtex

    entries = bibtex.parse(BIB)
    assert [e.doc_type for e in entries] == ["book", "article"]
    assert entries[0].authors == ["Marcin Majewski"] and entries[0].year == 2010
    assert entries[1].authors == ["Marcin Majewski", "Jan Kowalski"]
    assert entries[1].journal == "Ruch Biblijny i Liturgiczny"
    assert entries[0].access == "licensed" and entries[0].pdf_url == ""


def test_dspace_browse_author():
    page = {"_embedded": {"items": [DSPACE_ITEM]}}
    with mock.patch.object(dspace, "_get", return_value=page):
        items = dspace.browse_author_items("https://h", "Majewski, Marcin")
    assert items[0]["uuid"] == DSPACE_ITEM["uuid"]


def test_scholar_article_to_entry():
    from library.harvest import scholar

    a = {
        "title": "Masora Biblii Hebrajskiej",
        "authors": "M Majewski",
        "citation_id": "r0NgMwMAAAAJ:abc",
        "publication": "Ruch Biblijny i Liturgiczny 72 (3), 245-260, 2019",
        "year": "2019",
        "link": "https://scholar.google.com/x",
        "cited_by": {"value": 4},
    }
    e = scholar.article_to_entry(a, "Marcin Majewski")
    assert (
        e.journal == "Ruch Biblijny i Liturgiczny"
        and e.year == 2019
        and e.doc_type == "article"
    )
    assert e.access == "licensed" and e.pdf_url == ""
    b = scholar.article_to_entry(
        {"title": "Ugarit", "publication": "Petrus, 2010", "year": "2010"},
        "Marcin Majewski",
    )
    assert b.doc_type == "book" and b.authors == ["Marcin Majewski"]


def test_openalex_author_id_normalization():
    with mock.patch.object(
        openalex, "_get", return_value={"results": [], "meta": {}}
    ) as g:
        openalex.works_for_author("https://openalex.org/A5031058846")
        assert "author.id:A5031058846" in g.call_args[0][1]["filter"]
    import pytest

    with pytest.raises(ValueError):
        openalex.works_for_author("A……")


def test_title_dedup(db):
    from library.management.commands.ingest_manifest import _title_exists
    from library.models import Document

    Document.objects.create(
        title="Jak przekłady zmieniają Biblię. O przekładach i przekładaniu Pisma Świętego"
    )
    assert _title_exists("Jak przekłady zmieniają Biblię")
    assert _title_exists("Jak przeklady zmieniaja Biblie. O teorii i praktyce")
    assert not _title_exists("Ugarit – historia, religia, literatura")


def test_blog_post_to_entry(tmp_path):
    from library.harvest import blog

    post = {
        "id": 42,
        "date": "2025-09-26T19:30:06",
        "modified": "2025-10-21T11:33:11",
        "slug": "70-imion-boga",
        "link": "https://majewskimarcin.pl/baza-wiedzy/70-imion-boga/",
        "title": {"rendered": "70 imion Boga &#8211; test"},
        "excerpt": {"rendered": "<p>Pytanie o imię Boga jest kluczowym pytaniem.</p>"},
        "content": {
            "rendered": (
                "<script>x()</script><p><strong>Mojżesz zapytał</strong> (Wj 3,13).</p>"
                "<h2><strong>Siedem imion Boga</strong></h2><p>El, Elohim, Adonaj.</p>"
                "<blockquote><p>„Święty powiedział…” (<em>Exodus Rabba</em> 3,6).</p></blockquote>"
                "<ul><li>Echad</li><li>Adir</li></ul><nav>menu</nav>"
            )
        },
    }
    e = blog.post_to_entry(
        post, "Marcin Majewski", tmp_path, "https://majewskimarcin.pl"
    )
    assert (
        e.title == "70 imion Boga – test"
        and e.doc_type == "blog"
        and e.register == "popular"
    )
    assert (
        e.access == "licensed" and e.year == 2025 and e.journal == "majewskimarcin.pl"
    )
    text = open(e.local_file, encoding="utf-8").read()
    assert text.startswith("# 70 imion Boga – test\n\nMojżesz zapytał (Wj 3,13).")
    assert (
        "## Siedem imion Boga" in text
        and "> „Święty powiedział…” (Exodus Rabba 3,6)." in text
    )
    assert "- Echad\n- Adir" in text and "x()" not in text and "menu" not in text
    assert e.abstract.startswith("Pytanie o imię Boga")
