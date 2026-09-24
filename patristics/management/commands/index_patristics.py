"""Indeks pasaży Ojców w OpenSearch (z embeddingami — dla 37 tomów to kilkadziesiąt tysięcy wektorów).

python manage.py index_patristics --recreate [--no-vectors] [--vectors-file data/vectors.jsonl.gz]
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from patristics import search


class Command(BaseCommand):
    help = "Zaindeksuj pasaże patrystyczne (osobny indeks `patristics`)"

    def add_arguments(self, parser):
        parser.add_argument("--recreate", action="store_true")
        parser.add_argument("--no-vectors", action="store_true")
        parser.add_argument("--vectors-file")

    def handle(self, *args, **o):
        if settings.SEARCH_BACKEND != "opensearch":
            raise CommandError("SEARCH_BACKEND != opensearch")
        cached = {}
        if o["vectors_file"]:
            from library.management.commands.export_vectors import load_vectors

            cached = load_vectors(o["vectors_file"])
        search.ensure_index(recreate=o["recreate"])
        n = search.index_all(with_vectors=not o["no_vectors"], cached=cached)
        self.stdout.write(
            self.style.SUCCESS(
                f"patristics: {n} pasaży w indeksie {search.index_name()}"
            )
        )
