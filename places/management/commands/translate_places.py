"""Polskie nazwy miejsc biblijnych i polskie opisy lokalizacji (model czatu, partiami).

python manage.py translate_places [--rename] [--only-missing] [--limit N] [--batch 40]
python manage.py translate_places --locations [--force] [--limit N]

Nazwy: pisownia Biblii Tysiąclecia (najczęstsza w polskiej biblistyce) w `name_pl` oraz polskie
warianty z innych przekładów i literatury w `aliases_pl` (Siloam -> Siloe; warianty: Siloam,
sadzawka Siloe) — wyszukiwanie na mapie i w pytaniach znajduje miejsce po każdej z nich.
Gdy model nie jest pewny, zostawia nazwę angielską — lepsza EN niż zmyślona PL. Istniejącej
nazwy `name_pl` nie zmienia bez --rename; warianty dopisuje zawsze. Podpowiedzią dla modelu
jest polska etykieta z Wikidata (enrich_places_wikidata), gdy jest.

Lokalizacje (--locations): opisy z atlasu OpenBible, które nie są samą nazwą własną („within
10 km of Jaba”, „Pool of Siloam”, „in Nineveh”), dostają polski opis w `PlaceLocation.name_pl`;
współczesne nazwy arabskie i hebrajskie (Khirbet …, Tell …) zostają bez zmian.

Odpowiedź modelu to lista JSON; nazwy weryfikowane regexem i długością.
"""

import re

from django.core.management.base import BaseCommand

from library import llm
from library.curate import chat_json
from places.models import Place, PlaceLocation
from places.service import merge_names, reset_caches

NAMES_PROMPT = (
    "Jesteś biblistą. Podaj polskie nazwy miejsc biblijnych. Dla każdej pozycji zwróć obiekt "
    'JSON {{"i": numer, "pl": "nazwa", "warianty": ["…"]}}, gdzie „pl” to nazwa w pisowni Biblii '
    "Tysiąclecia, a „warianty” to inne polskie formy tej nazwy: z innych przekładów (Biblia "
    "Warszawska, Biblia Gdańska, Biblia Poznańska, przekłady ekumeniczne) i z polskiej "
    "literatury biblijnej, także z określeniem rodzaju miejsca, np. dla Siloam: "
    '"pl": "Siloe", "warianty": ["Siloam", "sadzawka Siloe", "Szelach"]. Najwyżej 5 wariantów. '
    "Jeśli miejsce nie ma utrwalonej polskiej nazwy albo nie jesteś pewien, przepisz nazwę "
    "angielską bez zmian i podaj pustą listę wariantów — lepsza nazwa angielska niż zmyślona "
    "polska. Zwróć wyłącznie listę JSON.\n\n"
    "Miejsca (nazwa EN; typ; inne pisownie EN; polska etykieta z Wikidata, jeśli jest):\n{items}"
)
LOCATIONS_PROMPT = (
    "Przetłumacz na polski opisy położenia miejsc biblijnych z atlasu OpenBible. Współczesne "
    "nazwy arabskie i hebrajskie (Khirbet …, Tell …, Tel …, Wadi …, Jebel …, Ain …, Bir …, "
    "Nahal …) przepisz bez zmian i bez odmiany. Nazwy biblijne i znane miejsca podaj po polsku w "
    "pisowni Biblii Tysiąclecia (Gibeah -> Gibea, Nineveh -> Niniwa, Pool of Siloam -> sadzawka "
    "Siloe, Hinnom Valley -> Dolina Hinnom, Church of the Holy Sepulchre -> Bazylika Grobu "
    "Świętego). Zachowaj liczby i jednostki („within 10 km of …” -> „do 10 km od …”, „about 2 km "
    "around …” -> „ok. 2 km wokół …”, „near …” -> „w pobliżu …”). Opis ma być krótki, bez "
    'komentarzy. Dla każdej pozycji zwróć obiekt JSON {{"i": numer, "pl": "opis"}}. Zwróć '
    "wyłącznie listę JSON.\n\nOpisy:\n{items}"
)
_NAME_RE = re.compile(r"^[\w\s'’\-–.()]{2,80}$", re.UNICODE)
# opis do przetłumaczenia: zawiera angielskie słowa, nie tylko współczesną nazwę własną
_ENGLISH_RE = re.compile(
    r"\b(?:of|the|near|within|about|around|in|between|along|and|or|at|on|region|area|valley|"
    r"pool|church|river|mount|mountain|mountains|sea|lake|gate|spring|well|desert|plain|mouth|"
    r"hill|island|city|tomb|temple|road|east|west|north|south|upper|lower|old|new|probably|"
    r"possibly|unknown|somewhere|site|ruins|village|garden|wall|tower|bay|gulf|coast|not)\b",
    re.IGNORECASE,
)


def needs_translation(text: str) -> bool:
    return bool(_ENGLISH_RE.search(text or ""))


def valid_name(value: str) -> bool:
    return bool(value) and bool(_NAME_RE.match(value))


def valid_description(value: str) -> bool:
    return 2 <= len(value) <= 200 and "\n" not in value


class Command(BaseCommand):
    help = "Polskie nazwy miejsc (BT) z wariantami albo polskie opisy lokalizacji — modelem czatu"

    def add_arguments(self, parser):
        parser.add_argument(
            "--locations", action="store_true", help="opisy lokalizacji zamiast nazw"
        )
        parser.add_argument(
            "--rename", action="store_true", help="nadpisz istniejące nazwy PL"
        )
        parser.add_argument(
            "--only-missing", action="store_true", help="tylko miejsca bez nazwy PL"
        )
        parser.add_argument(
            "--force", action="store_true", help="lokalizacje: także już przetłumaczone"
        )
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--batch", type=int, default=40)

    def handle(self, *args, **o):
        if not llm.enabled():
            self.stderr.write("LLM_BACKEND=echo — brak modelu")
            return
        if o["locations"]:
            self.translate_locations(o)
        else:
            self.translate_names(o)
        reset_caches()

    # --- nazwy -------------------------------------------------------------------------------

    def translate_names(self, o):
        qs = Place.objects.order_by("-verse_count")
        if o["only_missing"]:
            qs = qs.filter(name_pl="")
        places = list(qs[: o["limit"]] if o["limit"] else qs)
        named = variants = 0
        for start in range(0, len(places), o["batch"]):
            batch = places[start : start + o["batch"]]
            items = "\n".join(
                f"{i}. {p.name}; {', '.join(p.types) or '?'}; {', '.join(p.aliases[:4])}; "
                f"{(p.aliases_pl or [''])[0] if p.wikidata else ''}"
                for i, p in enumerate(batch, start=1)
            )
            try:
                rows = chat_json(NAMES_PROMPT.format(items=items), num_predict=6000)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"partia {start // o['batch'] + 1}: {exc}")
                continue
            by_i = {int(r.get("i", 0)): r for r in rows if isinstance(r, dict)}
            for i, p in enumerate(batch, start=1):
                row = by_i.get(i) or {}
                pl = str(row.get("pl", "")).strip()
                found = [
                    str(v).strip()
                    for v in row.get("warianty") or []
                    if isinstance(v, str)
                ]
                fields = []
                if valid_name(pl) and pl != p.name and (o["rename"] or not p.name_pl):
                    p.name_pl = pl
                    fields.append("name_pl")
                    named += 1
                merged = merge_names(
                    p.aliases_pl,
                    [v for v in found if valid_name(v)]
                    + ([pl] if valid_name(pl) and pl != p.name_pl else []),
                    exclude=p.name_pl or p.name,
                )
                if merged != p.aliases_pl:
                    variants += len(merged) - len(p.aliases_pl or [])
                    p.aliases_pl = merged
                    fields.append("aliases_pl")
                if fields:
                    p.save(update_fields=fields)
            self.stdout.write(
                f"  {min(start + o['batch'], len(places))}/{len(places)} · nazwy PL: {named}"
                f" · nowe warianty: {variants}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"nazwy PL: {named}, warianty: {variants} ({len(places)} miejsc)"
            )
        )

    # --- opisy lokalizacji ---------------------------------------------------------------------

    def translate_locations(self, o):
        qs = PlaceLocation.objects.order_by("id")
        if not o["force"]:
            qs = qs.filter(name_pl="")
        by_text: dict[str, list[PlaceLocation]] = {}
        for loc in qs:
            if needs_translation(loc.name):
                by_text.setdefault(loc.name, []).append(loc)
        texts = sorted(by_text)
        if o["limit"]:
            texts = texts[: o["limit"]]
        done = 0
        for start in range(0, len(texts), o["batch"]):
            batch = texts[start : start + o["batch"]]
            items = "\n".join(f"{i}. {t}" for i, t in enumerate(batch, start=1))
            try:
                rows = chat_json(LOCATIONS_PROMPT.format(items=items), num_predict=4000)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"partia {start // o['batch'] + 1}: {exc}")
                continue
            by_i = {
                int(r.get("i", 0)): str(r.get("pl", "")).strip()
                for r in rows
                if isinstance(r, dict)
            }
            for i, text in enumerate(batch, start=1):
                pl = by_i.get(i, "")
                if not valid_description(pl) or pl == text:
                    continue
                ids = [loc.id for loc in by_text[text]]
                done += PlaceLocation.objects.filter(id__in=ids).update(name_pl=pl)
            self.stdout.write(
                f"  {min(start + o['batch'], len(texts))}/{len(texts)} · opisy PL: {done}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"opisy lokalizacji PL: {done} ({len(texts)} różnych opisów)"
            )
        )
