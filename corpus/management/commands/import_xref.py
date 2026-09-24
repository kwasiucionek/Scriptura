"""Import cross-references OpenBible.info do tabeli VerseLink.

python manage.py fetch_sources xref            # cross-references.zip -> data/xref/cross_references.txt
python manage.py import_xref [--min-votes 0]
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from corpus.importers import xref
from corpus.models import VerseLink


class Command(BaseCommand):
    help = "Import powiązań wersetów (OpenBible.info, CC BY)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", default=str(settings.DATA_DIR / "xref" / "cross_references.txt")
        )
        parser.add_argument(
            "--min-votes",
            type=int,
            default=0,
            help="pomiń powiązania z liczbą głosów poniżej progu",
        )

    def handle(self, *args, **o):
        path = Path(o["file"])
        if not path.exists():
            self.stderr.write(f"brak {path} — uruchom fetch_sources xref")
            return
        rows = list(xref.parse_file(path, o["min_votes"]))
        with transaction.atomic():
            VerseLink.objects.all().delete()
            VerseLink.objects.bulk_create(
                [
                    VerseLink(from_ordinal=a, to_start=b, to_end=c, votes=v)
                    for a, b, c, v in rows
                ],
                batch_size=5000,
            )
        self.stdout.write(self.style.SUCCESS(f"VerseLink: {len(rows)} powiązań"))
