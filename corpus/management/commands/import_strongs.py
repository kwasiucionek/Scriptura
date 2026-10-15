"""Import słowników Stronga do haseł Lexeme (strong_def, strong_deriv, strong_kjv, strong_pron).

python manage.py fetch_sources strongs
python manage.py import_strongs
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from corpus.importers import strongs
from corpus.models import Lexeme

FILES = ["strongs-greek-dictionary.js", "strongs-hebrew-dictionary.js"]
FIELDS = ["strong_def", "strong_deriv", "strong_kjv", "strong_pron"]


class Command(BaseCommand):
    help = "Import słowników Stronga (Open Scriptures JSON) do leksykonu"

    def handle(self, *args, **o):
        data = {}
        for name in FILES:
            path = settings.DATA_DIR / "strongs" / name
            if not path.exists():
                raise CommandError(f"brak {path} — uruchom fetch_sources strongs")
            data.update(strongs.parse_js(path))
        changed = []
        for lex in Lexeme.objects.all():
            entry = data.get(strongs.base_number(lex.strong))
            if not entry:
                continue
            for k, v in strongs.entry_fields(entry).items():
                setattr(lex, k, v)
            changed.append(lex)
        with transaction.atomic():
            Lexeme.objects.bulk_update(changed, FIELDS, batch_size=2000)
        self.stdout.write(
            self.style.SUCCESS(
                f"Strong: {len(data)} haseł w słownikach, uzupełniono {len(changed)} wpisów leksykonu"
            )
        )
