"""Przebuduj chunki istniejących dokumentów z ich plików źródłowych (po zmianie chunkowania).

    python manage.py reingest_documents            # wszystkie z istniejącym source_path
    python manage.py reingest_documents --documents 38 44
Potem: reindex_chunks --recreate (granice chunków się zmieniły, wektory liczone od nowa).
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from library.ingest import build_chunks
from library.models import Chunk, Document


class Command(BaseCommand):
    help = "Ponowne chunkowanie dokumentów z plików źródłowych"

    def add_arguments(self, parser):
        parser.add_argument("--documents", nargs="*", type=int)

    def handle(self, *args, **o):
        qs = Document.objects.exclude(source_path="")
        if o["documents"]:
            qs = qs.filter(id__in=o["documents"])
        for doc in qs:
            path = Path(doc.source_path)
            if not path.exists():
                self.stderr.write(f"{doc.id:4} brak pliku: {path}")
                continue
            drafts = build_chunks(path)
            with transaction.atomic():
                doc.chunks.all().delete()
                Chunk.objects.bulk_create(
                    [
                        Chunk(
                            document=doc,
                            order=d.order,
                            section=d.section[:300],
                            text=d.text,
                            page_start=d.page_start,
                            page_end=d.page_end,
                            sigla=d.sigla,
                        )
                        for d in drafts
                    ]
                )
                doc.chunk_count = len(drafts)
                doc.save(update_fields=["chunk_count"])
            n_sigla = sum(len(d.sigla) for d in drafts)
            self.stdout.write(
                f"{doc.id:4} {doc.title[:55]:55} {len(drafts):5} chunków, {n_sigla:5} sigli"
            )
