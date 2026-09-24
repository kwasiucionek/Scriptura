"""Aktualizacja korpusu dla listy autorów: harvest -> kurator -> ingestia -> przekład -> reindeks -> raport.

    python manage.py update_corpus                      # wszyscy autorzy z data/authors.jsonl
    python manage.py update_corpus --authors rosik --dry-run
systemd timer / cron (np. co tydzień w nocy):
    cd /path/app && .venv/bin/python manage.py update_corpus >> data/manifests/update.log 2>&1

Plik data/authors.jsonl — jedna linia na autora:
  {"slug": "rosik", "name": "Mariusz Rosik", "openalex": ["A5073391228", "A5090059155"],
   "dspace": "Rosik, Mariusz", "scholar": "", "default_register": "scientific",
   "blog": "https://majewskimarcin.pl", "blog_access": "licensed"}   # blog opcjonalny; blog_access=open po zgodzie autora

Zasady: pobierane i indeksowane są tylko pozycje `open` (licencja CC); `licensed` czekają na
zgody, `review` na decyzję człowieka; obie grupy trafiają do raportu. Pozycje już obecne w
manifeście autora (po source_id) nie są ponownie oceniane.
"""

import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from library import curate as cur
from library.harvest import blog, dspace, manifest, openalex, scholar
from library.models import Document


class Command(BaseCommand):
    help = "Przyrostowa aktualizacja korpusu dla autorów z data/authors.jsonl"

    def add_arguments(self, parser):
        parser.add_argument("--authors", nargs="*", help="slugi (domyślnie wszyscy)")
        parser.add_argument(
            "--config", default=str(settings.DATA_DIR / "authors.jsonl")
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="tylko harvest + kurator + raport"
        )
        parser.add_argument("--no-llm", action="store_true")

    def handle(self, *args, **o):
        cfg = [
            json.loads(line)
            for line in Path(o["config"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if o["authors"]:
            cfg = [a for a in cfg if a["slug"] in o["authors"]]
        mailto = settings.HARVEST_MAILTO
        report_lines = [f"# Aktualizacja korpusu — {datetime.now():%Y-%m-%d %H:%M}\n"]
        new_doc_ids: list[int] = []

        for a in cfg:
            slug, name = a["slug"], a["name"]
            path = settings.MANIFESTS_DIR / f"{slug}.jsonl"
            existing = manifest.read(path) if path.exists() else []
            known = {e.source_id for e in existing}

            fresh: list = []
            for aid in a.get("openalex", []):
                fresh += openalex.works_for_author(aid, mailto, only_oa=False)
            if a.get("dspace"):
                fresh += dspace.harvest_author(
                    a.get("dspace_url", "https://open.icm.edu.pl"), a["dspace"], mailto
                )
            if a.get("scholar") and settings.SERPAPI_KEY:
                fresh += scholar.harvest_profile(a["scholar"], settings.SERPAPI_KEY)
            if a.get(
                "blog"
            ):  # WordPress autora; wpisy trafiają jako licensed (do zgody) lub open wg blog_access
                out_dir = settings.DATA_DIR / "library" / "blog"
                for post in blog.fetch_posts(a["blog"]):
                    e = blog.post_to_entry(post, name, out_dir, a["blog"])
                    if a.get("blog_access") in ("open", "licensed"):
                        e.access = a["blog_access"]
                    fresh.append(e)
            new_entries = [e for e in fresh if e.source_id not in known]
            for e in new_entries:
                if not getattr(e, "register", ""):
                    e.register = a.get("default_register", "")

            report_lines.append(
                f"\n## {name}: {len(new_entries)} nowych pozycji (znanych {len(existing)})"
            )
            if not new_entries:
                continue
            rep = cur.curate(existing + new_entries, name, use_llm=not o["no_llm"])
            cur.apply(existing + new_entries, rep)
            entries = existing + new_entries
            manifest.write(path, entries)

            for e in new_entries:
                mark = {
                    "open": "✔ do ingestii",
                    "skip": "– pominięte",
                    "review": "? do decyzji",
                    "licensed": "! wymaga zgody",
                }.get(e.access, e.access)
                report_lines.append(
                    f"- {mark}: {e.year or '----'} {e.title[:80]} — {e.note[:90]}"
                )

            if o["dry_run"]:
                continue
            before = set(Document.objects.values_list("id", flat=True))
            call_command("ingest_manifest", str(path), "--access", "open", "--no-index")
            new_doc_ids += sorted(
                set(Document.objects.values_list("id", flat=True)) - before
            )

        if not o["dry_run"] and new_doc_ids:
            call_command(
                "audit_corpus",
                "--documents",
                *map(str, new_doc_ids),
                "--apply",
                "--delete",
            )
            new_doc_ids = [
                i for i in new_doc_ids if Document.objects.filter(id=i).exists()
            ]
            call_command("translate_chunks")
            call_command("hash_documents")
            if settings.SEARCH_BACKEND == "opensearch":
                call_command("reindex_chunks", "--documents", *map(str, new_doc_ids))
            report_lines.append(
                f"\nZaindeksowano {len(new_doc_ids)} nowych dokumentów: {new_doc_ids}"
            )
            report_lines.append(
                "Do zestawu ewaluacyjnego: `eval_bootstrap --documents "
                + " ".join(map(str, new_doc_ids))
                + "`"
            )

        out = settings.MANIFESTS_DIR / f"report-{datetime.now():%Y-%m-%d}.md"
        out.write_text("\n".join(report_lines), encoding="utf-8")
        self.stdout.write("\n".join(report_lines))
        self.stdout.write(self.style.SUCCESS(f"raport: {out}"))
