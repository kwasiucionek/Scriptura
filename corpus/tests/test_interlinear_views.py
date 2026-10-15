"""Widoki interlinii i słownika."""

import pytest
from django.urls import reverse

from corpus.importers.base import TokenRecord, VerseRecord, import_records
from corpus.models import Lexeme, Token, Work, WorkKind
from corpus.services import lexicon as lexsvc
from corpus.templatetags.corpus_tags import lexicon_definition, strong_links

pytestmark = pytest.mark.django_db


@pytest.fixture
def texts():
    gnt = Work.objects.create(
        code="SBLGNT", name="GNT", language="grc", kind=WorkKind.ORIGINAL
    )
    pl = Work.objects.create(
        code="BG1632", name="BG", language="pl", kind=WorkKind.TRANSLATION
    )
    import_records(
        gnt,
        iter(
            [
                VerseRecord(
                    "Phil",
                    2,
                    6,
                    "ὃς οὐχ ἁρπαγμὸν ἡγήσατο",
                    [
                        TokenRecord("ὃς", lemma="ὅς", pos="RR", morph="----NSM-"),
                        TokenRecord("οὐχ", lemma="οὐ", pos="D-", morph="--------"),
                        TokenRecord(
                            "ἁρπαγμὸν",
                            lemma="ἁρπαγμός",
                            strong="G725",
                            pos="N-",
                            morph="----ASM-",
                        ),
                        TokenRecord(
                            "ἡγήσατο", lemma="ἡγέομαι", pos="V-", morph="3AMI-S--"
                        ),
                    ],
                ),
                VerseRecord(
                    "Phil",
                    2,
                    7,
                    "ἀλλὰ ἑαυτὸν ἐκένωσεν",
                    [
                        TokenRecord("ἀλλὰ", lemma="ἀλλά"),
                        TokenRecord("ἑαυτὸν", lemma="ἑαυτοῦ"),
                        TokenRecord("ἐκένωσεν", lemma="κενόω"),
                    ],
                ),
            ]
        ),
    )
    import_records(
        pl,
        iter(
            [
                VerseRecord("Phil", 2, 6, "Który będąc w kształcie Bożym…"),
                VerseRecord("Phil", 2, 7, "Ale samego siebie wyniszczył…"),
            ]
        ),
    )
    Token.objects.filter(surface="ἁρπαγμὸν").update(gloss_pl="grabieżą")
    Lexeme.objects.create(
        strong="G725",
        language="grc",
        lemma="ἁρπαγμός",
        lemma_norm="αρπαγμοσ",
        transliteration="harpagmos",
        translit_fold="harpagmos",
        morph="G:N-M",
        gloss="something to grasp",
        gloss_pl="grabież, zdobycz",
        definition="<b>ἁρπαγμός</b>\n1. robbery, [[Php.2.6|Php.2:6]]",
        strong_def="plunder",
        strong_deriv="from G726;",
        strong_kjv="robbery",
        strong_pron="har-pag-mos'",
    )
    Lexeme.objects.create(
        strong="G2758",
        language="grc",
        lemma="κενόω",
        lemma_norm="κενοω",
        gloss="to empty",
        gloss_pl="opróżnić, ogołocić",
    )
    return gnt, pl


def test_interlinear_page(client, texts):
    r = client.get(reverse("corpus:interlinear"), {"q": "Flp 2,6-7"})
    assert r.status_code == 200
    html = r.content.decode()
    assert "grabieżą" in html  # glosa kontekstowa
    assert "harpagmon" in html  # transliteracja formy
    assert "rz. B. l.poj. m." in html  # morfologia po polsku
    assert (
        'class="iw-pl dict">opróżnić</span>' in html
    )  # glosa słownikowa (bez kontekstowej)
    assert "Który będąc" in html  # BG 1632 pod wersetem
    assert reverse("corpus:lexicon", args=["G725"]) in html


def test_reference_button_switches_to_interlinear(client, texts):
    r = client.get(reverse("corpus:reference"), {"q": "Flp 2,6", "widok": "interlinia"})
    assert r.status_code == 200
    assert "iw-orig" in r.content.decode()


def test_interlinear_bad_siglum(client, texts):
    r = client.get(reverse("corpus:interlinear"), {"q": "Xyz 9"})
    assert r.status_code == 400


def test_lexicon_page(client, texts):
    r = client.get(reverse("corpus:lexicon", args=["G725"]))
    assert r.status_code == 200
    html = r.content.decode()
    assert "grabież, zdobycz" in html
    assert "grabieżą" in html and "(1)" in html  # w interlinii tłumaczone jako
    assert "plunder" in html and "robbery" in html
    assert reverse("corpus:lexicon", args=["G726"]) in html  # pochodzenie wg Stronga
    assert "Flp 2,6" in html  # odsyłacz z definicji STEP
    assert "<b>ἁρπαγμός</b>" in html


def test_lexicon_normalizes_number_and_404(client, texts):
    r = client.get(reverse("corpus:lexicon", args=["g0725"]))
    assert r.status_code == 302 and r["Location"].endswith("/G725/")
    assert client.get(reverse("corpus:lexicon", args=["G99999"])).status_code == 404


def test_lexicon_search(client, texts):
    r = client.get(reverse("corpus:lexicon_search"), {"q": "ἁρπαγμός"})
    assert r.status_code == 302
    r = client.get(
        reverse("corpus:lexicon_search"), {"q": "zdobycz"}, HTTP_HX_REQUEST="true"
    )
    assert "G725" in r.content.decode()
    assert [x.strong for x in lexsvc.search("harpagmos")] == ["G725"]
    assert [x.strong for x in lexsvc.search("G725")] == ["G725"]


def test_find_lexeme_homograph_fallback():
    Lexeme.objects.create(
        strong="H1254a", language="hbo", lemma="בָּרָא", lemma_norm="ברא"
    )
    assert lexsvc.find_lexeme("H1254").strong == "H1254a"
    assert lexsvc.find_lexeme("bzdura") is None


def test_definition_filter_is_safe():
    html = lexicon_definition(
        "<b>x</b> <script>alert(1)</script> [[Php.2.6|Php.2:6]] [[Zzz.1.1|Zzz]] <b>open"
    )
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'href="/text/ref/?q=Flp+2%2C6">Flp 2,6</a>' in html
    assert "Zzz" in html and "Zzz.1.1" not in html
    assert html.endswith("</b>")
    assert 'href="/text/slownik/H430/"' in strong_links("from H430 <x>")
    assert "&lt;x&gt;" in strong_links("from H430 <x>")


def test_attribution_next_to_texts(client, texts):
    gnt, pl = texts
    gnt.attribution = "SBL Greek New Testament © 2010 SBL (CC BY 4.0)"
    gnt.save()
    pl.license = "domena publiczna"
    pl.save()
    for url, params in (
        (reverse("corpus:interlinear"), {"q": "Flp 2,6"}),
        (reverse("corpus:reference"), {"q": "Flp 2,6", "works": "SBLGNT,BG1632"}),
        (reverse("corpus:lexicon", args=["G725"]), {}),
    ):
        html = client.get(url, params).content.decode()
        assert "© 2010 SBL (CC BY 4.0)" in html, url
    html = client.get(reverse("corpus:interlinear"), {"q": "Flp 2,6"}).content.decode()
    assert "BG — domena publiczna" in html  # nota zastępcza: nazwa + licencja


def test_rag_work_notes(texts):
    from rag.service import work_notes

    texts[0].attribution = "nota SBLGNT"
    texts[0].save()
    assert work_notes({"SBLGNT", "XYZ"}) == {"SBLGNT": "nota SBLGNT"}
    assert work_notes(set()) == {}
