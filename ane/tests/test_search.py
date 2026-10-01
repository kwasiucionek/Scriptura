"""Offline regression tests for English BM25 (no OpenSearch/LLM/embedding server)."""

from unittest.mock import Mock

import pytest

from ane import search
from ane.models import AneChapter, AnePassage, AneText


@pytest.mark.django_db
@pytest.mark.parametrize("translation", ["", None, "the flood"])
def test_english_bm25_survives_empty_translation_and_embedding_failure(
    monkeypatch, translation
):
    text = AneText.objects.create(source_id="offline", name="Gilgamesh")
    chapter = AneChapter.objects.create(text=text, name="XI")
    passage = AnePassage.objects.create(
        chapter=chapter,
        order=0,
        line_start="1",
        line_end="2",
        translation_en="The flood covered the earth.",
    )
    client = Mock()
    client.indices.exists.return_value = True
    client.search.return_value = {
        "hits": {
            "hits": [
                {"_id": passage.os_id, "_source": {"passage_id": passage.id}},
            ]
        }
    }
    monkeypatch.setattr("corpus.search.client.get_client", lambda: client)
    monkeypatch.setattr("library.translate.translate_query", lambda query: translation)
    embedding = Mock(side_effect=RuntimeError("offline embeddings"))
    monkeypatch.setattr(search, "embed", embedding)

    hits = search._retrieve_opensearch("flood", 4)

    assert [h.passage_id for h in hits] == [passage.id]
    assert hits[0].translation_en == passage.translation_en
    assert client.search.call_count == 1
    should = client.search.call_args.kwargs["body"]["query"]["bool"]["should"]
    english = next(c["match"]["translation_en"] for c in should if "match" in c)
    assert english == {"query": translation or "flood", "boost": 1.5}
    assert embedding.call_args.kwargs == {"is_query": True}
    assert embedding.call_args.args[0] == ["flood"] + (
        [translation] if translation else []
    )
