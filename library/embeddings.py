"""Embeddingi: Ollama (/api/embed) lub serwer TEI (/embed) — lokalny albo HF Inference Endpoint.

EMBEDDINGS_BACKEND=ollama  -> OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL (keep_alive 24h)
EMBEDDINGS_BACKEND=tei     -> TEI_EMBED_URL (+ TEI_TOKEN dla endpointów HF), model wybrany po stronie serwera
Prefiks zapytania (EMBEDDING_QUERY_PREFIX) doklejany tak samo w obu backendach.
"""

import json
import urllib.request

from django.conf import settings

BATCH = 32


def _headers(token: str = "") -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _post(url: str, body: dict, token: str = "", timeout: int = 120):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=_headers(token)
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.load(resp)


def embed(
    texts: list[str], model: str | None = None, is_query: bool = False
) -> list[list[float]]:
    """Zwraca wektory w kolejności wejścia. Batchuje po 32.
    is_query=True dokleja EMBEDDING_QUERY_PREFIX (arctic: "query: "); dokumenty bez prefiksu."""
    if is_query and settings.EMBEDDING_QUERY_PREFIX:
        texts = [settings.EMBEDDING_QUERY_PREFIX + t for t in texts]
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        if settings.EMBEDDINGS_BACKEND == "tei":
            out.extend(
                _post(
                    f"{settings.TEI_EMBED_URL.rstrip('/')}/embed",
                    {"inputs": batch, "truncate": True},
                    settings.TEI_TOKEN,
                )
            )
        else:
            data = _post(
                f"{settings.OLLAMA_BASE_URL}/api/embed",
                {
                    "model": model or settings.OLLAMA_EMBED_MODEL,
                    "input": batch,
                    "keep_alive": "24h",
                },
                settings.OLLAMA_API_KEY,
            )
            out.extend(data["embeddings"])
    return out
