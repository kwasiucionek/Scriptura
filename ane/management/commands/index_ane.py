"""Indeks pasaży ANE w OpenSearch.

python manage.py index_ane --recreate
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ane import search


class Command(BaseCommand):
    help = "Zaindeksuj pasaże ANE (osobny indeks `ane`)"

    def add_arguments(self, parser):
        parser.add_argument("--recreate", action="store_true")
        parser.add_argument("--no-vectors", action="store_true")
        parser.add_argument(
            "--vectors-file", help="plik z export_vectors zamiast liczenia embeddingów"
        )

    def handle(self, *args, **o):
        if settings.SEARCH_BACKEND != "opensearch":
            raise CommandError("SEARCH_BACKEND != opensearch")
        search.ensure_index(recreate=o["recreate"])
        cached = {}
        if o["vectors_file"]:
            from library.management.commands.export_vectors import load_vectors

            cached = load_vectors(o["vectors_file"])
        n = search.index_all(with_vectors=not o["no_vectors"], cached=cached)
        self.stdout.write(
            self.style.SUCCESS(f"ANE: {n} pasaży w indeksie {search.index_name()}")
        )
