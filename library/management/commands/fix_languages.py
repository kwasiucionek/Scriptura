"""Popraw język dokumentów na podstawie treści (metadane OpenAlex zgadują po tytule).

    python manage.py fix_languages                    # raport: metadane -> wykryty język
    python manage.py fix_languages --apply            # zapis; dla tekstów polskich czyści text_pl
    python manage.py fix_languages --documents 430 950 --apply

Po zmianie na „pl” przekład (text_pl) jest zbędny — był „z polskiego na polski” — i zostaje
wyczyszczony, żeby indeks dostał oryginał. Dokumenty, które z „pl” przeszły na inny język,
wymagają potem translate_chunks. W obu przypadkach: reindex_chunks --documents … (albo --missing).
"""

from collections import Counter

from django.core.management.base import BaseCommand

from library.langdetect import detect_document
from library.models import Chunk, Document


class Command(BaseCommand):
    help = "Wykryj język dokumentów z treści i popraw błędne metadane"

    def add_arguments(self, parser):
        parser.add_argument("--documents", nargs="*", type=int)
        parser.add_argument(
            "--since-id", type=int, default=0, help="tylko dokumenty o id > N"
        )
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **o):
        qs = Document.objects.filter(id__gt=o["since_id"]).order_by("id")
        if o["documents"]:
            qs = qs.filter(id__in=o["documents"])
        changes: list[tuple[int, str, str]] = []
        for doc in qs.only("id", "language", "title"):
            texts = list(doc.chunks.order_by("order").values_list("text", flat=True))
            detected = detect_document(texts)
            if detected and detected != doc.language[:2]:
                changes.append((doc.id, doc.language, detected))
                self.stdout.write(
                    f"  {doc.id:6} {doc.language} -> {detected}  {doc.title[:60]}"
                )
        summary = Counter((old, new) for _, old, new in changes)
        self.stdout.write(
            f"{len(changes)} dokumentów z innym językiem w treści: "
            + ", ".join(f"{a}->{b}: {n}" for (a, b), n in summary.most_common())
        )
        if not o["apply"] or not changes:
            return
        cleared = 0
        for doc_id, _, new in changes:
            Document.objects.filter(id=doc_id).update(language=new)
            if new == "pl":
                cleared += (
                    Chunk.objects.filter(document_id=doc_id)
                    .exclude(text_pl="")
                    .update(text_pl="")
                )
        from library.search import reset_language_cache

        reset_language_cache()
        ids = " ".join(str(i) for i, _, _ in changes)
        self.stdout.write(
            self.style.SUCCESS(
                f"zapisano {len(changes)} zmian języka, wyczyszczono text_pl w {cleared} chunkach"
                f"\nteraz: translate_chunks (nowe obce), reindex_chunks --documents {ids[:200]}"
                + ("…" if len(ids) > 200 else "")
            )
        )
