"""Pole authors.text (autor w BM25) i addytywna aktualizacja mappingu bez przebudowy indeksu."""

from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from library import search


def test_authors_searchable_with_polish_inflection():
    authors = search.CHUNKS_INDEX["mappings"]["properties"]["authors"]
    assert authors["type"] == "keyword"  # filtr po autorze bez zmian
    assert authors["fields"]["text"] == {"type": "text", "analyzer": "pl_stem"}
    body = search.bm25_body("Leon XIII o natchnieniu", 5, [])
    (clause,) = body["query"]["bool"]["must"][0]["bool"]["should"]
    assert "authors.text" in clause["multi_match"]["fields"]


def test_update_mapping_puts_properties_and_updates_in_place():
    client = mock.Mock()
    client.indices.exists.return_value = True
    client.update_by_query.return_value = {"task": "node:42"}
    with mock.patch("corpus.search.client.get_client", return_value=client):
        assert search.update_mapping("scriptura-chunks") == "node:42"
    body = client.indices.put_mapping.call_args.kwargs["body"]
    assert "text" in body["properties"]["authors"]["fields"]
    params = client.update_by_query.call_args.kwargs["params"]
    assert params["wait_for_completion"] == "false" and params["conflicts"] == "proceed"
    client.indices.exists.return_value = False
    with mock.patch("corpus.search.client.get_client", return_value=client):
        assert search.update_mapping("brak") is None


def test_reindex_chunks_update_mapping(settings, capsys):
    settings.SEARCH_BACKEND = "opensearch"
    with mock.patch.object(search, "update_mapping", return_value="node:7") as upd:
        call_command("reindex_chunks", "--update-mapping")
    assert upd.called and "node:7" in capsys.readouterr().out
    with mock.patch.object(search, "update_mapping", return_value=None):
        with pytest.raises(CommandError, match="brak indeksu"):
            call_command("reindex_chunks", "--update-mapping")
