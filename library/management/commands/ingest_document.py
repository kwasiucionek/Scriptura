"""Ingestia pliku do warstwy literatury (+ indeks OpenSearch, jeśli włączony).

    python manage.py ingest_document --file art.pdf --title "Tytuł" --author "Mariusz Rosik" \
        --type article --journal "Wrocławski Przegląd Teologiczny" --year 2021 --doi 10.x/y \
        --url https://... --license "CC BY 4.0" --access open
"""

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from corpus.models import Access
from library.ingest import build_chunks
from library.models import DEFAULT_REGISTER, Author, Chunk, DocType, Document, Register


class Command(BaseCommand):
    help = (
        "Wczytaj dokument (pdf/txt/md), potnij na chunki, wyciągnij sigla, zaindeksuj"
    )

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--title", required=True)
        parser.add_argument(
            "--author", action="append", default=[], help="można podać wielokrotnie"
        )
        parser.add_argument(
            "--type", default=DocType.ARTICLE, choices=[t.value for t in DocType]
        )
        parser.add_argument("--journal", default="")
        parser.add_argument("--year", type=int)
        parser.add_argument("--volume", default="")
        parser.add_argument("--pages", default="")
        parser.add_argument("--doi", default="")
        parser.add_argument("--url", default="")
        parser.add_argument("--language", default="pl")
        parser.add_argument(
            "--register",
            choices=[r.value for r in Register],
            help="domyślnie wg --type (artykuł/książka: naukowy)",
        )
        parser.add_argument("--license", default="", dest="license_text")
        parser.add_argument(
            "--access", default=Access.OPEN, choices=[a.value for a in Access]
        )
        parser.add_argument("--no-index", action="store_true")
        parser.add_argument(
            "--allow-duplicate",
            action="store_true",
            help="indeksuj mimo identycznego pliku w bazie",
        )
        parser.add_argument("--no-vectors", action="store_true")

    def handle(self, *args, **o):
        path = Path(o["file"])
        content_hash = hashlib.md5(path.read_bytes()).hexdigest()  # noqa: S324 — tylko dedup
        dup = Document.objects.filter(content_hash=content_hash).first()
        if dup and not o["allow_duplicate"]:
            self.stdout.write(
                self.style.WARNING(
                    f"ten sam plik jest już zaindeksowany jako #{dup.id} „{dup.title[:60]}” — pomijam"
                )
            )
            return
        drafts = build_chunks(path)
        with transaction.atomic():
            doc = Document.objects.create(
                title=o["title"],
                doc_type=o["type"],
                journal=o["journal"],
                year=o["year"],
                volume=o["volume"],
                pages=o["pages"],
                doi=o["doi"],
                url=o["url"],
                license=o["license_text"],
                access=o["access"],
                source_path=str(path),
                content_hash=content_hash,
                chunk_count=len(drafts),
                language=o["language"],
                register=o["register"]
                or DEFAULT_REGISTER.get(o["type"], Register.SCIENTIFIC),
            )
            for name in o["author"]:
                author, _ = Author.objects.get_or_create(
                    name=name, defaults={"slug": slugify(name)}
                )
                doc.authors.add(author)
            Chunk.objects.bulk_create(
                [
                    Chunk(
                        document=doc,
                        order=d.order,
                        section=d.section[:300],
                        text=d.text,
                        page_start=d.page_start,
                        page_end=d.page_end,
                        sigla=d.sigla,
                    )
                    for d in drafts
                ]
            )
        from library.search import reset_language_cache

        reset_language_cache()
        n_sigla = sum(len(d.sigla) for d in drafts)
        self.stdout.write(
            f"Dokument #{doc.id} — {doc.title}: {len(drafts)} chunków, {n_sigla} sigli"
        )
        if settings.SEARCH_BACKEND == "opensearch" and not o["no_index"]:
            from library.search import index_document

            n = index_document(doc, with_vectors=not o["no_vectors"])
            self.stdout.write(self.style.SUCCESS(f"Zaindeksowano {n} chunków"))
