"""Indeks `chunks` w OpenSearch (BM25 + knn_vector) i retrieval hybrydowy (RRF w Pythonie).

Fallback SEARCH_BACKEND=db: tylko leksykalnie po bazie (bez wektorów).
"""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from corpus.normalize import normalize_greek, normalize_hebrew, normalize_polish
from corpus.search.mappings import ANALYSIS
from library.models import Chunk, Document

log = logging.getLogger(__name__)

# Czasy ostatniego retrievalu (ms) — do diagnostyki w odpowiedzi RAG; nadpisywane per wywołanie
LAST_TIMINGS: dict[str, int] = {}

# Transliteracja naukowa -> konwencja polska, PRZED foldingiem ICU (który zdejmie resztę
# diakrytyków). Dzięki temu "miškān" w tekście i "miszkan" w pytaniu dają ten sam token.
TRANSLIT_MAPPINGS = [
    "š=>sz", "Š=>sz", "ḥ=>ch", "Ḥ=>ch", "ṣ=>c", "Ṣ=>c", "ṭ=>t", "Ṭ=>t",
    "ẓ=>z", "ǧ=>dż", "ḏ=>d", "ṯ=>t", "ḫ=>ch", "ġ=>g",
    "ʾ=>", "ʿ=>", "ʼ=>", "ʻ=>", "ʹ=>", "’=>", "‘=>",
]  # fmt: skip

CHUNKS_ANALYSIS = {
    **ANALYSIS,
    "char_filter": {"translit": {"type": "mapping", "mappings": TRANSLIT_MAPPINGS}},
    "analyzer": {
        **ANALYSIS["analyzer"],
        "fold": {
            "type": "custom",
            "char_filter": ["translit"],
            "tokenizer": "icu_tokenizer",
            "filter": ["icu_folding", "lowercase"],
        },
    },
}

CHUNKS_INDEX = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0, "knn": True},
        "analysis": CHUNKS_ANALYSIS,
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "document_id": {"type": "integer"},
            "order": {"type": "integer"},
            "title": {"type": "text", "analyzer": "pl_stem"},
            "authors": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "journal": {"type": "keyword"},
            "year": {"type": "short"},
            "access": {"type": "keyword"},
            "register": {"type": "keyword"},  # scientific | popular | mixed
            "owner_id": {"type": "integer"},  # materiały osobiste (access=personal)
            "section": {"type": "text", "analyzer": "pl_stem"},
            "text": {
                "type": "text",
                "analyzer": "pl_stem",
                "term_vector": "with_positions_offsets",
            },
            "text_exact": {"type": "text", "analyzer": "exact"},
            "text_fold": {
                "type": "text",
                "analyzer": "fold",
            },  # transliteracje bez diakrytyków
            "text_en": {
                "type": "text",
                "analyzer": "english",
            },  # tylko dokumenty angielskie
            "sigla": {"type": "long_range"},
            "sigla_labels": {"type": "keyword"},
            "terms_grc": {
                "type": "keyword"
            },  # słowa greckie z chunka, znormalizowane (bez akcentów)
            "terms_hbo": {"type": "keyword"},  # słowa hebrajskie, bez nikud
            "embedding": {
                "type": "knn_vector",
                "dimension": settings.EMBEDDING_DIM,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "lucene",
                },
            },
        },
    },
}


_GREEK_WORD = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]{2,}")
_HEBREW_WORD = re.compile(r"[\u0590-\u05ff]{2,}")


def original_terms(text: str) -> tuple[list[str], list[str]]:
    """Greckie i hebrajskie słowa z tekstu (chunk lub pytanie), znormalizowane jak w indeksie."""
    grc = sorted({normalize_greek(w) for w in _GREEK_WORD.findall(text)})
    hbo = sorted({normalize_hebrew(w) for w in _HEBREW_WORD.findall(text)})
    return [g for g in grc if g], [h for h in hbo if h]


@dataclass
class ChunkHit:
    chunk_id: int
    document_id: int
    order: int
    title: str
    authors: list[str]
    citation: str
    doc_type: str
    year: int | None
    url: str
    section: str
    text: str
    sigla: list[str]
    score: float
    access: str
    register: str = "scientific"
    owner_id: int | None = None


# --- indeksowanie ---------------------------------------------------------


def ensure_index(recreate: bool = False, index: str | None = None) -> str:
    from corpus.search.client import get_client, index_name

    client, name = get_client(), index or index_name("chunks")
    if recreate and client.indices.exists(index=name):
        client.indices.delete(index=name)
    if not client.indices.exists(index=name):
        client.indices.create(index=name, body=CHUNKS_INDEX)
    return name


def build_docs(
    document: Document, vectors: list[list[float]] | None, index: str | None = None
) -> Iterator[dict]:
    from corpus.search.client import index_name

    name = index or index_name("chunks")
    authors = [a.name for a in document.authors.all()]
    chunks = list(document.chunks.order_by("order"))
    for i, ch in enumerate(chunks):
        src = {
            "document_id": document.id,
            "order": ch.order,
            "title": document.title,
            "authors": authors,
            "doc_type": document.doc_type,
            "journal": document.journal,
            "year": document.year,
            "access": document.access,
            "register": document.register,
            "owner_id": document.owner_id or 0,
            "section": ch.section,
            # dokument obcy z maszynowym przekładem: polski do BM25/embeddingu, oryginał w text_en
            "text": ch.text_pl or ch.text,
            "text_exact": ch.text,
            # fold: oryginał + przekład, żeby transliteracje (skn, ṭḫ, miškān) przetrwały tłumaczenie
            "text_fold": f"{ch.text}\n{ch.text_pl}" if ch.text_pl else ch.text,
            **({"text_en": ch.text} if document.language.startswith("en") else {}),
            "sigla": [{"gte": s["start"], "lte": s["end"]} for s in ch.sigla],
            "sigla_labels": [s["ref"] for s in ch.sigla],
        }
        src["terms_grc"], src["terms_hbo"] = original_terms(ch.text)
        if vectors:
            src["embedding"] = vectors[i]
        yield {"_index": name, "_id": ch.os_id, "_source": src}


def delete_document_from_index(document: Document, index: str | None = None) -> None:
    """Usuwa chunki dokumentu z indeksu (materiały osobiste, usunięcia z admina)."""
    from corpus.search.client import get_client, index_name

    name = index or index_name("chunks")
    client = get_client()
    if client.indices.exists(index=name):
        client.delete_by_query(
            index=name,
            body={"query": {"term": {"document_id": document.id}}},
            refresh=True,
        )


def fetch_vectors(
    index: str, document_ids: list[int] | None = None
) -> dict[str, list[float]]:
    """Istniejące wektory z indeksu (id -> embedding), do ponownego użycia po zmianie mappingu."""
    from opensearchpy import helpers

    from corpus.search.client import get_client

    client = get_client()
    if not client.indices.exists(index=index):
        return {}
    query = (
        {"match_all": {}}
        if not document_ids
        else {"terms": {"document_id": document_ids}}
    )
    out: dict[str, list[float]] = {}
    body = {"query": query, "_source": ["embedding"]}
    for hit in helpers.scan(client, index=index, query=body, size=500):
        emb = hit["_source"].get("embedding")
        if emb:
            out[hit["_id"]] = emb
    return out


def index_document(
    document: Document,
    with_vectors: bool = True,
    index: str | None = None,
    cached_vectors: dict[str, list[float]] | None = None,
) -> int:
    from opensearchpy import helpers

    from corpus.search.client import get_client
    from library.embeddings import embed

    name = ensure_index(index=index)
    vectors = None
    if with_vectors:
        chunks = list(document.chunks.order_by("order"))
        cached = cached_vectors or {}
        if cached and all(c.os_id in cached for c in chunks):
            vectors = [cached[c.os_id] for c in chunks]
        else:
            texts = [
                f"{document.title}\n{c.section}\n{c.text_pl or c.text}" for c in chunks
            ]
            vectors = embed(texts)
    ok, _ = helpers.bulk(
        get_client(),
        build_docs(document, vectors, name),
        chunk_size=200,
        request_timeout=120,
    )
    get_client().indices.refresh(index=name)
    return ok


_HAS_EN: dict[str, bool] = {}


def HAS_ENGLISH_DOCS() -> bool:  # noqa: N802
    """Czy w korpusie są dokumenty angielskie (cache na czas procesu; ingestia go czyści)."""
    if "v" not in _HAS_EN:
        _HAS_EN["v"] = Chunk.objects.filter(
            document__language__startswith="en", text_pl=""
        ).exists()  # angielskie chunki bez przekładu -> tłumaczenie zapytania jako fallback
    return _HAS_EN["v"]


def reset_language_cache() -> None:
    _HAS_EN.clear()


# --- rozszerzanie kontekstu o sąsiednie chunki ------------------------------------


def diversify(hits: list[ChunkHit], max_per_doc: int, k: int) -> list[ChunkHit]:
    """Nie więcej niż max_per_doc chunków z jednego dokumentu w top-k (kolejność zachowana);
    sąsiadujące chunki (order±1) tego samego dokumentu liczą się jako jeden — sklei je expand_with_neighbors."""
    if max_per_doc <= 0:
        return hits[:k]
    out: list[ChunkHit] = []
    per_doc: dict[int, list[int]] = {}
    for h in hits:
        orders = per_doc.setdefault(h.document_id, [])
        if any(abs(h.order - o) <= 1 for o in orders):
            continue  # sąsiad już wybranego chunku — wejdzie jako kontekst, nie jako osobne [n]
        if len(orders) >= max_per_doc:
            continue
        orders.append(h.order)
        out.append(h)
        if len(out) >= k:
            break
    if len(out) < k:  # dopełnij tym, co odrzucono (poza sąsiadami)
        chosen = {id(h) for h in out}
        for h in hits:
            if id(h) in chosen:
                continue
            orders = per_doc.get(h.document_id, [])
            if any(abs(h.order - o) <= 1 for o in orders):
                continue
            out.append(h)
            per_doc.setdefault(h.document_id, []).append(h.order)
            if len(out) >= k:
                break
    return out


def expand_with_neighbors(hits: list[ChunkHit], radius: int) -> list[ChunkHit]:
    """Dokleja do każdego trafienia tekst chunków order±radius z tego samego dokumentu
    (z bazy), bez duplikowania fragmentów już obecnych w liście. Kolejność trafień
    bez zmian; chunki-sąsiedzi wchodzą do tekstu, nie jako osobne pozycje [n]."""
    if radius <= 0 or not hits:
        return hits
    wanted: dict[int, set[int]] = {}
    for h in hits:
        for o in range(h.order - radius, h.order + radius + 1):
            if o >= 0:
                wanted.setdefault(h.document_id, set()).add(o)
    present = {(h.document_id, h.order) for h in hits}
    rows = Chunk.objects.filter(document_id__in=wanted).values_list(
        "document_id", "order", "text"
    )
    texts = {(d, o): t for d, o, t in rows if o in wanted.get(d, ())}
    used: set[tuple[int, int]] = set()
    for h in hits:
        parts = []
        for o in range(h.order - radius, h.order + radius + 1):
            key = (h.document_id, o)
            if o == h.order:
                parts.append(h.text)
            elif key in texts and key not in present and key not in used:
                parts.append(texts[key])
                used.add(key)
        h.text = "\n".join(parts)
    return hits


# --- retrieval ------------------------------------------------------------


def _filters(
    access: list[str],
    authors: list[str] | None,
    sigla: list[tuple[int, int]] | None,
    registers: list[str] | None = None,
    user_id: int | None = None,
    personal_only: bool = False,
) -> list[dict]:
    """Filtry dostępu: poziomy użytkownika LUB jego materiały osobiste (access=personal + owner_id).
    personal_only=True zawęża do materiałów osobistych."""
    personal = {
        "bool": {
            "filter": [
                {"term": {"access": "personal"}},
                {"term": {"owner_id": user_id or -1}},
            ]
        }
    }
    if personal_only:
        f: list[dict] = [personal]
    else:
        levels = [a for a in access if a != "personal"]
        f = [
            {
                "bool": {
                    "should": [{"terms": {"access": levels}}, personal],
                    "minimum_should_match": 1,
                }
            }
        ]
    if registers:
        f.append({"terms": {"register": registers}})
    if authors:
        f.append({"terms": {"authors": authors}})
    if sigla:
        f.append(
            {
                "bool": {
                    "should": [
                        {
                            "range": {
                                "sigla": {"gte": s, "lte": e, "relation": "intersects"}
                            }
                        }
                        for s, e in sigla
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
    return f


def bm25_body(query: str, k: int, filters: list[dict], query_en: str = "") -> dict:
    """BM25 po tekście polskim (+ boost za terminy oryginalne z pytania — term na keyword,
    niezależny od tego, jak analizator potraktuje politoniczną grekę czy nikud) i, gdy podano
    query_en, równolegle po polu text_en dokumentów angielskich."""
    grc, hbo = original_terms(query)
    should = [{"term": {"terms_grc": {"value": t, "boost": 3.0}}} for t in grc]
    should += [{"term": {"terms_hbo": {"value": t, "boost": 3.0}}} for t in hbo]
    text_clauses = [
        {
            "multi_match": {
                "query": query,
                "fields": ["text^2", "text_fold", "section", "title"],
                "operator": "or",
            }
        }
    ]
    if query_en:
        text_clauses.append({"match": {"text_en": {"query": query_en, "boost": 1.5}}})
    return {
        "size": k,
        "_source": {"excludes": ["embedding"]},
        "query": {
            "bool": {
                "must": [{"bool": {"should": text_clauses, "minimum_should_match": 1}}],
                "should": should,
                "filter": filters,
            }
        },
    }


def knn_body(vector: list[float], k: int, filters: list[dict]) -> dict:
    return {
        "size": k,
        "_source": {"excludes": ["embedding"]},
        "query": {
            "knn": {
                "embedding": {
                    "vector": vector,
                    "k": k,
                    "filter": {"bool": {"filter": filters}},
                }
            }
        },
    }


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion: id -> score."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def retrieve(
    query: str,
    k: int = 8,
    access: list[str] | None = None,
    authors: list[str] | None = None,
    sigla: list[tuple[int, int]] | None = None,
    registers: list[str] | None = None,
    user_id: int | None = None,
    personal_only: bool = False,
) -> list[ChunkHit]:
    """Hybryda (BM25 + kNN, RRF) -> reranker -> top k. Pula kandydatów = RERANK_TOP_N."""
    import time

    from library.rerank import rerank

    LAST_TIMINGS.clear()
    access = access or settings.RAG_ACCESS
    pool = max(k, settings.RERANK_TOP_N)
    t = time.monotonic()
    if settings.SEARCH_BACKEND == "opensearch":
        hits = _retrieve_opensearch(
            query, pool, access, authors, sigla, registers, user_id, personal_only
        )
    else:
        hits = _retrieve_db(
            query, pool, access, authors, sigla, registers, user_id, personal_only
        )
    LAST_TIMINGS["search_ms"] = int((time.monotonic() - t) * 1000) - LAST_TIMINGS.get(
        "embed_ms", 0
    )
    t = time.monotonic()
    out = rerank(query, hits, k)
    LAST_TIMINGS["rerank_ms"] = int((time.monotonic() - t) * 1000)
    return out


def _retrieve_opensearch(
    query, k, access, authors, sigla, registers=None, user_id=None, personal_only=False
) -> list[ChunkHit]:
    from corpus.search.client import get_client, index_name
    from library.embeddings import embed

    client, name = get_client(), index_name("chunks")
    import time

    from library.translate import translate_query

    filters = _filters(access, authors, sigla, registers, user_id, personal_only)
    pool = k * 2
    t = time.monotonic()
    query_en = translate_query(query) if HAS_ENGLISH_DOCS() else ""
    LAST_TIMINGS["translate_ms"] = int((time.monotonic() - t) * 1000)
    bm25 = client.search(index=name, body=bm25_body(query, pool, filters, query_en))[
        "hits"
    ]["hits"]
    rankings = [[h["_id"] for h in bm25]]
    docs = {h["_id"]: h["_source"] for h in bm25}
    try:
        t = time.monotonic()
        queries = [query] + ([query_en] if query_en else [])
        vecs = embed(queries, is_query=True)
        LAST_TIMINGS["embed_ms"] = int((time.monotonic() - t) * 1000)
        for vec in (
            vecs
        ):  # kNN po polsku i (gdy jest tłumaczenie) po angielsku — osobne listy do RRF
            knn = client.search(index=name, body=knn_body(vec, pool, filters))["hits"][
                "hits"
            ]
            rankings.append([h["_id"] for h in knn])
            docs.update({h["_id"]: h["_source"] for h in knn})
    except Exception as exc:  # brak embeddingów nie blokuje odpowiedzi
        log.warning("kNN pominięte: %s", exc)
    fused = rrf(rankings)
    top = sorted(fused, key=fused.get, reverse=True)[:k]
    doc_ids = {docs[i]["document_id"] for i in top}
    documents = {
        d.id: d
        for d in Document.objects.filter(id__in=doc_ids).prefetch_related("authors")
    }
    hits = []
    for i in top:
        s = docs[i]
        d = documents[s["document_id"]]
        hits.append(
            ChunkHit(
                chunk_id=0,
                document_id=d.id,
                order=s["order"],
                title=d.title,
                authors=s["authors"],
                citation=d.citation,
                doc_type=d.doc_type,
                year=d.year,
                url=d.url,
                section=s["section"],
                text=s["text"],
                sigla=s["sigla_labels"],
                score=fused[i],
                access=d.access,
                register=d.register,
                owner_id=d.owner_id,
            )
        )
    return hits


_STOPWORDS = {
    "jest",
    "oraz",
    "jako",
    "tego",
    "tych",
    "jaki",
    "jaka",
    "jakie",
    "czym",
    "przez",
    "ktory",
    "która",
    "które",
}


def _retrieve_db(
    query, k, access, authors, sigla, registers=None, user_id=None, personal_only=False
) -> list[ChunkHit]:
    """Fallback bez OpenSearch: wynik = trafienia słów zapytania po granicy wyrazu (prefiks),
    filtr po dostępie/autorach w SQL, sigla i punktacja w Pythonie."""
    words = [
        w for w in normalize_polish(query).split() if len(w) > 3 and w not in _STOPWORDS
    ]
    patterns = [re.compile(rf"\b{re.escape(w)}\w*", re.IGNORECASE) for w in words]
    q_grc, q_hbo = original_terms(query)
    personal = Q(document__access="personal", document__owner_id=user_id or -1)
    if personal_only:
        qs = Chunk.objects.filter(personal).select_related("document")
    else:
        levels = [a for a in access if a != "personal"]
        qs = Chunk.objects.filter(
            Q(document__access__in=levels) | personal
        ).select_related("document")
    if authors:
        qs = qs.filter(document__authors__name__in=authors)
    if registers:
        qs = qs.filter(document__register__in=registers)
    if (
        words and not sigla
    ):  # z siglami słowa tylko punktują; bez sigli są wstępnym filtrem
        cond = Q()
        for w in words:
            cond |= Q(text__icontains=w)
        qs = qs.filter(cond)
    scored = []
    for ch in qs.distinct()[:500]:
        hit_sigla = bool(sigla) and any(
            s["start"] <= e and s["end"] >= st for s in ch.sigla for st, e in sigla
        )
        if sigla and not hit_sigla:
            continue
        score = sum(len(p.findall(ch.text)) for p in patterns) + (
            10 if hit_sigla else 0
        )
        if q_grc or q_hbo:
            c_grc, c_hbo = original_terms(ch.text)
            score += 3 * (len(set(q_grc) & set(c_grc)) + len(set(q_hbo) & set(c_hbo)))
        if score:
            scored.append((score, ch))
    scored.sort(key=lambda t: -t[0])
    hits = []
    for score, ch in scored[:k]:
        d = ch.document
        hits.append(
            ChunkHit(
                chunk_id=ch.id,
                document_id=d.id,
                order=ch.order,
                title=d.title,
                authors=[a.name for a in d.authors.all()],
                citation=d.citation,
                doc_type=d.doc_type,
                year=d.year,
                url=d.url,
                section=ch.section,
                text=ch.text,
                sigla=[s["ref"] for s in ch.sigla],
                score=float(score),
                access=d.access,
                register=d.register,
                owner_id=d.owner_id,
            )
        )
    return hits
