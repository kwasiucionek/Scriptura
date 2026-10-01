"""Offline regression tests for English BM25 (no OpenSearch/LLM/embedding server)."""

from unittest.mock import Mock

import pytest

from patristics import search
from patristics.models import PatPassage, PatWork


@pytest.mark.django_db
@pytest.mark.parametrize("translation", ["", None, "the resurrection"])
def test_english_bm25_survives_empty_translation_and_embedding_failure(
    monkeypatch, translation
):
    work = PatWork.objects.create(
        series="ANF",
        volume=1,
        ccel_id="offline",
        author="Irenaeus",
        title="Against Heresies",
    )
    passage = PatPassage.objects.create(
        work=work, order=0, text_en="The resurrection of the dead."
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

    hits = search._retrieve_opensearch("resurrection", 4)

    assert [h.passage_id for h in hits] == [passage.id]
    assert hits[0].text_en == passage.text_en
    assert client.search.call_count == 1
    should = client.search.call_args.kwargs["body"]["query"]["bool"]["should"]
    english = next(c["match"]["text_en"] for c in should if "text_en" in c["match"])
    assert english == {"query": translation or "resurrection", "boost": 1.5}
    assert embedding.call_args.kwargs == {"is_query": True}
    assert embedding.call_args.args[0] == [translation or "resurrection"]
