"""Import leksykonu STEPBible do tabeli Lexeme i uzupełnienie pustych lematów tokenów po Strongu.

python manage.py fetch_sources step
python manage.py import_lexicon                # TBESH + TBESG z data/step/
python manage.py import_lexicon --no-fill      # bez uzupełniania Token.lemma
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from corpus.importers import step
from corpus.models import Lexeme, Token

FILES = ["TBESH.txt", "TBESG.txt"]


class Command(BaseCommand):
    help = "Import leksykonów STEPBible (TBESH/TBESG) i uzupełnienie lematów WLC"

    def add_arguments(self, parser):
        parser.add_argument("--no-fill", action="store_true")

    def handle(self, *args, **o):
        data: Path = settings.DATA_DIR / "step"
        rows = []
        for name in FILES:
            path = data / name
            if not path.exists():
                self.stderr.write(f"brak {path} — uruchom fetch_sources step")
                continue
            rows += list(step.parse_file(path))
        with transaction.atomic():
            Lexeme.objects.all().delete()
            Lexeme.objects.bulk_create([Lexeme(**r) for r in rows], batch_size=2000)
        self.stdout.write(f"Lexeme: {len(rows)} haseł")
        if o["no_fill"]:
            return
        lex = {r["strong"]: r for r in rows}
        strongs = list(
            Token.objects.filter(lemma="")
            .exclude(strong="")
            .values_list("strong", flat=True)
            .distinct()
        )
        filled = 0
        with transaction.atomic():
            for s in strongs:
                r = lex.get(s) or lex.get(s.rstrip("abcdefghijklmnopqrstuvwxyz"))
                if not r:
                    continue
                filled += Token.objects.filter(strong=s, lemma="").update(
                    lemma=r["lemma"], lemma_norm=r["lemma_norm"]
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"uzupełniono lematy w {filled} tokenach ({len(strongs)} Strongów)"
            )
        )
