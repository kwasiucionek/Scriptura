"""Import leksykonu STEPBible do tabeli Lexeme i powiązanie tokenów z hasłami:
puste lematy WLC uzupełniane po Strongu, puste Strongi SBLGNT — po lemacie.

python manage.py fetch_sources step
python manage.py import_lexicon                # TBESH + TBESG z data/step/
python manage.py import_lexicon --no-fill      # bez uzupełniania tokenów
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
        # aktualizacja zamiast kasowania: pola spoza STEP (gloss_pl, strong_*) zostają
        fields = [k for k in rows[0] if k != "strong"] if rows else []
        existing = {x.strong: x for x in Lexeme.objects.all()}
        new, changed = [], []
        for r in rows:
            obj = existing.get(r["strong"])
            if obj is None:
                new.append(Lexeme(**r))
                continue
            for k in fields:
                setattr(obj, k, r[k])
            changed.append(obj)
        with transaction.atomic():
            Lexeme.objects.bulk_create(new, batch_size=2000)
            Lexeme.objects.bulk_update(changed, fields, batch_size=2000)
        self.stdout.write(
            f"Lexeme: {len(rows)} haseł ({len(new)} nowych, {len(changed)} zaktualizowanych)"
        )
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
        self._fill_greek_strongs()

    def _fill_greek_strongs(self):
        """SBLGNT (MorphGNT) nie ma numerów Stronga — dopisujemy je po lemacie (interlinia,
        strona słownika, konkordancja po Strongu)."""
        index = step.GreekStrongIndex(
            Lexeme.objects.filter(language="grc").values_list(
                "strong", "lemma", "lemma_norm"
            )
        )
        pairs = list(
            Token.objects.filter(verse_text__work__language="grc", strong="")
            .exclude(lemma="")
            .values_list("lemma", "lemma_norm")
            .distinct()
        )
        filled, missing = 0, set()
        with transaction.atomic():
            for lemma, lemma_norm in pairs:
                strong = index.lookup(lemma, lemma_norm)
                if not strong:
                    missing.add(lemma)
                    continue
                filled += Token.objects.filter(
                    verse_text__work__language="grc", strong="", lemma=lemma
                ).update(strong=strong)
        self.stdout.write(
            self.style.SUCCESS(
                f"grecki Strong po lemacie: {filled} tokenów, bez dopasowania {len(missing)} lematów"
            )
        )
