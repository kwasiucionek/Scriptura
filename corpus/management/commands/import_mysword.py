"""Import przekładu z modułu MySword (*.bbl.mybible) jako dzieła (Work) z przypisami (VerseNote).

    python manage.py import_mysword --file /srv/scriptura/data/licensed/eib/SNPD4_SJ.bbl.mybible \\
        --code SNPD --access private
    python manage.py import_mysword --file … --code SNPD --books Gen Phil   # próba na części

Ponowny import zastępuje tekst i przypisy dzieła (aktualizacja modułu). Dostęp: `private` do
czasu zgody wydawcy (widzi superużytkownik i grupa scriptura-dzielo-KOD), potem `open`.
Plik modułu z tekstem chronionym trzymamy w DATA_DIR/licensed/ — poza repozytorium.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from corpus.importers import mysword
from corpus.importers.base import VerseRecord, import_records
from corpus.models import Access, VerseNote, VerseText, Work, WorkKind

SNPD_DEFAULTS = {
    "name": "Biblia, to jest Pismo Święte Starego i Nowego Przymierza. Przekład dosłowny z przypisami (EIB)",
    "year": 2018,
    "license": "© Ewangeliczny Instytut Biblijny — licencja niekomercyjna dla Scriptury",
    "attribution": "Przekład dosłowny z przypisami (SNPD) © Ewangeliczny Instytut Biblijny "
    "2018–2026, feib.pl; przekład i przypisy: Piotr Zaremba",
    "source_url": "https://feib.pl/",
}
BATCH = 2000


class Command(BaseCommand):
    help = "Importuj przekład z modułu MySword (*.bbl.mybible) z przypisami"

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--code", required=True, help="kod dzieła, np. SNPD")
        parser.add_argument("--name", default="")
        parser.add_argument(
            "--access", default=Access.PRIVATE, choices=[a.value for a in Access]
        )
        parser.add_argument(
            "--books", nargs="*", help="OSIS ksiąg (domyślnie wszystkie)"
        )
        parser.add_argument("--language", default="pl")

    def handle(self, *args, **o):
        path = Path(o["file"])
        if not path.exists():
            raise CommandError(f"brak pliku {path}")
        info = mysword.details(path)
        defaults = dict(SNPD_DEFAULTS) if o["code"].upper() == "SNPD" else {}
        defaults.update(
            {
                "language": o["language"],
                "kind": WorkKind.TRANSLATION,
                "versification": "mt",
                "access": o["access"],
                "notes": f"Moduł MySword {info.get('Abbreviation', '')} "
                f"v{info.get('Version', '')} ({info.get('VersionDate', '')})",
            }
        )
        if o["name"]:
            defaults["name"] = o["name"]
        defaults.setdefault("name", info.get("Title") or o["code"])
        work, _ = Work.objects.update_or_create(code=o["code"], defaults=defaults)

        ref_url = reverse("corpus:reference")
        books = set(o["books"]) if o["books"] else None
        parsed = list(mysword.parse_module(path, books, ref_url))
        if not parsed:
            raise CommandError(
                "moduł nie zawiera wersetów (albo filtr ksiąg nic nie zostawił)"
            )
        stats = import_records(
            work,
            iter(VerseRecord(p.osis, p.chapter, p.verse, p.text) for p in parsed),
        )
        by_key = {
            vt.verse.osis_id: vt
            for vt in VerseText.objects.filter(work=work).select_related("verse")
        }
        texts, notes = [], []
        for p in parsed:
            vt = by_key.get(f"{p.osis}.{p.chapter}.{p.verse}")
            if vt is None:
                continue
            vt.markup = p.markup
            texts.append(vt)
            notes += [
                VerseNote(
                    verse_text=vt,
                    kind=n.kind,
                    label=n.label,
                    order=i,
                    html=n.html,
                    text=n.text,
                )
                for i, n in enumerate(p.notes)
            ]
        for i in range(
            0, len(texts), BATCH
        ):  # krótkie transakcje: strona działa w trakcie
            with transaction.atomic():
                VerseText.objects.bulk_update(texts[i : i + BATCH], ["markup"])
        for i in range(0, len(notes), BATCH):
            with transaction.atomic():
                VerseNote.objects.bulk_create(notes[i : i + BATCH])
        n_notes = sum(1 for n in notes if n.kind == VerseNote.Kind.NOTE)
        self.stdout.write(
            self.style.SUCCESS(
                f"{work.code}: {stats['verses']} wersetów, {n_notes} przypisów, "
                f"{len(notes) - n_notes} odsyłaczy; dostęp: {work.access}"
            )
        )
