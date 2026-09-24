"""Maszynowy przekład pasaży Ojców (EN -> PL) do wyszukiwania i czytania w panelu źródeł.
Model w prompcie nadal dostaje oryginał angielski. Ten sam model i zasady co translate_chunks.

    python manage.py translate_patristics                          # wszystkie bez przekładu
    python manage.py translate_patristics --volumes anf01 npnf110  # wybrane tomy
    python manage.py translate_patristics --force
Potem: index_patristics --recreate (pole text_pl trafia do BM25; wektory z --vectors-file zostają).
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from library.management.commands.translate_chunks import translate
from patristics.models import PatPassage


class Command(BaseCommand):
    help = (
        "Przekład pasaży Ojców na polski (maszynowy, tylko do wyszukiwania i podglądu)"
    )

    def add_arguments(self, parser):
        parser.add_argument("--volumes", nargs="*", help="id tomów CCEL, np. anf01")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--model", default=settings.TRANSLATE_MODEL)

    def handle(self, *args, **o):
        qs = PatPassage.objects.select_related("work").order_by("work_id", "order")
        if o["volumes"]:
            qs = qs.filter(work__ccel_id__in=o["volumes"])
        if not o["force"]:
            qs = qs.filter(text_pl="")
        total, done, t0 = qs.count(), 0, time.monotonic()
        self.stdout.write(f"{total} pasaży do przetłumaczenia modelem {o['model']}")
        for p in qs.iterator(chunk_size=50):
            try:
                p.text_pl = translate(
                    p.text_en,
                    "en",
                    o["model"],
                    settings.TRANSLATE_BASE_URL,
                    settings.OLLAMA_API_KEY,
                )
            except Exception as exc:
                self.stderr.write(f"  {p.ref[:60]}: {exc}")
                continue
            p.save(update_fields=["text_pl"])
            done += 1
            if done % 25 == 0:
                rate = (time.monotonic() - t0) / done
                self.stdout.write(
                    f"  {done}/{total} ~{rate:.1f} s/pasaż, zostało ~{(total - done) * rate / 60:.0f} min"
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"przetłumaczono {done}/{total}; teraz: index_patristics --recreate --vectors-file …"
            )
        )
