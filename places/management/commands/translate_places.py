"""Polskie nazwy miejsc biblijnych (model czatu, partie po 60): Bethel -> Betel, Haran -> Charan.

python manage.py translate_places [--force] [--limit N]
Konwencja: pisownia Biblii Tysiąclecia (najczęstsza w polskiej biblistyce); gdy model nie jest
pewny, zostawia nazwę angielską — lepsza EN niż zmyślona PL. Odpowiedź modelu to JSON
[{"i": 1, "pl": "Betel"}]; weryfikowana długością i brakiem znaków spoza alfabetu.
"""

import re

from django.core.management.base import BaseCommand

from library import llm
from library.curate import chat_json
from places.models import Place
from places.service import reset_name_cache

PROMPT = (
    "Podaj polskie nazwy miejsc biblijnych w pisowni Biblii Tysiąclecia. Dla każdej pozycji zwróć "
    'obiekt JSON {{"i": numer, "pl": "nazwa"}}. Jeśli miejsce nie ma utrwalonej polskiej nazwy '
    "albo nie jesteś pewien, przepisz nazwę angielską bez zmian. Zwróć wyłącznie listę JSON.\n\n"
    "Miejsca (nazwa EN; typ; inne pisownie):\n{items}"
)
_OK_RE = re.compile(r"^[\w\s'’\-–.()]{2,60}$", re.UNICODE)


class Command(BaseCommand):
    help = "Tłumaczy nazwy miejsc na polski (BT) modelem czatu"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true", help="także miejsca z nazwą PL"
        )
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch", type=int, default=60)

    def handle(self, *args, **o):
        if not llm.enabled():
            self.stderr.write("LLM_BACKEND=echo — brak modelu")
            return
        qs = Place.objects.order_by("-verse_count")
        if not o["force"]:
            qs = qs.filter(name_pl="")
        places = list(qs[: o["limit"]] if o["limit"] else qs)
        done = 0
        for start in range(0, len(places), o["batch"]):
            batch = places[start : start + o["batch"]]
            items = "\n".join(
                f"{i}. {p.name}; {', '.join(p.types) or '?'}; {', '.join(p.aliases[:4])}"
                for i, p in enumerate(batch, start=1)
            )
            try:
                rows = chat_json(PROMPT.format(items=items), num_predict=3000)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"partia {start // o['batch'] + 1}: {exc}")
                continue
            by_i = {
                int(r.get("i", 0)): str(r.get("pl", "")).strip()
                for r in rows
                if isinstance(r, dict)
            }
            for i, p in enumerate(batch, start=1):
                pl = by_i.get(i, "")
                if pl and _OK_RE.match(pl) and pl != p.name:
                    p.name_pl = pl
                    p.save(update_fields=["name_pl"])
                    done += 1
            self.stdout.write(
                f"  {min(start + o['batch'], len(places))}/{len(places)} · PL: {done}"
            )
        reset_name_cache()
        self.stdout.write(self.style.SUCCESS(f"nazwy polskie: {done} z {len(places)}"))
