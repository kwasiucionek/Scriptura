"""Zbierz manifest prac ze źródła (bez pobierania plików).

    python manage.py harvest dspace --browse-author "Majewski, Marcin" --out data/manifests/majewski-open.jsonl
    python manage.py harvest dspace --query "Rosik Mariusz" --base-url https://open.icm.edu.pl ...
    python manage.py harvest openalex --author "Marcin Majewski"            # lista kandydatów z afiliacją
    python manage.py harvest openalex --author-id A5012345678 --only-oa --out data/manifests/majewski-oa.jsonl
    python manage.py harvest scholar --scholar-id r0NgMwMAAAAJ --out data/manifests/majewski-scholar.jsonl
    python manage.py harvest bibtex --file ~/Pobrane/citations.bib --out data/manifests/majewski-scholar.jsonl

Manifest przejrzyj ręcznie (kolumna access: open/licensed/private/skip), potem `ingest_manifest`.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from library.harvest import bibtex, blog, dspace, manifest, openalex, scholar, youtube


class Command(BaseCommand):
    help = (
        "Harvest metadanych do manifestu JSONL (dspace | openalex | scholar | bibtex)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "source",
            choices=["dspace", "openalex", "scholar", "bibtex", "blog", "youtube"],
        )
        parser.add_argument(
            "--query",
            help="dspace: zapytanie discover (pełnotekstowe), np. Majewski Marcin",
        )
        parser.add_argument(
            "--browse-author",
            help='dspace: kanoniczna wartość autora z indeksu przeglądania, np. "Majewski, Marcin" (dokładnie)',
        )
        parser.add_argument(
            "--base-url",
            default="https://open.icm.edu.pl",
            help="dspace: adres instancji",
        )
        parser.add_argument(
            "--author", help="openalex: nazwisko do wyszukania kandydatów"
        )
        parser.add_argument(
            "--author-id", help="openalex: id autora (A…) do pobrania prac"
        )
        parser.add_argument(
            "--only-oa",
            action="store_true",
            help="openalex: tylko prace w otwartym dostępie",
        )
        parser.add_argument(
            "--scholar-id",
            help="scholar: id profilu z URL citations?user=… (wymaga SERPAPI_KEY)",
        )
        parser.add_argument(
            "--file", help="bibtex: plik .bib (eksport z profilu Google Scholar)"
        )
        parser.add_argument(
            "--site", help="blog: adres WordPressa, np. https://majewskimarcin.pl"
        )
        parser.add_argument("--video", nargs="*", help="youtube: adresy lub id filmów")
        parser.add_argument(
            "--since",
            help="blog: tylko wpisy zmodyfikowane od daty YYYY-MM-DD (przyrost)",
        )
        parser.add_argument(
            "--out", help="ścieżka JSONL (domyślnie MANIFESTS_DIR/<source>.jsonl)"
        )

    def handle(self, *args, **o):
        mailto = settings.HARVEST_MAILTO
        source = o["source"]

        if source == "dspace":
            base = o["base_url"].rstrip("/")
            if o["browse_author"]:
                entries = dspace.harvest_author(base, o["browse_author"], mailto)
            elif o["query"]:
                entries = dspace.harvest(base, o["query"], mailto)
            else:
                raise CommandError("dspace wymaga --browse-author lub --query")

        elif source == "scholar":
            if not o["scholar_id"]:
                raise CommandError("scholar wymaga --scholar-id")
            if not settings.SERPAPI_KEY:
                raise CommandError(
                    "brak SERPAPI_KEY w .env (alternatywa: eksport BibTeX -> harvest bibtex)"
                )
            entries = scholar.harvest_profile(o["scholar_id"], settings.SERPAPI_KEY)

        elif source == "blog":
            if not o["site"] or not o["author"]:
                raise CommandError(
                    "blog wymaga --site i --author (nazwisko autora bloga)"
                )
            out_dir = settings.DATA_DIR / "library" / "blog"
            entries = [
                blog.post_to_entry(p, o["author"], out_dir, o["site"])
                for p in blog.fetch_posts(o["site"], o["since"])
            ]

        elif source == "youtube":
            if not o["video"] or not o["author"]:
                raise CommandError("youtube wymaga --video (adresy/id) i --author")
            out_dir = settings.DATA_DIR / "library"
            entries = []
            for v in o["video"]:
                info = youtube.fetch(v)
                if not info.captions:
                    self.stderr.write(f"  {info.title[:60]}: brak napisów — pomijam")
                    continue
                entries.append(youtube.to_entry(info, o["author"], out_dir))
                self.stdout.write(
                    f"  {info.title[:60]}: {len(info.captions)} napisów, "
                    f"{len(info.chapters)} rozdziałów ({info.caption_kind})"
                )

        elif source == "bibtex":
            if not o["file"]:
                raise CommandError("bibtex wymaga --file")
            entries = bibtex.parse(Path(o["file"]).read_text(encoding="utf-8"))

        else:  # openalex
            if o["author_id"]:
                entries = openalex.works_for_author(
                    o["author_id"], mailto, o["only_oa"]
                )
            elif o["author"]:
                for a in openalex.find_authors(o["author"], mailto):
                    self.stdout.write(
                        f"{a['id']:32} {a['display_name']:30} prac={a['works_count']:4}  "
                        f"{', '.join(a['institutions'])}"
                    )
                self.stdout.write("Wybierz --author-id i uruchom ponownie.")
                return
            else:
                raise CommandError("openalex wymaga --author lub --author-id")

        out = Path(o["out"]) if o["out"] else settings.MANIFESTS_DIR / f"{source}.jsonl"
        n = manifest.write(out, entries)
        by_access: dict[str, int] = {}
        for e in entries:
            by_access[e.access] = by_access.get(e.access, 0) + 1
        self.stdout.write(self.style.SUCCESS(f"{n} pozycji -> {out}  {by_access}"))
        for e in entries[:20]:
            self.stdout.write(
                f"  [{e.access:8}] {e.year or '----'} {e.title[:70]:70} {e.license or '—'}"
            )
