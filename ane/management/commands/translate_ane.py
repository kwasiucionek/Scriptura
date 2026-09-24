"""Maszynowy przekład pasaży ANE (EN -> PL) do wyszukiwania — ten sam model i zasady co translate_chunks.

    python manage.py translate_ane [--force]
Potem: index_ane --recreate.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from ane.models import AnePassage
from library.management.commands.translate_chunks import translate


class Command(BaseCommand):
    help = "Przekład pasaży ANE na polski (tylko do wyszukiwania)"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--model", default=settings.TRANSLATE_MODEL)

    def handle(self, *args, **o):
        qs = AnePassage.objects.select_related("chapter__text").order_by("id")
        if not o["force"]:
            qs = qs.filter(translation_pl="")
        total, done, t0 = qs.count(), 0, time.monotonic()
        self.stdout.write(f"{total} pasaży do przetłumaczenia modelem {o['model']}")
        for p in qs.iterator(chunk_size=50):
            try:
                p.translation_pl = translate(
                    p.translation_en,
                    "en",
                    o["model"],
                    settings.TRANSLATE_BASE_URL,
                    settings.OLLAMA_API_KEY,
                )
            except Exception as exc:
                self.stderr.write(f"  {p.ref}: {exc}")
                continue
            p.save(update_fields=["translation_pl"])
            done += 1
            if done % 10 == 0:
                rate = (time.monotonic() - t0) / done
                self.stdout.write(f"  {done}/{total} ~{rate:.1f} s/pasaż")
        self.stdout.write(
            self.style.SUCCESS(
                f"przetłumaczono {done}/{total}; teraz: index_ane --recreate"
            )
        )
