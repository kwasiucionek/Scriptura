"""Przebuduj indeks `chunks` w OpenSearch dla wszystkich (lub wybranych) dokumentów.

    python manage.py reindex_chunks --recreate                    # nowy model embeddingów -> od zera
    python manage.py reindex_chunks --recreate --reuse-vectors    # zmiana mappingu bez liczenia wektorów
    python manage.py reindex_chunks --documents 3 7 --no-vectors
    python manage.py reindex_chunks --recreate --suffix arctic    # osobny indeks do A/B
    python manage.py reindex_chunks --missing                     # tylko dokumenty niepełne w indeksie
                                                                   # (np. po ingest_manifest --no-index)
    python manage.py reindex_chunks --update-mapping              # nowe pola mappingu w istniejącym indeksie
                                                                   # (w tle, bez przerwy w wyszukiwaniu)

Po zmianie OLLAMA_EMBED_MODEL / EMBEDDING_DIM zawsze --recreate bez --reuse-vectors
(wymiar pola knn_vector jest zamrożony w mappingu, a stare wektory są z innej przestrzeni).
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from library.models import Chunk, Document


class Command(BaseCommand):
    help = "Reindeksuj chunki do OpenSearch (z embeddingami wg OLLAMA_EMBED_MODEL)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--documents",
            nargs="*",
            type=int,
            help="id dokumentów (domyślnie wszystkie)",
        )
        parser.add_argument("--recreate", action="store_true")
        parser.add_argument(
            "--reuse-vectors",
            action="store_true",
            help="zbierz istniejące embeddingi przed --recreate i użyj ich ponownie",
        )
        parser.add_argument("--no-vectors", action="store_true")
        parser.add_argument(
            "--missing",
            action="store_true",
            help="tylko dokumenty, których chunków w indeksie jest mniej/więcej niż w bazie",
        )
        parser.add_argument(
            "--vectors-file",
            help="plik z export_vectors (JSONL[.gz]) zamiast liczenia embeddingów",
        )
        parser.add_argument(
            "--suffix", default="", help="sufiks nazwy indeksu, np. 'arctic'"
        )
        parser.add_argument(
            "--update-mapping",
            action="store_true",
            help="dopisz nowe pola mappingu (addytywnie) i przelicz je w miejscu, bez przebudowy",
        )

    def handle(self, *args, **o):
        if settings.SEARCH_BACKEND != "opensearch":
            raise CommandError("SEARCH_BACKEND != opensearch")
        from corpus.search.client import index_name
        from library import search

        index = index_name("chunks") + (f"-{o['suffix']}" if o["suffix"] else "")
        if o["update_mapping"]:
            task = search.update_mapping(index)
            if task is None:
                raise CommandError(f"brak indeksu {index}")
            self.stdout.write(
                f"mapping {index} zaktualizowany; przeliczanie w tle: zadanie {task} "
                f"(GET _tasks/{task})"
            )
            return
        cached = (
            search.fetch_vectors(index, o["documents"]) if o["reuse_vectors"] else {}
        )
        if o["reuse_vectors"]:
            self.stdout.write(f"zebrano {len(cached)} istniejących wektorów")
        if o["vectors_file"]:
            from library.management.commands.export_vectors import load_vectors

            cached.update(load_vectors(o["vectors_file"]))
            self.stdout.write(f"wczytano {len(cached)} wektorów z pliku")
        search.ensure_index(recreate=o["recreate"], index=index)

        qs = Document.objects.prefetch_related("authors").order_by("id")
        if o["documents"]:
            qs = qs.filter(id__in=o["documents"])
        if o["missing"]:
            have = search.indexed_counts(index)
            in_db = dict(
                Chunk.objects.values("document_id")
                .annotate(n=Count("id"))
                .values_list("document_id", "n")
            )
            ids = [d for d, n in in_db.items() if have.get(d, 0) != n]
            qs = qs.filter(id__in=ids)
            self.stdout.write(f"{len(ids)} dokumentów niepełnych w indeksie {index}")
        total = 0
        for doc in qs:
            n = search.index_document(
                doc,
                with_vectors=not o["no_vectors"],
                index=index,
                cached_vectors=cached,
            )
            total += n
            self.stdout.write(f"{doc.id:4} {doc.title[:60]:60} {n} chunków")
        self.stdout.write(
            self.style.SUCCESS(
                f"Razem {total} chunków w {index}, model {settings.OLLAMA_EMBED_MODEL}"
            )
        )
