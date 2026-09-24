"""Scal dublety autorów („Marcin Majewski” vs „Majewski, Marcin”, różnice w diakrytykach).

python manage.py merge_authors            # podgląd
python manage.py merge_authors --apply    # scalenie: zostaje forma „Imię Nazwisko” (lub najczęstsza)
"""

import unicodedata
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from library.models import Author
from rag.citations import split_name


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


class Command(BaseCommand):
    help = "Scal dublety autorów (Nazwisko, Imię / Imię Nazwisko)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **o):
        groups: dict[tuple, list[Author]] = defaultdict(list)
        for a in Author.objects.all():
            last, first = split_name(a.name)
            groups[(_fold(last), _fold(first.split(" ")[0] if first else ""))].append(a)
        dups = {k: v for k, v in groups.items() if len(v) > 1}
        if not dups:
            self.stdout.write("brak dubletów")
            return
        for members in dups.values():
            # kanoniczny: forma „Imię Nazwisko” (bez przecinka), a przy remisie — z największą liczbą dokumentów
            members.sort(key=lambda a: ("," in a.name, -a.documents.count()))
            keep, rest = members[0], members[1:]
            self.stdout.write(
                f"{keep.name!r}  <- " + ", ".join(repr(a.name) for a in rest)
            )
            if not o["apply"]:
                continue
            with transaction.atomic():
                for a in rest:
                    for d in a.documents.all():
                        d.authors.add(keep)
                        d.authors.remove(a)
                    a.delete()
        self.stdout.write(
            self.style.SUCCESS("scalono" if o["apply"] else "podgląd — dodaj --apply")
        )
