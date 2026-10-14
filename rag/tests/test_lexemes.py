"""Terminy z pytania -> hasła leksykonu (dopasowanie po rdzeniu) i legenda skrótów ksiąg w promptcie."""

import pytest

from corpus.models import Lexeme
from rag import service


def _lx(strong, lemma, translit, fold, gloss):
    return Lexeme.objects.create(
        strong=strong, language="grc", lemma=lemma, lemma_norm=lemma.lower(),
        transliteration=translit, translit_fold=fold, gloss=gloss,
    )  # fmt: skip


@pytest.fixture
def tartaros_db():
    _lx("G9404", "Τάρταρος", "Tartaros", "tartaros", "infernal region")
    _lx("G5020", "ταρταρόω", "tartaroō", "tartaro", "hell: Tartarus")
    _lx("G21215", "Ταρταρόπαις", "Tartaropais", "tartaropais", "child of Tartarus")
    _lx("G3056", "λόγος", "logos", "logos", "word")
    _lx("G3058", "λοιδορέω", "loidoreō", "loidoreo", "to revile")


def test_translit_stem():
    assert service.translit_stem("tartaros") == "tartar"
    assert service.translit_stem("tartar") == "tartar"
    assert (
        service.translit_stem("logos") == ""
    )  # za krótki rdzeń: tylko dokładne dopasowanie
    assert service.translit_stem("charanu") == "charan"


@pytest.mark.django_db
def test_polish_form_without_exact_lexeme_matches_stem(tartaros_db):
    # „Tartar” nie ma dokładnego hasła; wcześniej -> brak leksemu -> odmowa odpowiedzi
    got = [
        lx.strong for lx in service.lexemes_from_transliteration("Czym jest Tartar?")
    ]
    assert got == ["G5020", "G9404"]  # najkrótsze najpierw, Tartaropais odpada


@pytest.mark.django_db
def test_exact_lexeme_brings_one_related(tartaros_db):
    # τάρταρος (LXX) + ταρταρόω — jedyne wystąpienie w NT (2 P 2,4)
    got = [
        lx.strong for lx in service.lexemes_from_transliteration("Co to jest Tartaros?")
    ]
    assert got == ["G9404", "G5020"]


@pytest.mark.django_db
def test_short_term_stays_exact_and_no_duplicates(tartaros_db):
    got = [lx.strong for lx in service.lexemes_from_transliteration("logos i Logos")]
    assert got == ["G3056"]


def test_book_legend_expands_abbreviations():
    legend = service.book_legend(
        ["Hi 40,20", "Prz 30,16", "Hi 41,24", "2 P 2,4", "Hi 2", "xyz"]
    )
    assert legend == "Hi = Księga Hioba; Prz = Księga Przysłów; 2 P = 2 List św. Piotra"


@pytest.mark.django_db
def test_prompt_contains_book_legend(monkeypatch):
    monkeypatch.setattr(service, "lexicon_lines", lambda *a, **kw: [])
    verses = [service.VerseSource(ref="Hi 40,20", work="LXX", text="…ἐν τῷ ταρτάρῳ…")]
    user = service.build_messages("Czym jest Tartaros?", [], verses)[1]["content"]
    assert "(Skróty ksiąg: Hi = Księga Hioba)" in user
