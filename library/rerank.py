"""Reranker kandydatów z RRF.

RERANKER_BACKEND:
  tei  — model par (BAAI/bge-reranker-v2-m3) przez TEI /rerank (lokalnie, GPU/CPU)
  llm  — model czatu (chmura Ollamy) ocenia trafność każdego kandydata 0–3 w jednym wywołaniu;
         bez GPU, ~1 wywołanie na pytanie; kandydaci poniżej RERANK_LLM_MIN_SCORE odpadają
         (mniej nietrafionych źródeł w panelu, wcześniejsza uczciwa odmowa)
  none — kolejność RRF bez zmian
"""

import json
import logging
import re
import urllib.request

from django.conf import settings

from library.search import ChunkHit

log = logging.getLogger(__name__)

LLM_PROMPT = """Oceniasz przydatność fragmentów literatury biblistycznej do odpowiedzi na pytanie.
Skala: 3 = fragment wprost odpowiada na pytanie lub omawia dokładnie ten problem; 2 = istotny kontekst
(ten sam tekst biblijny, termin lub zagadnienie, ale nie wprost); 1 = luźno powiązany; 0 = nie na temat
(zbieżność samych słów, recenzja innej książki, inny problem).

PYTANIE: {question}

FRAGMENTY:
{items}

Odpowiedz WYŁĄCZNIE listą JSON obiektów {{"i": numer, "s": ocena}} dla wszystkich fragmentów."""


def rerank(query: str, hits: list[ChunkHit], top_k: int) -> list[ChunkHit]:
    """Zwraca top_k trafień w kolejności rerankera; przy błędzie usługi — kolejność wejściowa."""
    backend = settings.RERANKER_BACKEND
    if backend not in ("tei", "llm") or not hits:
        return hits[:top_k]
    candidates = hits[: settings.RERANK_TOP_N]
    texts = [
        f"{h.title}\n{h.section}\n{h.text}"[: settings.RERANK_MAX_CHARS]
        for h in candidates
    ]
    try:
        scores = (
            _tei_rerank(query, texts)
            if backend == "tei"
            else _llm_rerank(query, candidates)
        )
    except Exception as exc:
        log.warning("reranker niedostępny, zostaje kolejność RRF: %s", exc)
        return hits[:top_k]
    for h, s in zip(candidates, scores, strict=True):
        h.score = s
    ranked = sorted(candidates, key=lambda h: -h.score)
    if backend == "llm":
        # poniżej progu odpada; pusta lista = literatura milczy (odpowiedź z wersetów/leksykonu albo odmowa)
        return [h for h in ranked if h.score >= settings.RERANK_LLM_MIN_SCORE][:top_k]
    return ranked[:top_k]


def _tei_rerank(query: str, texts: list[str]) -> list[float]:
    req = urllib.request.Request(
        f"{settings.TEI_RERANK_URL}/rerank",
        data=json.dumps(
            {"query": query, "texts": texts, "raw_scores": False, "truncate": True}
        ).encode(),
        headers={
            "Content-Type": "application/json",
            **(
                {"Authorization": f"Bearer {settings.TEI_TOKEN}"}
                if settings.TEI_TOKEN
                else {}
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        rows = json.load(resp)  # [{"index": i, "score": s}, ...] posortowane malejąco
    scores = [0.0] * len(texts)
    for r in rows:
        scores[r["index"]] = float(r["score"])
    return scores


def _llm_rerank(query: str, candidates: list[ChunkHit]) -> list[float]:
    """Ocena 0–3 każdego kandydata jednym wywołaniem modelu (JSON)."""
    from library.curate import chat_json

    items = "\n\n".join(
        f"[{i}] {h.authors[0] if h.authors else '?'}, „{h.title[:90]}”"
        + (f" — {h.section[:60]}" if h.section else "")
        + f"\n{h.text[: settings.RERANK_LLM_MAX_CHARS]}"
        for i, h in enumerate(candidates, start=1)
    )
    rows = chat_json(
        LLM_PROMPT.format(question=query, items=items), num_predict=600, attempts=2
    )
    scores = [0.0] * len(candidates)
    rated = 0
    for r in rows:
        try:
            i, s = int(r.get("i")), float(r.get("s"))
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(candidates):
            scores[i - 1] = max(0.0, min(3.0, s))
            rated += 1
    # same zera są poprawnym wynikiem („nic nie jest na temat”); awaria = brak ocen dla większości
    if rated < max(1, len(candidates) // 2):
        raise RuntimeError(f"reranker LLM ocenił {rated}/{len(candidates)} kandydatów")
    return scores


def parse_scores(text: str, n: int) -> list[float]:  # pomocnicze do testów/debugowania
    rows = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
    out = [0.0] * n
    for r in rows:
        out[int(r["i"]) - 1] = float(r["s"])
    return out
