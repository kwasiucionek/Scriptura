"""Uzupełnij content_hash istniejących dokumentów i wypisz duplikaty (ten sam plik pod kilkoma rekordami).

python manage.py hash_documents
"""

import hashlib
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand

from library.models import Document


class Command(BaseCommand):
    help = "Policz MD5 plików źródłowych i pokaż duplikaty"

    def handle(self, *args, **o):
        by_hash: dict[str, list[Document]] = defaultdict(list)
        for d in Document.objects.exclude(source_path=""):
            p = Path(d.source_path)
            if not p.exists():
                continue
            if not d.content_hash:
                d.content_hash = hashlib.md5(p.read_bytes()).hexdigest()  # noqa: S324
                d.save(update_fields=["content_hash"])
            by_hash[d.content_hash].append(d)
        dups = {h: ds for h, ds in by_hash.items() if len(ds) > 1}
        for h, ds in dups.items():
            self.stdout.write(
                f"{h[:8]}  " + " | ".join(f"#{d.id} {d.title[:45]}" for d in ds)
            )
        self.stdout.write(
            self.style.SUCCESS(f"{len(by_hash)} plików, {len(dups)} grup duplikatów")
        )
