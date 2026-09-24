"""Indeks i retrieval pasaży ANE — osobny indeks `ane`, osobna funkcja; wynik trafia do RAG
tylko jako sekcja porównawcza (rag.service), nigdy jako źródło [n] literatury.

Pola: translation_en (english), translation_pl (polish, po translate_ane), normalized (fold —
transliteracje akadyjskie), embedding z przekładu polskiego (gdy jest) lub angielskiego.
Zapytanie: polskie -> BM25 po translation_pl + (tłumaczenie zapytania) po translation_en; kNN.
"""

import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from ane.models import AnePassage
from library.embeddings import embed
from library.search import CHUNKS_ANALYSIS, rrf

log = logging.getLogger(__name__)

ANE_INDEX = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0, "knn": True},
        "analysis": CHUNKS_ANALYSIS,
    },
    "mappings": {
        "properties": {
            "passage_id": {"type": "integer"},
            "text": {"type": "keyword"},
            "ref": {"type": "keyword"},
            "translation_en": {"type": "text", "analyzer": "english"},
            "translation_pl": {"type": "text", "analyzer": "pl_stem"},
            "normalized": {"type": "text", "analyzer": "fold"},
            "embedding": {
                "type": "knn_vector",
                "dimension": settings.EMBEDDING_DIM,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                },
            },
        }
    },
}


@dataclass
class AneHit:
    passage_id: int
    text: str
    ref: str
    translation_en: str
    normalized: str
    url: str
    license: str
    score: float


def index_name() -> str:
    from corpus.search.client import index_name as _in

    return _in("ane")


def ensure_index(recreate: bool = False) -> str:
    from corpus.search.client import get_client

    client, name = get_client(), index_name()
    if recreate and client.indices.exists(index=name):
        client.indices.delete(index=name)
    if not client.indices.exists(index=name):
        client.indices.create(index=name, body=ANE_INDEX)
    return name


def index_all(
    with_vectors: bool = True, cached: dict[str, list[float]] | None = None
) -> int:
    from opensearchpy import helpers

    from corpus.search.client import get_client

    name = ensure_index()
    passages = list(
        AnePassage.objects.select_related("chapter__text").order_by(
            "chapter__text", "chapter__order", "order"
        )
    )
    if not passages:
        return 0
    vectors = None
    if with_vectors:
        cached = cached or {}
        if cached and all(p.os_id in cached for p in passages):
            vectors = [
                cached[p.os_id] for p in passages
            ]  # z export_vectors — bez liczenia
        else:
            vectors = embed(
                [
                    f"{p.chapter.label}\n{p.translation_pl or p.translation_en}"
                    for p in passages
                ]
            )
    docs = []
    for i, p in enumerate(passages):
        src = {
            "passage_id": p.id,
            "text": str(p.chapter.text),
            "ref": p.ref,
            "translation_en": p.translation_en,
            "translation_pl": p.translation_pl,
            "normalized": p.normalized,
        }
        if vectors:
            src["embedding"] = vectors[i]
        docs.append({"_index": name, "_id": p.os_id, "_source": src})
    helpers.bulk(get_client(), docs, chunk_size=200, request_timeout=120)
    get_client().indices.refresh(index=name)
    return len(docs)


def retrieve(query: str, k: int = 4) -> list[AneHit]:
    if settings.SEARCH_BACKEND == "opensearch":
        try:
            return _retrieve_opensearch(query, k)
        except Exception as exc:  # brak indeksu ANE nie blokuje odpowiedzi
            log.warning("ANE retrieval pominięty: %s", exc)
            return []
    return _retrieve_db(query, k)


def _retrieve_opensearch(query: str, k: int) -> list[AneHit]:
    from corpus.search.client import get_client
    from library.translate import translate_query

    client, name = get_client(), index_name()
    if not client.indices.exists(index=name):
        return []
    query_en = translate_query(query)
    should = [
        {
            "multi_match": {
                "query": query,
                "fields": ["translation_pl^2", "normalized"],
                "operator": "or",
            }
        },
    ]
    if query_en:
        should.append({"match": {"translation_en": {"query": query_en, "boost": 1.5}}})
    bm25 = client.search(
        index=name,
        body={
            "size": k * 3,
            "_source": {"excludes": ["embedding"]},
            "query": {"bool": {"should": should, "minimum_should_match": 1}},
        },
    )["hits"]["hits"]
    rankings = [[h["_id"] for h in bm25]]
    docs = {h["_id"]: h["_source"] for h in bm25}
    try:
        vecs = embed([query] + ([query_en] if query_en else []), is_query=True)
        for vec in vecs:
            knn = client.search(
                index=name,
                body={
                    "size": k * 3,
                    "_source": {"excludes": ["embedding"]},
                    "query": {"knn": {"embedding": {"vector": vec, "k": k * 3}}},
                },
            )["hits"]["hits"]
            rankings.append([h["_id"] for h in knn])
            docs.update({h["_id"]: h["_source"] for h in knn})
    except Exception as exc:
        log.warning("ANE kNN pominięte: %s", exc)
    fused = rrf(rankings)
    ids = sorted(fused, key=lambda i: -fused[i])[:k]
    by_id = {
        p.id: p
        for p in AnePassage.objects.filter(
            id__in=[docs[i]["passage_id"] for i in ids]
        ).select_related("chapter__text")
    }
    out = []
    for i in ids:
        p = by_id.get(docs[i]["passage_id"])
        if p:
            out.append(_hit(p, fused[i]))
    return out


def _retrieve_db(query: str, k: int) -> list[AneHit]:
    words = [
        w for w in re.findall(r"\w+", query.lower()) if len(w) > 3
    ]  # bez interpunkcji
    if not words:
        return []
    cond = Q()
    for w in words:
        cond |= (
            Q(translation_pl__icontains=w)
            | Q(translation_en__icontains=w)
            | Q(normalized__icontains=w)
        )
    qs = AnePassage.objects.filter(cond).select_related("chapter__text")[: k * 5]
    scored = []
    for p in qs:
        hay = f"{p.translation_pl} {p.translation_en} {p.normalized}".lower()
        scored.append((sum(hay.count(w) for w in words), p))
    scored.sort(key=lambda x: -x[0])
    return [_hit(p, float(s)) for s, p in scored[:k]]


def _hit(p: AnePassage, score: float) -> AneHit:
    t = p.chapter.text
    return AneHit(
        passage_id=p.id, text=str(t), ref=p.ref, translation_en=p.translation_en,
        normalized=p.normalized[:400], url=t.url, license=t.license, score=score,
    )  # fmt: skip
