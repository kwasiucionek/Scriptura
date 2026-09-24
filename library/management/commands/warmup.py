"""Rozgrzewka backendów po starcie: embedding + reranker, żeby pierwsze pytanie nie płaciło za ładowanie.

    python manage.py warmup
systemd:  ExecStartPost=/usr/bin/env -C /path/app .venv/bin/python manage.py warmup
cron:     0 */3 * * *  cd /path/app && .venv/bin/python manage.py warmup
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rozgrzej embeddingi (Ollama/TEI) i reranker (TEI)"

    def handle(self, *args, **o):
        if settings.SEARCH_BACKEND == "opensearch":
            from library.embeddings import embed

            t = time.monotonic()
            embed(["rozgrzewka"], is_query=True)
            self.stdout.write(f"embed: {int((time.monotonic() - t) * 1000)} ms")
        if settings.RERANKER_BACKEND == "tei":
            from library.rerank import _tei_rerank

            t = time.monotonic()
            _tei_rerank("rozgrzewka", ["Krótki tekst pierwszy.", "Krótki tekst drugi."])
            self.stdout.write(f"rerank: {int((time.monotonic() - t) * 1000)} ms")
