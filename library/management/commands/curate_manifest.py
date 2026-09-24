"""Ocena wstępna manifestu: reguły + model. Domyślnie tylko raport; --apply wpisuje decyzje.

python manage.py curate_manifest data/manifests/rosik-openalex.jsonl --author "Mariusz Rosik"
python manage.py curate_manifest data/manifests/rosik-openalex.jsonl --author "Mariusz Rosik" --apply
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from library import curate as cur
from library.harvest import manifest


class Command(BaseCommand):
    help = "Kurator manifestu (skip dla szumu i dubletów, review dla niepewnych, rejestr, język)"

    def add_arguments(self, parser):
        parser.add_argument("manifest")
        parser.add_argument("--author", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--no-llm", action="store_true", help="tylko reguły")

    def handle(self, *args, **o):
        path = Path(o["manifest"])
        entries = manifest.read(path)
        report = cur.curate(entries, o["author"], use_llm=not o["no_llm"])
        for d in report.decisions:
            e = entries[d.index]
            if d.access in ("skip", "review") and e.access != d.access:
                self.stdout.write(
                    f"  -> {d.access:6} [{e.access:8}] {e.title[:60]:60} {d.reason}"
                )
        self.stdout.write(
            self.style.SUCCESS(f"{len(entries)} pozycji, decyzje: {report.counts()}")
        )
        if o["apply"]:
            cur.apply(entries, report)
            manifest.write(path, entries)
            self.stdout.write(self.style.SUCCESS(f"zapisano {path}"))
