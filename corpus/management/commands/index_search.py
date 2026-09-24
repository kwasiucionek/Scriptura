"""Zbuduj/odśwież indeks OpenSearch z wersetów w bazie.

python manage.py index_search --recreate
python manage.py index_search --works BG1632
"""

from django.core.management.base import BaseCommand

from corpus.models import Work
from corpus.search.indexer import reindex


class Command(BaseCommand):
    help = "Indeksuj wersety (VerseText + zdenormalizowane tokeny) do OpenSearch"

    def add_arguments(self, parser):
        parser.add_argument(
            "--works", nargs="*", help="kody dzieł (domyślnie wszystkie)"
        )
        parser.add_argument(
            "--recreate", action="store_true", help="usuń i utwórz indeks od nowa"
        )

    def handle(self, *args, **opts):
        works = (
            list(Work.objects.filter(code__in=opts["works"])) if opts["works"] else None
        )
        n = reindex(works, recreate=opts["recreate"])
        self.stdout.write(self.style.SUCCESS(f"Zaindeksowano {n} dokumentów"))
