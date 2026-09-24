"""Eksport embeddingów z indeksów OpenSearch do pliku (JSONL: {"id": "<_id>", "v": [...]}),
żeby po przeniesieniu na inny host (Mikrus) nie liczyć ich od nowa.

    python manage.py export_vectors --out data/vectors.jsonl.gz          # chunks + ane
Na serwerze: reindex_chunks --recreate --vectors-file data/vectors.jsonl.gz
             index_ane --recreate --vectors-file data/vectors.jsonl.gz
"""

import gzip
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Eksport wektorów z indeksów chunks i ane do JSONL.gz"

    def add_arguments(self, parser):
        parser.add_argument(
            "--out", default=str(settings.DATA_DIR / "vectors.jsonl.gz")
        )

    def handle(self, *args, **o):
        if settings.SEARCH_BACKEND != "opensearch":
            raise CommandError("SEARCH_BACKEND != opensearch")
        from opensearchpy import helpers

        from corpus.search.client import get_client, index_name

        client = get_client()
        out = Path(o["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with gzip.open(out, "wt", encoding="utf-8") as f:
            for idx in (
                index_name("chunks"),
                index_name("ane"),
                index_name("patristics"),
            ):
                if not client.indices.exists(index=idx):
                    continue
                for hit in helpers.scan(
                    client,
                    index=idx,
                    query={"query": {"match_all": {}}, "_source": ["embedding"]},
                    size=500,
                ):
                    emb = hit["_source"].get("embedding")
                    if emb:
                        f.write(json.dumps({"id": hit["_id"], "v": emb}) + "\n")
                        n += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"{n} wektorów -> {out} ({out.stat().st_size // 1024} KB)"
            )
        )


def load_vectors(path: str | Path) -> dict[str, list[float]]:
    """Wczytanie pliku z export_vectors (używane przez reindex_chunks / index_ane)."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    out: dict[str, list[float]] = {}
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[r["id"]] = r["v"]
    return out
