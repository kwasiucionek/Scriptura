"""Testy harvestu na utrwalonych kształtach odpowiedzi DSpace 7 i OpenAlex (bez sieci)."""

from unittest import mock

import pytest
from django.core.management.base import CommandError

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


def test_openalex_works_for_source_filters_and_paginates(settings):
    """Bez klucza: strony numerowane (kursor za 1. stroną wymaga OPENALEX_API_KEY — HTTP 401)."""
    settings.OPENALEX_API_KEY = ""
    w = lambda i: {"id": f"https://openalex.org/W{i}", "display_name": f"T{i}"}  # noqa: E731
    page1 = {"results": [w(i) for i in range(100)], "meta": {"count": 150}}
    page2 = {"results": [w(i) for i in range(100, 150)], "meta": {"count": 150}}
    with mock.patch.object(openalex, "_get", side_effect=[page1, page2]) as g:
        out = openalex.works_for_source(
            "https://openalex.org/S123", only_oa=True, from_year=2015
        )
    assert len(out) == 150 and g.call_count == 2
    first, second = (c[0][1] for c in g.call_args_list)
    assert first["filter"] == (
        "primary_location.source.id:S123,is_oa:true,from_publication_date:2015-01-01"
    )
    assert (first["page"], second["page"]) == (1, 2) and "cursor" not in first
    import pytest

    with pytest.raises(ValueError):
        openalex.works_for_source("A123")  # id autora zamiast czasopisma


def test_openalex_cursor_with_api_key(settings):
    settings.OPENALEX_API_KEY = "k"
    page1 = {
        "results": [{"id": "https://openalex.org/W1", "display_name": "A"}],
        "meta": {"next_cursor": "c2"},
    }
    page2 = {
        "results": [],
        "meta": {"next_cursor": "c3"},
    }  # pusta strona kończy pętlę mimo kursora
    with mock.patch.object(openalex, "_get", side_effect=[page1, page2]) as g:
        out = openalex.works_for_source("S123")
    assert [e.title for e in out] == ["A"]
    assert [c[0][1]["cursor"] for c in g.call_args_list] == ["*", "c2"]


def test_openalex_find_sources():
    data = {"results": [{"id": "https://openalex.org/S9", "display_name": "Verbum Vitae", "works_count": 1200,
                         "issn_l": "1644-8561", "host_organization_name": "KUL"}]}  # fmt: skip
    with mock.patch.object(openalex, "_get", return_value=data):
        (src,) = openalex.find_sources("Verbum Vitae")
    assert src["display_name"] == "Verbum Vitae" and src["issn"] == "1644-8561"


# --- dokumenty Kościoła (library.harvest.vatican) -------------------------------

_VATICAN_CONTENT = (
    "<html><head><script>x()</script></head><body><nav>menu</nav>"
    '<div class="abstract text parbase vaticanrichtext"><div class="clearfix"></div></div>'
    '<div class="text parbase container vaticanrichtext">'
    '<p align="center">[AR - EN - IT]<br/><b>DEI VERBUM</b><br/>ON DIVINE<br/>REVELATION</p>'
    "<p>1. Hearing the word\nof God&nbsp;with reverence (1 John 1:2-3).<sup>(1)</sup></p>"
    "<h3>CHAPTER I</h3><p>2. In His goodness “God” chose to reveal Himself.</p>"
    '<p class="copyright">Copyright © Dicastero per la Comunicazione</p>'
    "</div><footer><div>FAQ</div></footer></body></html>"
)


def _doc(key):
    from library.harvest import vatican

    return next(d for d in vatican.DOCUMENTS if d.key == key)


def test_vatican_page_text_content_region():
    from library.harvest import vatican

    text = vatican.page_text(_VATICAN_CONTENT)
    assert text.startswith(
        "DEI VERBUM ON DIVINE REVELATION\n\n1. Hearing the word of God with"
    )
    assert "### CHAPTER I\n\n2. In His goodness “God”" in text
    assert "[AR" not in text and "Copyright" not in text
    assert "menu" not in text and "FAQ" not in text and "x()" not in text


def test_vatican_page_text_falls_back_to_body():
    from library.harvest import vatican

    raw = (
        '<html><body><table><tr><td><a href="/"><img alt="Index"></a></td>'
        '<td><p align="center">PONTIFICIA COMMISSIONE BIBLICA</p>'
        "<p>La Santa Madre Chiesa\n (1 Tm 3,15).</p></td></tr></table></body></html>"
    )
    assert (
        vatican.page_text(raw)
        == "PONTIFICIA COMMISSIONE BIBLICA\n\nLa Santa Madre Chiesa (1 Tm 3,15)."
    )


def test_vatican_decode_declared_latin1_as_cp1252():
    from library.harvest import vatican

    body = (
        '<meta content="text/html; charset=iso-8859-1"><p>\x93Verbum\x94 è</p>'.encode(
            "latin-1"
        )
    )
    assert "“Verbum” è" in vatican.decode(body)
    assert vatican.decode("zażółć".encode(), "text/html;charset=utf-8") == "zażółć"


def test_vatican_excerpt_markers_ignore_whitespace_and_fail_loudly():
    from library.harvest import vatican

    text = "Wstęp redakcji.\nCAPUT  II\nDE REVELATIONE Eadem   Sancta Mater.\nCAPUT III dalej"
    assert vatican.excerpt(text, "CAPUT II DE REVELATIONE", "Sancta Mater.") == (
        "CAPUT  II\nDE REVELATIONE Eadem   Sancta Mater."
    )
    with pytest.raises(ValueError, match="początku"):
        vatican.excerpt(text, "CAPUT IV")
    with pytest.raises(ValueError, match="końca"):
        vatican.excerpt(text, "CAPUT II", "nie ma")


def test_vatican_source_text_wikisource_excerpt_and_migne_columns():
    from library.harvest import vatican

    raw = (
        "<div><p>editio: Migne</p><p>CAP. VII. 13. Qui vero libri (0501A)recipiantur in canone, "
        "brevis annexus ostendit. Data X kalendas Martias, viris clarissimis consulibus.</p>"
        "<p>EPISTOLA VII.</p></div>"
    )
    doc = _doc("innocenty-i-consulenti-tibi")
    with mock.patch.object(vatican, "fetch", return_value=raw) as fetch:
        text = vatican.source_text(doc, doc.url)
    assert fetch.call_args.args[0].endswith("?action=render")
    assert text == (
        "CAP. VII. 13. Qui vero libri recipiantur in canone, brevis annexus ostendit. "
        "Data X kalendas Martias, viris clarissimis consulibus."
    )


def test_vatican_source_text_drops_editor_numbering():
    from library.harvest import vatican

    raw = (
        "<p>1334 706 Unum atque eundem Deum Veteris et Novi Testamenti profitetur: 1335 706 "
        "Quinque Moysi; Actibus Apostolorum et Apocalypsi Johannis. 1336 707 Propterea</p>"
    )
    doc = _doc("sobor-florencki-dekret-dla-jakobitow")
    with mock.patch.object(vatican, "fetch", return_value=raw):
        text = vatican.source_text(doc, doc.url)
    assert text == (
        "Unum atque eundem Deum Veteris et Novi Testamenti profitetur: Quinque Moysi; "
        "Actibus Apostolorum et Apocalypsi Johannis."
    )


def test_vatican_ccel_section_skips_notes_and_joins_following():
    from library.harvest import vatican

    xml = (
        b'<ThML><ThML.body><div1 id="viii"><div4 id="viii.vii.iii.lxiv"><p id="viii.vii.iii.lxiv-p1">No psalms composed'
        b"<note>Hefele says.</note> by private individuals.</p><p>Notes.</p><p>Epitome.</p></div4>"
        b'<div4 id="viii.vii.iii.lxv"><p>These are all the books.</p></div4>'
        b'<div4 id="viii.vii.iii.lxvi"><p>Next canon.</p></div4></div1></ThML.body></ThML>'
    )
    assert vatican.ccel_section(xml, "viii.vii.iii.lxiv", following=1) == (
        "No psalms composed by private individuals.\n\nThese are all the books."
    )
    with pytest.raises(ValueError, match="brak sekcji"):
        vatican.ccel_section(xml, "viii.vii.iii.c")


def test_vatican_pdf_text_drops_running_heads_and_joins_hyphens():
    import pymupdf

    from library.harvest import vatican

    pdf = pymupdf.open()
    for body in (
        "180 Acta Apostolicae Sedis - Commentarium Officiale\nBibliorum scientiam quanti Eccle-\nsia fecerit.",
        "Acta Pii PP. XI 181\nDatum Romae. PIUS PP. XI",
    ):
        pdf.new_page().insert_text((72, 72), body)
    data = pdf.tobytes()
    assert vatican.pdf_text(data, (1, 2)) == (
        "Bibliorum scientiam quanti Ecclesia fecerit. Datum Romae. PIUS PP. XI"
    )


def test_vatican_to_entry(tmp_path):
    from library.harvest import vatican

    doc = _doc("dei-verbum")
    e = vatican.to_entry(doc, vatican.page_text(_VATICAN_CONTENT), tmp_path)
    assert (e.doc_type, e.access, e.register, e.language) == (
        "church",
        "open",
        "mixed",
        "en",
    )
    assert (
        e.authors == ["Sobór Watykański II"]
        and e.year == 1965
        and e.journal == "vatican.va"
    )
    assert (
        e.url.startswith("https://www.vatican.va/")
        and e.source_id == "vatican-dei-verbum"
    )
    assert len(e.license) <= 128 and len(e.note) <= 300
    text = open(e.local_file, encoding="utf-8").read()
    assert text.startswith(f"# {doc.title}\n\nDEI VERBUM")
    aas = vatican.to_entry(_doc("instrukcja-pkb-1950"), "INSTRUCTIO", tmp_path)
    assert aas.url.endswith(".pdf#page=495") and aas.language == "la"


def test_vatican_documents_are_unique_and_valid():
    from library.harvest import vatican
    from library.models import DocType

    keys = [d.key for d in vatican.DOCUMENTS]
    assert len(keys) == len(set(keys)) and "church" in DocType.values
    for d in vatican.DOCUMENTS:
        assert d.url.startswith("https://") and d.language in (
            "pl",
            "en",
            "it",
            "la",
        ), d.key
        assert d.original_language in ("la", "en", "grc"), d.key
        assert not d.path.lower().endswith(".pdf") or d.pages, d.key
        if d.public_domain:
            license_, note = vatican.pd_license(d)
            assert len(license_) <= 128 and len(note) <= 300, d.key
        assert len(d.source) <= 200 and len(d.title) <= 300


def test_harvest_vatican_command(tmp_path, settings):
    from django.core.management import call_command

    from library.harvest import manifest, vatican

    settings.DATA_DIR = tmp_path
    out = tmp_path / "vatican.jsonl"
    with mock.patch.object(vatican, "fetch", return_value=_VATICAN_CONTENT) as fetch:
        call_command("harvest", "vatican", "--keys", "dei-verbum", "--out", str(out))
    assert fetch.call_count == 1
    (e,) = manifest.read(out)
    assert e.doc_type == "church" and e.local_file.startswith(
        str(tmp_path / "library" / "vatican")
    )
    with pytest.raises(CommandError, match="nieznane klucze"):
        call_command("harvest", "vatican", "--keys", "nie-ma-takiego")


def test_vatican_split_for_translation_keeps_sentences():
    from library.harvest import vatican

    para = " ".join(f"Sententia numero {i} longa satis est." for i in range(40))
    pieces = vatican.split_for_translation(para, max_chars=300)
    assert all(len(p) <= 300 for p in pieces) and " ".join(pieces) == para
    assert vatican.split_for_translation("Brevis.", max_chars=300) == ["Brevis."]


def test_vatican_polish_translation_public_domain_only(tmp_path):
    from library.harvest import vatican

    calls = []

    def fake(text):
        calls.append(text)
        return f"PL({text})"

    original = "Prima pars.\n\nSecunda pars. Tertia sententia."
    e = vatican.polish_translation(
        _doc("providentissimus-deus"), original, tmp_path, fake, workers=2
    )
    assert sorted(calls) == ["Prima pars.", "Secunda pars. Tertia sententia."]
    assert (e.language, e.doc_type, e.access, e.source_id) == (
        "pl",
        "church",
        "open",
        "vatican-providentissimus-deus-pl",
    )
    assert e.url.endswith("_providentissimus-deus.html") and "/la/" in e.url
    assert e.title.endswith("(przekład roboczy z łaciny)") and len(e.license) <= 128
    text = open(e.local_file, encoding="utf-8").read()
    assert text.endswith("PL(Prima pars.)\n\nPL(Secunda pars. Tertia sententia.)\n")
    athanasius = vatican.polish_translation(
        _doc("atanazy-list-39"), "Of the books.", tmp_path, fake
    )
    assert (
        athanasius.title.endswith("(przekład roboczy z angielskiego)")
        and "XIX w." in athanasius.license
    )
    assert athanasius.journal.startswith("CCEL")
    with pytest.raises(ValueError, match="domeny publicznej"):
        vatican.polish_translation(_doc("dei-verbum"), original, tmp_path, fake)


def test_harvest_vatican_skips_public_domain_by_default(tmp_path):
    from library.harvest import vatican

    with mock.patch.object(vatican, "source_text", return_value="Textus."):
        keys = {e.source_id for e in vatican.harvest(tmp_path)}
        explicit = vatican.harvest(tmp_path, ["providentissimus-deus"])
    assert "vatican-providentissimus-deus" not in keys and "vatican-dei-verbum" in keys
    assert (
        "vatican-kanon-muratoriego" not in keys
        and "vatican-instrukcja-pkb-1950" in keys
    )
    assert [e.source_id for e in explicit] == ["vatican-providentissimus-deus"]


def test_harvest_vatican_polish_pd_command(tmp_path, settings):
    from django.core.management import call_command

    from library.harvest import manifest, vatican
    from library.management.commands import translate_chunks

    settings.DATA_DIR = tmp_path
    out = tmp_path / "pd.jsonl"
    with (
        mock.patch.object(vatican, "fetch", return_value=_VATICAN_CONTENT) as fetch,
        mock.patch.object(
            translate_chunks, "translate", side_effect=lambda t, src, *a: f"[{src}] {t}"
        ),
    ):
        call_command(
            "harvest", "vatican", "--polish-pd", "--keys", "providentissimus-deus",
            "--translate-model", "m", "--out", str(out),
        )  # fmt: skip
    assert "/la/" in fetch.call_args.args[0]
    (e,) = manifest.read(out)
    assert e.language == "pl" and "[la] " in open(e.local_file, encoding="utf-8").read()


def test_openalex_works_by_ids_keeps_order_and_batches(settings, tmp_path):
    # wybrane prace (np. z wyszukiwania tematycznego); ujęcie z --perspective trafia do manifestu
    from django.core.management import call_command

    from library.harvest import manifest

    w = lambda i: {"id": f"https://openalex.org/W{i}", "display_name": f"T{i}"}  # noqa: E731
    ids = [f"W{i}" for i in range(60)]
    pages = [{"results": [w(i) for i in reversed(range(50))]}, {"results": [w(55)]}]
    with mock.patch.object(openalex, "_get", side_effect=pages) as g:
        out = openalex.works_by_ids(ids + ["https://openalex.org/W3"])
    assert [e.title for e in out] == [f"T{i}" for i in range(50)] + ["T55"]
    assert g.call_count == 2
    assert g.call_args_list[0][0][1]["filter"].startswith("openalex:W0|W1|")
    path = tmp_path / "m.jsonl"
    with mock.patch.object(openalex, "_get", return_value={"results": [w(7)]}):
        call_command(
            "harvest",
            "openalex",
            "--ids",
            "W7",
            "--perspective",
            "catholic",
            "--out",
            str(path),
        )
    (e,) = manifest.read(path)
    assert e.perspective == "catholic" and e.source_id.endswith("W7")


def test_vatican_parts_joined_and_perspective(tmp_path):
    # Pastor aeternus: rozdziały z osobnych stron Wikiźródeł, w kolejności
    from library.harvest import vatican

    doc = _doc("sobor-watykanski-i-pastor-aeternus")
    pages = {
        part: f"<div><p>{part.rsplit('/', 1)[-1]} textus.</p></div>"
        for part in doc.parts
    }
    with mock.patch.object(
        vatican, "fetch", side_effect=lambda url: pages[url.split("?")[0]]
    ):
        text = vatican.source_text(doc, doc.original_url)
    assert text.split("\n\n") == [
        "Praefatio textus.", "Caput_I textus.", "Caput_II textus.", "Caput_III textus.", "Caput_IV textus.",
    ]  # fmt: skip
    assert (
        vatican.to_entry(_doc("lumen-gentium"), "x", tmp_path).perspective
        == "magisterium"
    )
    assert vatican.to_entry(_doc("kanon-muratoriego"), "x", tmp_path).perspective == ""


@pytest.mark.django_db
def test_ingest_document_saves_perspective(tmp_path, settings):
    from django.core.management import call_command

    from library.models import Document

    settings.SEARCH_BACKEND = "db"
    src = tmp_path / "bracia.md"
    src.write_text(
        "# Czy Chrystus miał braci?\n\nBracia Pańscy to krewni Jezusa (Mk 6,3).\n",
        encoding="utf-8",
    )
    call_command("ingest_document", "--file", str(src), "--title", "Czy Chrystus miał braci?",
                 "--perspective", "catholic", "--no-index")  # fmt: skip
    assert (
        Document.objects.get(title="Czy Chrystus miał braci?").perspective == "catholic"
    )


def test_vatican_catechism_drops_intratext_navigation():
    from library.harvest import vatican

    raw = (
        "<body><p>Help</p><p>Catechism of the Catholic Church</p><p>IntraText - Text</p>"
        "<ul><li>PART ONE</li><li>Paragraph 2. CONCEIVED Previous - Next</li></ul>"
        "<p>Paragraph 2. CONCEIVED</p><p>484 The Annunciation to Mary.</p>"
        "<p>170 LG 64; cf. 63.</p><p>Previous - NextCopyright © Libreria Editrice Vaticana</p></body>"
    )
    doc = _doc("katechizm-484-511")
    with mock.patch.object(vatican, "fetch", return_value=raw):
        text = vatican.source_text(doc, doc.url)
    assert text.startswith("Paragraph 2. CONCEIVED") and text.endswith(
        "170 LG 64; cf. 63."
    )
    assert "Help" not in text and "Copyright" not in text
