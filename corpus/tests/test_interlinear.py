"""Interlinia i słownik: glosy kontekstowe, morfologia po polsku, transliteracja, Strong."""

import json

import pytest
from django.core.management import call_command

from corpus import interlinear, morph_pl
from corpus.importers import step, strongs
from corpus.importers.base import TokenRecord, VerseRecord, import_records
from corpus.models import Lexeme, Token, Work, WorkKind
from corpus.translit import greek_translit


def _verse(osis="Phil.2.6", vt=1, n=3):
    words = [
        {"i": i, "w": f"w{i}", "lemma": "", "morph": "", "gloss": ""} for i in range(n)
    ]
    return interlinear.VerseItem(osis, vt, words)


# --- parse_response ------------------------------------------------------------------------


def test_parse_response_valid_with_chatter():
    v = _verse()
    text = 'Oto JSON:\n{"Phil.2.6": [{"i": 0, "g": "który"}, {"i": 1, "g": " w "}, {"i": 2, "g": "postaci"}]}\nKoniec.'
    assert interlinear.parse_response(text, [v]) == {
        1: {0: "który", 1: "w", 2: "postaci"}
    }


@pytest.mark.parametrize(
    "items",
    [
        [{"i": 0, "g": "a"}, {"i": 1, "g": "b"}],  # brak pozycji
        [
            {"i": 0, "g": "a"},
            {"i": 1, "g": "b"},
            {"i": 2, "g": "c"},
            {"i": 3, "g": "d"},
        ],
        [{"i": 0, "g": "a"}, {"i": 1, "g": ""}, {"i": 2, "g": "c"}],  # pusta glosa
        [{"i": 0, "g": "a"}, {"i": 0, "g": "b"}, {"i": 2, "g": "c"}],  # duplikat
        [
            {"i": 0, "g": "a"},
            {"i": 1, "g": "λόγος"},
            {"i": 2, "g": "c"},
        ],  # nieprzetłumaczone
        [{"i": "x", "g": "a"}, {"i": 1, "g": "b"}, {"i": 2, "g": "c"}],
    ],
)
def test_parse_response_rejects_misaligned(items):
    v = _verse()
    assert interlinear.parse_response(json.dumps({"Phil.2.6": items}), [v]) == {}


def test_parse_response_keeps_good_verses_only():
    a, b = _verse("Gen.1.1", 1, 2), _verse("Gen.1.2", 2, 2)
    text = json.dumps(
        {
            "Gen.1.1": [{"i": 0, "g": "na początku"}, {"i": 1, "g": "stworzył"}],
            "Gen.1.2": [],
        }
    )
    assert interlinear.parse_response(text, [a, b]) == {
        1: {0: "na początku", 1: "stworzył"}
    }
    assert interlinear.parse_response("bez json", [a]) == {}


def test_batches_limit_words_and_verses():
    verses = [_verse(f"V.{i}", i, 100) for i in range(5)]
    assert [len(b) for b in interlinear.batches(verses, max_words=250)] == [2, 2, 1]
    assert [
        len(b) for b in interlinear.batches(verses, max_words=10_000, max_verses=4)
    ] == [4, 1]


def test_gloss_verses_retries_failed_verse_alone(monkeypatch):
    a, b = _verse("A.1", 1, 1), _verse("A.2", 2, 1)
    calls = []

    def fake(verses, model, base_url, api_key=""):
        calls.append([v.osis for v in verses])
        if len(verses) == 2:  # paczka: drugi werset źle wyrównany
            return json.dumps(
                {"A.1": [{"i": 0, "g": "x"}], "A.2": [{"i": 5, "g": "y"}]}
            )
        return json.dumps({verses[0].osis: [{"i": 0, "g": "z"}]})

    monkeypatch.setattr(interlinear, "request_glosses", fake)
    result, failed = interlinear.gloss_verses([a, b], "m", "http://x")
    assert result == {1: {0: "x"}, 2: {0: "z"}}
    assert failed == []
    assert calls == [["A.1", "A.2"], ["A.2"]]


def test_gloss_verses_reports_failures(monkeypatch):
    monkeypatch.setattr(interlinear, "request_glosses", lambda *a, **k: "{}")
    result, failed = interlinear.gloss_verses([_verse("B.1", 1, 1)], "m", "http://x")
    assert result == {} and failed == ["B.1"]


# --- morfologia, transliteracja, definicje -------------------------------------------------


def test_morph_greek():
    short, full = morph_pl.describe("grc", "V-", "3AMI-S--")
    assert short == "cz. aor. med. ind. 3 os. l.poj."
    assert "aoryst" in full and "strona medialna" in full
    assert morph_pl.describe("grc", "N-", "----ASM-")[0] == "rz. B. l.poj. m."


def test_morph_hebrew_segments():
    short, full = morph_pl.describe("hbo", "", "HR/Ncfsa")
    assert short == "przyim. + rz. ż. l.poj. abs."
    assert full.startswith("przyimek + rzeczownik pospolity")
    assert (
        morph_pl.describe("hbo", "", "HC/Vqw3ms")[0]
        == "spój. + cz. Qal wajjiqtol 3 os. m. l.poj."
    )
    assert morph_pl.describe("hbo", "", "AVhp3ms")[0].startswith("aram. cz. Hafel")
    assert morph_pl.describe("hbo", "", "") == ("", "")
    assert morph_pl.describe("hbo", "", "H??/Zz") == (
        "",
        "",
    )  # nieznane kody bez wyjątku


@pytest.mark.parametrize(
    "word,expected",
    [
        ("ἁρπαγμὸν", "harpagmon"),
        ("Ἰησοῦ", "Iēsou"),
        ("ῥῆμα", "rhēma"),
        ("εὐαγγέλιον", "euangelion"),
        ("υἱός", "huios"),
        ("ὑπάρχων", "hyparchōn"),
        ("Αἱρέσεις", "Haireseis"),
        ("θεῷ,", "theōi"),
        ("Μωϋσῆς", "Mōysēs"),
    ],
)
def test_greek_translit(word, expected):
    assert greek_translit(word) == expected


def test_clean_definition_keeps_structure():
    raw = (
        "<b>ἁρπαγμός</b>, οῦ, ὁ<BR /> (ἁρπάζω); <BR />__1. robbery, "
        "<ref='Php.2.6'>Php.2:6</ref>, AV &amp; more <span>x</span>"
    )
    assert step.clean_definition(raw) == (
        "<b>ἁρπαγμός</b>, οῦ, ὁ\n(ἁρπάζω);\n1. robbery, [[Php.2.6|Php.2:6]], AV & more x"
    )
    assert step.clean_definition(": beginning<br>1) first") == "beginning\n1) first"


def test_greek_strong_index():
    idx = step.GreekStrongIndex(
        [
            ("G4459", "πῶς", "πωσ"),
            ("G4458", "πως", "πωσ"),
            ("G2590", "καρπός", "καρποσ"),
            ("G7804", "καρπός", "καρποσ"),
            ("G2591", "Κάρπος", "καρποσ"),
            ("G2669", "καταποντίζω", "καταποντιζω"),
        ]
    )
    assert idx.lookup("πῶς", "πωσ") == "G4459"
    assert idx.lookup("πως", "πωσ") == "G4458"
    assert (
        idx.lookup("καρπός", "καρποσ") == "G2590"
    )  # klasyczny numer przed rozszerzonym
    assert idx.lookup("καταποντίζομαι", "καταποντιζομαι") == "G2669"
    assert idx.lookup("ἄγνωστον", "αγνωστον") == ""


def test_greek_strong_index_variants():
    idx = step.GreekStrongIndex(
        [
            ("G3779", "οὕτως", "ουτωσ"),
            ("G3475", "Μωϋσῆς, Μωσῆς", "μωυσησ, μωσησ"),
            ("G1672", "Ἕλλην=Ἕλλην", "ελλην=ελλην"),
            ("G622", "ἀπολλύω", "απολλυω"),
            ("G3660", "ὄμνυμι", "ομνυμι"),
            ("G2097", "εὐαγγελίζομαι", "ευαγγελιζομαι"),
            ("G5552", "χρύσεος", "χρυσεοσ"),
            ("G5062", "τεσσαράκοντα", "τεσσαρακοντα"),
        ]
    )
    assert idx.lookup("οὕτω(ς)", "ουτω(σ)") == "G3779"
    assert idx.lookup("Μωϋσῆς", "μωυσησ") == "G3475"
    assert idx.lookup("Ἕλλην", "ελλην") == "G1672"
    assert idx.lookup("ἀπόλλυμι", "απολλυμι") == "G622"
    assert idx.lookup("ὀμνύω", "ομνυω") == "G3660"
    assert idx.lookup("εὐαγγελίζω", "ευαγγελιζω") == "G2097"
    assert idx.lookup("χρυσοῦς", "χρυσουσ") == "G5552"
    assert idx.lookup("τεσσεράκοντα", "τεσσερακοντα") == "G5062"


def test_strongs_parse_js(tmp_path):
    path = tmp_path / "g.js"
    path.write_text(
        "/* licencja */\nvar strongsGreekDictionary = "
        '{"G725":{"lemma":"ἁρπαγμός","strongs_def":" plunder","derivation":"from G726;","kjv_def":"robbery","pron":"har-pag-mos\'"}};\n'
        "module.exports = strongsGreekDictionary;",
        encoding="utf-8",
    )
    data = strongs.parse_js(path)
    assert strongs.entry_fields(data["G725"]) == {
        "strong_def": "plunder",
        "strong_deriv": "from G726;",
        "strong_kjv": "robbery",
        "strong_pron": "har-pag-mos'",
    }
    assert strongs.base_number("H1254a") == "H1254"
    assert strongs.base_number("G0725") == "G725"


# --- komendy -------------------------------------------------------------------------------

STEP_HEADER = "TBESG header\n"


def _step_line(strong, lemma, translit, morph, gloss, meaning):
    return "\t".join([strong, strong, strong, lemma, translit, morph, gloss, meaning])


@pytest.mark.django_db
def test_import_lexicon_upsert_and_strongs(settings, tmp_path):
    settings.DATA_DIR = tmp_path
    (tmp_path / "step").mkdir()
    (tmp_path / "step" / "TBESG.txt").write_text(
        STEP_HEADER
        + _step_line(
            "G0725",
            "ἁρπαγμός",
            "harpagmos",
            "G:N-M",
            "robbery",
            "<b>a</b> thing<br>2. prize",
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "step" / "TBESH.txt").write_text(STEP_HEADER, encoding="utf-8")
    gnt = Work.objects.create(
        code="SBLGNT", name="GNT", language="grc", kind=WorkKind.ORIGINAL
    )
    import_records(
        gnt,
        iter(
            [
                VerseRecord(
                    "Phil",
                    2,
                    6,
                    "οὐχ ἁρπαγμὸν",
                    [
                        TokenRecord("οὐχ", lemma="οὐ"),
                        TokenRecord("ἁρπαγμὸν", lemma="ἁρπαγμός"),
                    ],
                )
            ]
        ),
    )
    call_command("import_lexicon")
    lex = Lexeme.objects.get(strong="G725")
    assert lex.definition == "<b>a</b> thing\n2. prize"
    assert Token.objects.get(surface="ἁρπαγμὸν").strong == "G725"
    assert Token.objects.get(surface="οὐχ").strong == ""

    lex.gloss_pl = "grabież"
    lex.save()
    (tmp_path / "strongs").mkdir()
    (tmp_path / "strongs" / "strongs-greek-dictionary.js").write_text(
        'var d = {"G725":{"strongs_def":"plunder","kjv_def":"robbery"}};',
        encoding="utf-8",
    )
    (tmp_path / "strongs" / "strongs-hebrew-dictionary.js").write_text(
        "var h = {};", encoding="utf-8"
    )
    call_command("import_strongs")
    call_command(
        "import_lexicon", "--no-fill"
    )  # ponowny import nie kasuje pól spoza STEP
    lex.refresh_from_db()
    assert (lex.gloss_pl, lex.strong_def, lex.strong_kjv) == (
        "grabież",
        "plunder",
        "robbery",
    )


@pytest.mark.django_db
def test_gloss_tokens_command(monkeypatch):
    gnt = Work.objects.create(
        code="SBLGNT", name="GNT", language="grc", kind=WorkKind.ORIGINAL
    )
    import_records(
        gnt,
        iter(
            [
                VerseRecord(
                    "John", 1, 1, "Ἐν ἀρχῇ", [TokenRecord("Ἐν"), TokenRecord("ἀρχῇ")]
                ),
                VerseRecord("John", 1, 2, "οὗτος", [TokenRecord("οὗτος")]),
            ]
        ),
    )
    Token.objects.filter(surface="οὗτος").update(gloss_pl="ten")
    seen = []

    def fake(verses, model, base_url, api_key=""):
        seen.extend(v.osis for v in verses)
        glosses = iter(["na", "początku"])
        return json.dumps(
            {
                v.osis: [{"i": w["i"], "g": next(glosses)} for w in v.words]
                for v in verses
            }
        )

    monkeypatch.setattr(interlinear, "request_glosses", fake)
    call_command("gloss_tokens", "--works", "SBLGNT", "--workers", "1")
    assert seen == ["John.1.1"]  # werset z kompletem glos pominięty
    assert list(
        Token.objects.order_by("verse_text__verse__ordinal", "position").values_list(
            "gloss_pl", flat=True
        )
    ) == [
        "na",
        "początku",
        "ten",
    ]
