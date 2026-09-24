"""Indeks i retrieval pasaży patrystycznych — osobny indeks `patristics`; dwa kanały:
1) po wersecie: PatRef nakładające się na sigla z pytania (najpewniejszy — Ojcowie o tym miejscu),
2) semantyczny: BM25 (en + pl) + kNN po przekładzie zapytania.
Wynik = sekcja porównawcza w prompcie; nigdy źródło [n] literatury.
"""

import logging
import re
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from library.embeddings import embed
from library.search import CHUNKS_ANALYSIS, rrf
from patristics.models import PatPassage, PatRef

log = logging.getLogger(__name__)

PAT_INDEX = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0, "knn": True},
        "analysis": CHUNKS_ANALYSIS,
    },
    "mappings": {
        "properties": {
            "passage_id": {"type": "integer"},
            "author": {"type": "keyword"},
            "work": {"type": "keyword"},
            "ref": {"type": "keyword"},
            "text_en": {"type": "text", "analyzer": "english"},
            "text_pl": {"type": "text", "analyzer": "pl_stem"},
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
class PatHit:
    passage_id: int
    author: str
    work: str
    ref: str
    text_en: str
    url: str
    license: str
    score: float
    via_verse: bool = False
    text_pl: str = ""  # przekład maszynowy (translate_patristics), gdy jest


def index_name() -> str:
    from corpus.search.client import index_name as _in

    return _in("patristics")


def ensure_index(recreate: bool = False) -> str:
    from corpus.search.client import get_client

    client, name = get_client(), index_name()
    if recreate and client.indices.exists(index=name):
        client.indices.delete(index=name)
    if not client.indices.exists(index=name):
        client.indices.create(index=name, body=PAT_INDEX)
    return name


def index_all(
    with_vectors: bool = True,
    cached: dict[str, list[float]] | None = None,
    batch: int = 256,
) -> int:
    from opensearchpy import helpers

    from corpus.search.client import get_client

    name = ensure_index()
    cached = cached or {}
    qs = PatPassage.objects.select_related("work").order_by("work_id", "order")
    total = 0
    buf: list[PatPassage] = []

    def flush():
        nonlocal total
        if not buf:
            return
        vectors = None
        if with_vectors:
            missing = [p for p in buf if p.os_id not in cached]
            if missing:
                vecs = embed(
                    [
                        f"{p.work.author}: {p.text_pl or p.text_en}"[:4000]
                        for p in missing
                    ]
                )
                for p, v in zip(missing, vecs, strict=True):
                    cached[p.os_id] = v
            vectors = [cached[p.os_id] for p in buf]
        docs = []
        for i, p in enumerate(buf):
            src = {
                "passage_id": p.id,
                "author": p.work.author_pl or p.work.author,
                "work": str(p.work),
                "ref": p.ref,
                "text_en": p.text_en,
                "text_pl": p.text_pl,
            }
            if vectors:
                src["embedding"] = vectors[i]
            docs.append({"_index": name, "_id": p.os_id, "_source": src})
        helpers.bulk(get_client(), docs, chunk_size=200, request_timeout=120)
        total += len(docs)
        buf.clear()

    for p in qs.iterator(chunk_size=batch):
        buf.append(p)
        if len(buf) >= batch:
            flush()
    flush()
    get_client().indices.refresh(index=name)
    return total


def by_verses(ranges: list[tuple[int, int]], k: int = 6) -> list[PatHit]:
    """Pasaże, których odsyłacze nakładają się na zakresy sigli; najpierw dokładniejsze (krótsze) odsyłacze."""
    if not ranges:
        return []
    q = Q()
    for a, b in ranges:
        q |= Q(start__lte=b, end__gte=a)
    refs = PatRef.objects.filter(q).select_related("passage__work")
    scored: dict[int, tuple[float, PatPassage]] = {}
    for r in refs:
        span = r.end - r.start + 1
        score = 1.0 / span  # cały rozdział (999) daje mały wynik, pojedynczy werset 1.0
        cur = scored.get(r.passage_id)
        if not cur or score > cur[0]:
            scored[r.passage_id] = (score, r.passage)
    best = sorted(scored.values(), key=lambda x: -x[0])[:k]
    return [_hit(p, s, via_verse=True) for s, p in best]


def retrieve(
    query: str, k: int = 4, ranges: list[tuple[int, int]] | None = None
) -> list[PatHit]:
    hits = by_verses(ranges or [], k)
    seen = {h.passage_id for h in hits}
    need = k - len(hits)
    if need <= 0:
        return hits
    if settings.SEARCH_BACKEND == "opensearch":
        try:
            more = _retrieve_opensearch(query, need + len(seen))
        except Exception as exc:
            log.warning("retrieval patrystyczny pominięty: %s", exc)
            more = []
    else:
        more = _retrieve_db(query, need + len(seen))
    return hits + [h for h in more if h.passage_id not in seen][:need]


def _retrieve_opensearch(query: str, k: int) -> list[PatHit]:
    from corpus.search.client import get_client
    from library.translate import translate_query

    client, name = get_client(), index_name()
    if not client.indices.exists(index=name):
        return []
    query_en = translate_query(query)
    should = [{"match": {"text_pl": {"query": query}}}]
    if query_en:
        should.append({"match": {"text_en": {"query": query_en, "boost": 1.5}}})
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
        for vec in embed([query_en or query], is_query=True):
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
        log.warning("kNN patrystyczne pominięte: %s", exc)
    fused = rrf(rankings)
    ids = sorted(fused, key=lambda i: -fused[i])[:k]
    by_id = {
        p.id: p
        for p in PatPassage.objects.filter(
            id__in=[docs[i]["passage_id"] for i in ids]
        ).select_related("work")
    }
    return [
        _hit(by_id[docs[i]["passage_id"]], fused[i])
        for i in ids
        if docs[i]["passage_id"] in by_id
    ]


def _retrieve_db(query: str, k: int) -> list[PatHit]:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]
    if not words:
        return []
    cond = Q()
    for w in words:
        cond |= Q(text_pl__icontains=w) | Q(text_en__icontains=w)
    scored = []
    for p in PatPassage.objects.filter(cond).select_related("work")[: k * 5]:
        hay = f"{p.text_pl} {p.text_en}".lower()
        scored.append((sum(hay.count(w) for w in words), p))
    scored.sort(key=lambda x: -x[0])
    return [_hit(p, float(s)) for s, p in scored[:k]]


def _hit(p: PatPassage, score: float, via_verse: bool = False) -> PatHit:
    w = p.work
    return PatHit(
        passage_id=p.id, author=w.author_pl or w.author, work=w.title_pl or w.title, ref=p.ref,
        text_en=p.text_en, url=w.url, license=w.license, score=score, via_verse=via_verse,
        text_pl=p.text_pl,
    )  # fmt: skip
