"""Import korpusów do bazy.

python manage.py import_corpus oshb               # WLC (cały ST hebrajski)
python manage.py import_corpus morphgnt --books Mark John
python manage.py import_corpus json --file data/translations/bg.json --code BG1632
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from corpus.importers import json_bible, lxx, morphgnt, oshb
from corpus.importers.base import import_records
from corpus.models import Access, Work, WorkKind

WORKS = {
    "oshb": {
        "code": "WLC",
        "name": "Westminster Leningrad Codex (OSHB, morfologia OSHM)",
        "language": "hbo",
        "kind": WorkKind.ORIGINAL,
        "versification": "mt",
        "license": "WLC: domena publiczna; OSHB/OSHM: CC BY 4.0",
        "source_url": "https://github.com/openscriptures/morphhb",
        "access": Access.OPEN,
    },
    "lxx": {
        "code": "LXX",
        "name": "Septuaginta (Rahlfs 1935, opr. Eliran Wong; lematy OSSP)",
        "language": "grc",
        "kind": WorkKind.ORIGINAL,
        "versification": "lxx",
        "license": "CC BY-NC-SA 4.0 (E. Wong); tekst bazowy CCAT/CATSS — deklaracja użytkownika",
        "source_url": "https://github.com/eliranwong/LXX-Rahlfs-1935",
        "access": Access.OPEN,
    },
    "morphgnt": {
        "code": "SBLGNT",
        "name": "SBL Greek New Testament (MorphGNT)",
        "language": "grc",
        "kind": WorkKind.ORIGINAL,
        "versification": "mt",
        "license": "SBLGNT: CC BY 4.0; MorphGNT: CC BY-SA 3.0",
        "source_url": "https://github.com/morphgnt/sblgnt",
        "access": Access.OPEN,
    },
}


class Command(BaseCommand):
    help = "Importuj OSHB / MorphGNT / przekład JSON do tabel Work, Verse, VerseText, Token"

    def add_arguments(self, parser):
        parser.add_argument("source", choices=["oshb", "morphgnt", "lxx", "json"])
        parser.add_argument(
            "--books", nargs="*", help="OSIS id ksiąg (domyślnie wszystkie)"
        )
        parser.add_argument("--file", help="json: ścieżka do pliku przekładu")
        parser.add_argument("--code", help="json: kod dzieła, np. BG1632")
        parser.add_argument("--name", help="json: nazwa dzieła (domyślnie z pliku)")
        parser.add_argument("--language", default="pl")
        parser.add_argument("--license", default="", dest="license_text")
        parser.add_argument(
            "--access", default=Access.OPEN, choices=[a.value for a in Access]
        )
        parser.add_argument(
            "--keep", action="store_true", help="nie usuwaj poprzedniego importu"
        )

    def handle(self, *args, **opts):
        data: Path = settings.DATA_DIR
        only = set(opts["books"] or []) or None
        source = opts["source"]

        if source in WORKS:
            meta = WORKS[source]
            work, _ = Work.objects.update_or_create(code=meta["code"], defaults=meta)
            if source == "oshb":
                records = oshb.parse_dir(data / "oshb" / "wlc", only)
            elif source == "lxx":
                lexmap = lxx.load_lexmap(
                    data / "lxx" / "Lex_LXXno.csv", data / "lxx" / "OSSP_lexemes.csv"
                )
                records = lxx.parse_file(
                    data / "lxx" / "LXX_final_main.csv", lexmap, only
                )
            else:
                records = morphgnt.parse_dir(data / "morphgnt", only)
        else:
            if not opts["file"] or not opts["code"]:
                raise CommandError("json wymaga --file i --code")
            path = Path(opts["file"])
            meta = json_bible.metadata(path)
            work, _ = Work.objects.update_or_create(
                code=opts["code"],
                defaults={
                    "name": opts["name"] or meta.get("longName") or opts["code"],
                    "language": opts["language"],
                    "kind": WorkKind.TRANSLATION,
                    "year": meta.get("year"),
                    "license": opts["license_text"],
                    "access": opts["access"],
                },
            )
            records = json_bible.parse_file(path, only)

        stats = import_records(work, records, replace=not opts["keep"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{work.code}: {stats['verses']} wersetów, {stats['tokens']} tokenów"
            )
        )
