"""Embeddingi: partie wg EMBEDDINGS_BATCH i limit czasu wg EMBEDDINGS_TIMEOUT."""

from library import embeddings


def test_embed_batches_and_timeout(monkeypatch, settings):
    settings.EMBEDDINGS_BACKEND = "ollama"
    settings.EMBEDDINGS_BATCH = 2
    settings.EMBEDDINGS_TIMEOUT = 777
    settings.EMBEDDING_QUERY_PREFIX = "query: "
    calls = []

    class Resp:
        def __init__(self, n):
            self.n = n

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        import json

        body = json.loads(req.data)
        calls.append((body["input"], timeout))
        return Resp(len(body["input"]))

    monkeypatch.setattr(embeddings.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        embeddings.json, "load", lambda resp: {"embeddings": [[1.0]] * resp.n}
    )
    out = embeddings.embed(["a", "b", "c", "d", "e"])
    assert len(out) == 5
    assert [len(c[0]) for c in calls] == [2, 2, 1]
    assert {c[1] for c in calls} == {777}
    calls.clear()
    embeddings.embed(["pytanie"], is_query=True)
    assert calls[0][0] == ["query: pytanie"]
