"""Wzmianki słowa oryginału w literaturze (strona hasła słownika) — fallback bazodanowy."""

import pytest

from library.models import Chunk, Document
from library.search import term_mentions

pytestmark = pytest.mark.django_db


def test_term_mentions_db_fallback_respects_access_and_dedupes():
    open_doc = Document.objects.create(title="Hymn o kenozie", access="open")
    closed = Document.objects.create(title="Zamknięty", access="restricted")
    Chunk.objects.create(document=open_doc, order=0, text="Słowo ἁρπαγμὸν w Flp 2,6 …")
    Chunk.objects.create(document=open_doc, order=1, text="ἁρπαγμός raz jeszcze")
    Chunk.objects.create(document=closed, order=0, text="ἁρπαγμός w tekście zamkniętym")
    Chunk.objects.create(document=open_doc, order=2, text="bez słowa greckiego")

    hits = term_mentions(["ἁρπαγμός", "ἁρπαγμὸν"], [], access=["open"])
    assert [(h.title, h.order) for h in hits] == [("Hymn o kenozie", 0)]
    assert (
        term_mentions([], [], ["ab"]) == []
    )  # za krótka transliteracja nie szuka niczego
