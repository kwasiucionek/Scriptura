"""Ewaluacja retrievalu na zestawie pytań z oznaczonymi właściwymi chunkami.

    python scripts/eval_retrieval.py data/eval/questions.jsonl [--k 5] [--no-rerank]

Format JSONL (jedna linia = jedno pytanie):
  {"q": "argumenty za wtórnością Mk 16,9-20", "type": "pl",
   "relevant": ["<document_id>:<order>", "12:3"], "authors": [], "note": "..."}
  type: pl | pl+orig (termin grecki/hebrajski w pytaniu) | pl->en (odpowiedź w angielskim tekście)

Metryki per typ i łącznie: recall@k, near±1 (trafienie w chunk sąsiedni — błąd granicy,
nie rankingu), MRR@k, hit@1. Przy --no-rerank mierzy samą hybrydę (RRF),
bez flagi — pełny pipeline (RRF -> reranker). Uruchom dwa razy, żeby zobaczyć, ile daje reranker.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from corpus.sigla import extract, ordinal_range  # noqa: E402
from library.search import retrieve  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true")
    a = ap.parse_args()
    if a.no_rerank:
        settings.RERANKER_BACKEND = "none"

    rows = [
        json.loads(line)
        for line in Path(a.file).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    stats: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0, "recall": 0.0, "near": 0.0, "mrr": 0.0, "hit1": 0.0}
    )
    misses = []
    for row in rows:
        q, rel = row["q"], set(row["relevant"])
        sigla = [ordinal_range(r) for m in extract(q) for r in m.refs] or None
        hits = retrieve(q, k=a.k, authors=row.get("authors") or None, sigla=sigla)
        ids = [f"{h.document_id}:{h.order}" for h in hits]
        found = [i for i, x in enumerate(ids) if x in rel]
        rel_pairs = {(int(a), int(b)) for a, b in (r.split(":") for r in rel)}
        near = any(
            (h.document_id, o) in rel_pairs
            for h in hits
            for o in (h.order - 1, h.order, h.order + 1)
        )
        for key in ("all", row.get("type", "pl")):
            s = stats[key]
            s["n"] += 1
            s["recall"] += len(set(ids) & rel) / max(len(rel), 1)
            s["near"] += 1.0 if near else 0.0
            s["mrr"] += 1.0 / (found[0] + 1) if found else 0.0
            s["hit1"] += 1.0 if found and found[0] == 0 else 0.0
        if not found:
            misses.append((q, ids[:3]))

    mode = "RRF" if a.no_rerank else f"RRF -> {settings.RERANKER_BACKEND}"
    print(
        f"model={settings.OLLAMA_EMBED_MODEL} backend={settings.SEARCH_BACKEND} tryb={mode} k={a.k}"
    )
    print(
        f"{'typ':10} {'n':>4} {'recall@k':>9} {'near±1':>7} {'MRR@k':>7} {'hit@1':>6}"
    )
    for key, s in sorted(stats.items(), key=lambda kv: kv[0] != "all"):
        n = s["n"]
        print(
            f"{key:10} {n:4} {s['recall'] / n:9.3f} {s['near'] / n:7.3f} "
            f"{s['mrr'] / n:7.3f} {s['hit1'] / n:6.3f}"
        )
    if misses:
        print("\nBez trafienia:")
        for q, top in misses:
            print(f"  - {q!r} -> {top}")


if __name__ == "__main__":
    main()
