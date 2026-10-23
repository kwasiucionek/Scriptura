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


@pytest.mark.django_db
def test_index_all_skips_passages_already_indexed(monkeypatch):
    # przyrost (--missing): pasaże obecne w indeksie nie są liczone ani wysyłane ponownie
    work = PatWork.objects.create(
        series="NPNF2", volume=6, ccel_id="npnf206", author="Jerome", title="T"
    )
    old, new = (
        PatPassage.objects.create(work=work, order=i, text_en=t)
        for i, t in enumerate(["old", "new"])
    )
    client = Mock()
    client.indices.exists.return_value = True
    monkeypatch.setattr("corpus.search.client.get_client", lambda: client)
    bulk = Mock()
    monkeypatch.setattr("opensearchpy.helpers.bulk", bulk)
    embed = Mock(return_value=[[0.1]])
    monkeypatch.setattr(search, "embed", embed)
    assert search.index_all(skip={old.os_id}) == 1
    (docs,) = [c.args[1] for c in bulk.call_args_list]
    assert [d["_id"] for d in docs] == [new.os_id]
    assert embed.call_args.args[0] == ["Jerome: new"]
