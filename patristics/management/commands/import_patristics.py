"""Import tomów ANF/NPNF z CCEL (ThML) do bazy patrystycznej.

    python manage.py import_patristics anf01 anf02          # wybrane tomy
    python manage.py import_patristics --all                 # 37 tomów (~150 MB XML, kilkadziesiąt tys. pasaży)
    python manage.py import_patristics anf01 --from-file data/patristics/anf01.xml
Cache XML w data/patristics/. Po imporcie: index_patristics --recreate (embeddingi!).
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from patristics import importers
from patristics.thml import parse_volume


class Command(BaseCommand):
    help = "Import tomów CCEL (ANF/NPNF) do bazy Ojców"

    def add_arguments(self, parser):
        parser.add_argument(
            "volumes", nargs="*", help="id tomów CCEL, np. anf01 npnf101"
        )
        parser.add_argument("--all", action="store_true")
        parser.add_argument(
            "--from-file", help="lokalny plik XML zamiast pobierania (jeden tom)"
        )
        parser.add_argument("--max-chars", type=int, default=1500)

    def handle(self, *args, **o):
        cache = settings.DATA_DIR / "patristics"
        ids = list(importers.VOLUMES) if o["all"] else o["volumes"]
        if not ids:
            self.stderr.write("podaj tomy albo --all")
            return
        for cid in ids:
            try:
                data = (
                    Path(o["from_file"]).read_bytes()
                    if o["from_file"]
                    else importers.fetch_volume(cid, cache)
                )
                works = parse_volume(data, max_chars=o["max_chars"])
                nw, np_ = importers.save_volume(cid, works)
            except Exception as exc:
                self.stderr.write(f"{cid}: błąd — {exc}")
                continue
            self.stdout.write(f"{cid}: {nw} dzieł, {np_} pasaży")
        self.stdout.write(
            self.style.SUCCESS("gotowe — teraz: index_patristics --recreate")
        )
