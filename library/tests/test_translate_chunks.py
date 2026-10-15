"""translate_chunks --workers: równoległe żądania, zapis w głównym wątku, błędy nie przerywają."""

import pytest
from django.core.management import call_command

from library.management.commands import translate_chunks as tc
from library.models import Chunk, Document


@pytest.mark.django_db
def test_translate_chunks_parallel(monkeypatch):
    doc = Document.objects.create(title="T", language="en")
    pl = Document.objects.create(title="P", language="pl")
    for i in range(23):
        Chunk.objects.create(document=doc, order=i, text=f"text {i}")
    Chunk.objects.create(document=pl, order=0, text="polski")

    def fake(text, src, model, base_url, api_key=""):
        if text == "text 7":
            raise RuntimeError("429")
        return f"PL {text}"

    monkeypatch.setattr(tc, "translate", fake)
    call_command("translate_chunks", "--workers", "4")
    got = dict(Chunk.objects.filter(document=doc).values_list("order", "text_pl"))
    assert got[0] == "PL text 0" and got[22] == "PL text 22"
    assert got[7] == ""  # błąd jednego fragmentu nie przerywa reszty
    assert Chunk.objects.get(document=pl).text_pl == ""  # polskie bez przekładu


def test_clean_translation_drops_model_preface_and_markdown():
    from library.management.commands.translate_chunks import clean_translation

    out = clean_translation(
        "Oto polskie tłumaczenie:\n\nCałe Pismo *przez Boga natchnione* jest **pożyteczne** (2 Tm 3,16).",
        "Omnis Scriptura divinitus inspirata (II Tim., III, 16).",
    )
    assert out == "Całe Pismo przez Boga natchnione jest pożyteczne (2 Tm 3,16)."
    # gwiazdki z oryginału zostają, zwykły tekst bez zmian
    assert (
        clean_translation("Przypis *a* zostaje.", "Note *a* stays.")
        == "Przypis *a* zostaje."
    )
    assert clean_translation("Oto Bóg działa.", "Ecce Deus.") == "Oto Bóg działa."
