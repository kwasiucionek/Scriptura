"""Wykrywanie języka z treści i poprawa metadanych (fix_languages, ingest_document)."""

import pytest
from django.core.management import call_command

from library.langdetect import detect, detect_document
from library.models import Chunk, Document

PL = "Autor recenzji zauważa, że książka jest ważna, ponieważ przez analizę tekstu pokazuje, jak który motyw się rozwija oraz dlaczego nie można go pominąć."
EN = "The author argues that the book is important, and that the analysis of the text shows how the motif develops, which is why it cannot be ignored by the reader."
IT = "Il libro della studiosa mostra che la lettura del testo non è semplice e che il motivo della promessa è centrale per la teologia del Salterio e delle tradizioni."


def test_detect_languages_and_uncertainty():
    assert detect(PL) == "pl" and detect(EN) == "en" and detect(IT) == "it"
    assert detect("Gen 1:1; Ex 3:14") == ""  # za mało słów — brak decyzji
    assert (
        detect_document([PL, PL, IT, PL]) == "pl"
    )  # polska recenzja z włoskim cytatem
    assert detect(
        "Gli studi su Isaia mostrano che il profeta non è un autore unico."
    ) in ("it", "")


@pytest.mark.django_db
def test_fix_languages_apply_clears_text_pl_for_polish():
    review = Document.objects.create(
        title="Recenzja: Il racconto doppio", language="it"
    )
    Chunk.objects.create(document=review, order=0, text=PL, text_pl="przeróbka modelu")
    eng = Document.objects.create(title="La simbolica", language="it")
    Chunk.objects.create(document=eng, order=0, text=EN, text_pl="przekład")
    ok = Document.objects.create(title="Studio", language="it")
    Chunk.objects.create(document=ok, order=0, text=IT)

    call_command("fix_languages")  # raport — bez zmian
    assert Document.objects.get(id=review.id).language == "it"

    call_command("fix_languages", "--apply")
    review.refresh_from_db(), eng.refresh_from_db(), ok.refresh_from_db()
    assert (review.language, eng.language, ok.language) == ("pl", "en", "it")
    assert review.chunks.get().text_pl == ""  # oryginał zamiast przeróbki
    assert eng.chunks.get().text_pl == "przekład"  # przekład z angielskiego zostaje


def test_detect_latin_church_document():
    from library.langdetect import detect

    latin = (
        "Unum atque eundem Deum Veteris et Novi Testamenti, hoc est Legis et Prophetarum atque "
        "Evangelii profitetur auctorem: quoniam eodem Spiritu Sancto inspirante utriusque "
        "Testamenti Sancti locuti sunt, quorum libros suscipit et veneratur, qui titulis "
        "sequentibus continentur. Sed et ea quae ab Ecclesia tradita sunt, nec sine causa."
    )
    assert detect(latin) == "la"
