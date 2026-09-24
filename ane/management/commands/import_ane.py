"""Import tekstu z eBL do bazy ANE.

    python manage.py import_ane L/1/4 --name-pl "Gilgamesz" --stages "Standard Babylonian"
    python manage.py import_ane L/1/2 --name-pl "Enuma Elisz"
    python manage.py import_ane L/1/4 --from-dir data/ane/L-1-4     # z zapisanych JSON-ów (text.json, chapters/*.json)

Bez --stages importuje wszystkie rozdziały tekstu. Pobrane JSON-y są zapisywane w data/ane/<id>/
(cache; --from-dir odtwarza bez sieci). Po imporcie: index_ane --recreate.
"""

import json
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from ane import importers


class Command(BaseCommand):
    help = "Import kompozycji z eBL (genre/category/index) do bazy ANE"

    def add_arguments(self, parser):
        parser.add_argument("text_id", help="np. L/1/4")
        parser.add_argument("--name-pl", default="")
        parser.add_argument(
            "--stages", nargs="*", help="tylko te wersje, np. 'Standard Babylonian'"
        )
        parser.add_argument(
            "--from-dir", help="katalog z text.json i chapters/*.json (bez sieci)"
        )

    def handle(self, *args, **o):
        genre, category, index = o["text_id"].split("/")
        cache = (
            Path(o["from_dir"])
            if o["from_dir"]
            else settings.DATA_DIR / "ane" / o["text_id"].replace("/", "-")
        )
        cache.mkdir(parents=True, exist_ok=True)
        meta_path = cache / "text.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = importers.fetch_text(genre, int(category), int(index))
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        chapters = []
        for ch in meta.get("chapters", []):
            stage, name = ch.get("stage", ""), str(ch.get("name", ""))
            if o["stages"] and stage not in o["stages"]:
                continue
            fname = (
                cache
                / "chapters"
                / (re.sub(r"[^A-Za-z0-9]+", "_", f"{stage}_{name}") + ".json")
            )
            if fname.exists():
                data = json.loads(fname.read_text(encoding="utf-8"))
            else:
                if o["from_dir"]:
                    self.stderr.write(f"brak {fname}")
                    continue
                data = importers.fetch_chapter(
                    genre, int(category), int(index), stage, name
                )
                fname.parent.mkdir(parents=True, exist_ok=True)
                fname.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            draft = importers.parse_chapter(data)
            if not draft.title:
                draft.title = " ".join(
                    p.get("text", "")
                    for p in ch.get("title", [])
                    if isinstance(p, dict)
                )
            chapters.append(draft)
            self.stdout.write(f"  {stage} {name}: {len(draft.lines)} linii")
        text = importers.save_text(meta, chapters, o["text_id"], o["name_pl"])
        n_pass = sum(c.passages.count() for c in text.chapters.all())
        self.stdout.write(
            self.style.SUCCESS(
                f"{text}: {text.chapters.count()} rozdziałów, {n_pass} pasaży — teraz: index_ane --recreate"
            )
        )
