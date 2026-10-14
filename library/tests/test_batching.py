"""in_batches: kolejność querysetu, limit, select_related i zapisy w trakcie iteracji."""

import pytest

from library.batching import in_batches
from library.models import Chunk, Document


@pytest.mark.django_db
def test_in_batches_order_limit_and_writes():
    doc = Document.objects.create(title="T", language="en")
    for i in range(7):
        Chunk.objects.create(document=doc, order=i, text=f"t{i}")
    qs = (
        Chunk.objects.filter(document=doc).select_related("document").order_by("-order")
    )
    seen = []
    for ch in in_batches(qs, size=3):
        seen.append(ch.order)
        assert ch.document.title == "T"  # select_related zachowane
        ch.text_pl = f"p{ch.order}"
        ch.save(update_fields=["text_pl"])  # zapis w trakcie iteracji
    assert seen == [6, 5, 4, 3, 2, 1, 0]
    assert Chunk.objects.filter(document=doc, text_pl="").count() == 0
    assert [c.order for c in in_batches(qs, size=2, limit=3)] == [6, 5, 4]


@pytest.mark.django_db
def test_in_batches_skips_rows_deleted_meanwhile():
    doc = Document.objects.create(title="T", language="en")
    for i in range(4):
        Chunk.objects.create(document=doc, order=i, text=f"t{i}")
    qs = Chunk.objects.filter(document=doc).order_by("order")
    out = []
    for ch in in_batches(qs, size=2):
        out.append(ch.order)
        if ch.order == 0:
            Chunk.objects.filter(document=doc, order=3).delete()
    assert out == [0, 1, 2]
