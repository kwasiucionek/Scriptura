"""Import geografii biblijnej (OpenBible.info Bible Geocoding Data, CC BY 4.0).

python manage.py fetch_sources places      # ancient.jsonl -> data/places/ancient.jsonl
python manage.py import_places [--file …]  # Place / PlaceLocation / PlaceRef (zastępuje)
Polskie nazwy: python manage.py translate_places (model czatu; bez tego UI pokazuje nazwy EN).
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from places.importers import openbible
from places.models import Place, PlaceLocation, PlaceRef


class Command(BaseCommand):
    help = "Import miejsc biblijnych z OpenBible.info (CC BY)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", default=str(settings.DATA_DIR / "places" / "ancient.jsonl")
        )
        parser.add_argument(
            "--keep-pl",
            action="store_true",
            default=True,
            help="zachowaj istniejące polskie nazwy (domyślnie tak)",
        )

    def handle(self, *args, **o):
        path = Path(o["file"])
        if not path.exists():
            self.stderr.write(
                f"brak {path} — uruchom: python manage.py fetch_sources places"
            )
            return
        rows = list(openbible.parse_file(path))
        names_pl = dict(
            Place.objects.exclude(name_pl="").values_list("openbible_id", "name_pl")
        )
        with transaction.atomic():
            Place.objects.all().delete()
            places = Place.objects.bulk_create(
                [
                    Place(
                        openbible_id=r.openbible_id,
                        slug=r.slug,
                        name=r.name,
                        name_pl=names_pl.get(r.openbible_id, "")
                        if o["keep_pl"]
                        else "",
                        aliases=r.aliases,
                        kind=r.kind,
                        types=r.types,
                        wikidata=r.wikidata,
                        verse_count=len(r.ordinals),
                        url=r.url,
                    )
                    for r in rows
                ],
                batch_size=500,
            )
            by_id = (
                {p.openbible_id: p for p in Place.objects.all()}
                if any(p.pk is None for p in places)
                else {p.openbible_id: p for p in places}
            )
            locs, refs = [], []
            for r in rows:
                p = by_id[r.openbible_id]
                for i, loc in enumerate(r.locations):
                    locs.append(
                        PlaceLocation(
                            place=p,
                            order=i,
                            modern_id=loc.modern_id,
                            name=loc.name[:160],
                            lon=loc.lon,
                            lat=loc.lat,
                            geometry=loc.geometry,
                            radius_m=loc.radius_m,
                            type=loc.type[:40],
                            score=loc.score,
                            confidence=loc.confidence,
                        )  # fmt: skip
                    )
                refs.extend(PlaceRef(place=p, ordinal=o_) for o_ in r.ordinals)
            PlaceLocation.objects.bulk_create(locs, batch_size=2000)
            PlaceRef.objects.bulk_create(refs, batch_size=5000)
        no_loc = sum(1 for r in rows if not r.locations)
        self.stdout.write(
            self.style.SUCCESS(
                f"Place: {len(rows)} · PlaceLocation: {len(locs)} · PlaceRef: {len(refs)}"
                f" · bez lokalizacji: {no_loc} · polskie nazwy zachowane: {sum(1 for r in rows if r.openbible_id in names_pl)}"
            )
        )
