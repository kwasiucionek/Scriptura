"""Przelicz Chunk.sigla z tekstu dla wszystkich (lub wybranych) dokumentów — po zmianie parsera.

    python manage.py recompute_sigla
    python manage.py recompute_sigla --documents 46 47
Potem: reindex_chunks --recreate (indeks trzyma kopię sigli).
"""

from django.core.management.base import BaseCommand

from library.ingest import sigla_for
from library.models import Chunk


class Command(BaseCommand):
    help = "Przelicz sigla chunków z ich tekstu"

    def add_arguments(self, parser):
        parser.add_argument("--documents", nargs="*", type=int)

    def handle(self, *args, **o):
        qs = Chunk.objects.all()
        if o["documents"]:
            qs = qs.filter(document_id__in=o["documents"])
        changed = 0
        batch: list[Chunk] = []
        for ch in qs.iterator(chunk_size=500):
            new = sigla_for(ch.text)
            if new != ch.sigla:
                ch.sigla = new
                batch.append(ch)
                changed += 1
            if len(batch) >= 500:
                Chunk.objects.bulk_update(batch, ["sigla"])
                batch = []
        if batch:
            Chunk.objects.bulk_update(batch, ["sigla"])
        self.stdout.write(
            self.style.SUCCESS(f"zmieniono sigla w {changed} chunkach z {qs.count()}")
        )
