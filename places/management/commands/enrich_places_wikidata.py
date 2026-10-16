"""Polskie warianty nazw, krótkie opisy i linki do polskiej Wikipedii z Wikidata (CC0).

python manage.py enrich_places_wikidata [--sleep 1]
Dotyczy miejsc z identyfikatorem Wikidata (OpenBible linked_data). Etykieta i aliasy PL trafiają
do `aliases_pl` (wyszukiwanie: „sadzawka Siloam” obok „Siloe”), opis do `description_pl`,
artykuł plwiki do `wiki_pl`. Nazwy wyświetlanej (`name_pl`) nie zmienia — ta idzie z
translate_places w pisowni Biblii Tysiąclecia.
"""

import time

from django.core.management.base import BaseCommand

from places.models import Place
from places.service import merge_names, reset_caches
from places.wikidata import BATCH, fetch, parse_entity


class Command(BaseCommand):
    help = "Polskie warianty nazw, opisy i linki plwiki z Wikidata"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sleep", type=float, default=1.0, help="przerwa między żądaniami (s)"
        )

    def handle(self, *args, **o):
        by_q: dict[str, list[Place]] = {}
        for p in Place.objects.exclude(wikidata=""):
            by_q.setdefault(p.wikidata.strip(), []).append(p)
        ids = sorted(by_q)
        updated = 0
        for start in range(0, len(ids), BATCH):
            chunk = ids[start : start + BATCH]
            try:
                entities = fetch(chunk)
            except Exception as exc:  # noqa: BLE001 — sieć; następna partia
                self.stderr.write(f"partia {start // BATCH + 1}: {exc}")
                continue
            for q, entity in entities.items():
                data = parse_entity(entity)
                for p in by_q.get(q, []):
                    p.aliases_pl = merge_names(
                        p.aliases_pl,
                        [data["label"], *data["aliases"]],
                        exclude=p.name_pl,
                    )
                    p.description_pl = data["description"][:300]
                    p.wiki_pl = data["wiki"][:300]
                    p.save(update_fields=["aliases_pl", "description_pl", "wiki_pl"])
                    updated += 1
            self.stdout.write(f"  {min(start + BATCH, len(ids))}/{len(ids)}")
            time.sleep(o["sleep"])
        reset_caches()
        self.stdout.write(
            self.style.SUCCESS(f"Wikidata: zaktualizowano {updated} miejsc")
        )
