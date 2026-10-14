"""reindex_chunks --missing: tylko dokumenty, których liczba chunków w indeksie różni się od bazy."""

import pytest
from django.core.management import call_command

from library import search
from library.models import Chunk, Document


@pytest.mark.django_db
def test_reindex_missing_only_incomplete_documents(settings, monkeypatch):
    settings.SEARCH_BACKEND = "opensearch"
    docs = []
    for title, n in (("pełny", 2), ("niepełny", 3), ("brak", 1)):
        d = Document.objects.create(title=title)
        for i in range(n):
            Chunk.objects.create(document=d, order=i, text=f"{title} {i}")
        docs.append(d)
    full, partial, absent = docs
    monkeypatch.setattr(search, "ensure_index", lambda **kw: "idx")
    monkeypatch.setattr(
        search, "indexed_counts", lambda index=None: {full.id: 2, partial.id: 1}
    )
    done = []
    monkeypatch.setattr(
        search,
        "index_document",
        lambda doc, **kw: done.append(doc.id) or doc.chunks.count(),
    )
    call_command("reindex_chunks", "--missing")
    assert done == [partial.id, absent.id]
