"""Smoke-test integracji z żywym OpenSearch (uruchom po `docker compose up -d`).

    SEARCH_BACKEND=opensearch python scripts/opensearch_smoke.py

Sprawdza: analizatory (Stempel, ICU folding), NEAR, Strong+słowo, highlight, aggs.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from corpus.search.client import get_client, index_name  # noqa: E402
from corpus.search.indexer import reindex  # noqa: E402
from corpus.services import text as svc  # noqa: E402

client = get_client()
print("cluster:", client.info()["version"]["number"])
plugins = {p["component"] for p in client.cat.plugins(format="json")}
assert {"analysis-stempel", "analysis-icu"} <= plugins, f"brak pluginów: {plugins}"

n = reindex(recreate=True)
print("zaindeksowano:", n)

idx = index_name("verses")
for analyzer, text in [
    ("pl_stem", "Bogami stworzeniami"),
    ("grc_fold", "Ἀρχὴ λόγος"),
    ("hbo_fold", "בְּרֵאשִׁ֖ית"),
]:
    toks = client.indices.analyze(index=idx, body={"analyzer": analyzer, "text": text})[
        "tokens"
    ]
    print(f"{analyzer:9}", [t["token"] for t in toks])

works = svc.active_works()
for q in [
    "Bóg NEAR/2 niebo",
    '"na początku" -niebo',
    "H430 niebo",
    "lemma:ἀρχή",
    "logos",
    "elohim",
]:
    r = svc.lexical(q, works, limit=3)
    print(f"{q!r:28} total={r.total:5}  by_work={r.by_work}")
    for h in r.hits[:2]:
        print("   ", h.ref, h.work, h.html[:90])
