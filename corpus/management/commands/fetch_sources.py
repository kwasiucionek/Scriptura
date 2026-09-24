"""Pobiera otwarte korpusy do DATA_DIR: OSHB, MorphGNT, Biblia Gdańska JSON, LXX, leksykony STEPBible.

python manage.py fetch_sources            # wszystko
python manage.py fetch_sources oshb       # tylko OSHB
python manage.py fetch_sources oshb --books Gen Isa Ps
python manage.py fetch_sources lxx        # Septuaginta (Rahlfs 1935, E. Wong) — 3 pliki, ~60 MB
python manage.py fetch_sources step       # TBESH + TBESG (Tyndale House, CC BY 4.0) — ~15 MB
"""

import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from corpus.books import BOOKS

OSHB_RAW = (
    "https://raw.githubusercontent.com/openscriptures/morphhb/master/wlc/{name}.xml"
)
MORPHGNT_RAW = "https://raw.githubusercontent.com/morphgnt/sblgnt/master/{name}"
MORPHGNT_FILES = [
    "61-Mt", "62-Mk", "63-Lk", "64-Jn", "65-Ac", "66-Ro", "67-1Co", "68-2Co", "69-Ga",
    "70-Eph", "71-Php", "72-Col", "73-1Th", "74-2Th", "75-1Ti", "76-2Ti", "77-Tit",
    "78-Phm", "79-Heb", "80-Jas", "81-1Pe", "82-2Pe", "83-1Jn", "84-2Jn", "85-3Jn",
    "86-Jud", "87-Re",
]  # fmt: skip
BG_RAW = (
    "https://raw.githubusercontent.com/bible-api-io/bible-api-version-bg/master/bg.json"
)
LXX_RAW = "https://raw.githubusercontent.com/eliranwong/LXX-Rahlfs-1935/master/"
LXX_FILES = {
    "LXX_final_main.csv": "11_end-users_files/MyBible/Bibles/LXX_final_main.csv",
    "Lex_LXXno.csv": "02_lexemes/Lex_LXXno.csv",
    "OSSP_lexemes.csv": "02_lexemes/OSSP_lexemes.csv",
}
STEP_RAW = "https://raw.githubusercontent.com/STEPBible/STEPBible-Data/master/Lexicons/"
STEP_FILES = {
    "TBESH.txt": "TBESH%20-%20Translators%20Brief%20lexicon%20of%20Extended%20Strongs%20for%20Hebrew%20-%20STEPBible.org%20CC%20BY.txt",
    "TBESG.txt": "TBESG%20-%20Translators%20Brief%20lexicon%20of%20Extended%20Strongs%20for%20Greek%20-%20STEPBible.org%20CC%20BY.txt",
}
XREF_ZIP = "https://a.openbible.info/data/cross-references.zip"
ALL_SOURCES = ["oshb", "morphgnt", "bg", "lxx", "step", "xref"]


def _download(url: str, dest: Path, force: bool) -> bool:
    if dest.exists() and not force:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        dest.write_bytes(resp.read())
    return True


class Command(BaseCommand):
    help = (
        "Pobierz otwarte korpusy (OSHB, MorphGNT, BG 1632, LXX, STEPBible) do DATA_DIR"
    )

    def add_arguments(self, parser):
        parser.add_argument("sources", nargs="*", choices=ALL_SOURCES, default=[])
        parser.add_argument(
            "--books", nargs="*", help="OSIS id ksiąg (domyślnie wszystkie)"
        )
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **opts):
        sources = set(opts["sources"]) or set(ALL_SOURCES)
        only = set(opts["books"] or [])
        data: Path = settings.DATA_DIR
        fetched = 0

        if "oshb" in sources:
            for spec in BOOKS:
                if spec.oshb_file and (not only or spec.osis in only):
                    dest = data / "oshb" / "wlc" / f"{spec.oshb_file}.xml"
                    fetched += _download(
                        OSHB_RAW.format(name=spec.oshb_file), dest, opts["force"]
                    )
                    self.stdout.write(f"oshb {spec.oshb_file}")
        if "morphgnt" in sources:
            for name in MORPHGNT_FILES:
                code = f"{int(name[:2]) - 60:02d}"
                spec = next(b for b in BOOKS if b.morphgnt_code == code)
                if only and spec.osis not in only:
                    continue
                fname = f"{name}-morphgnt.txt"
                fetched += _download(
                    MORPHGNT_RAW.format(name=fname),
                    data / "morphgnt" / fname,
                    opts["force"],
                )
                self.stdout.write(f"morphgnt {fname}")
        if "bg" in sources:
            fetched += _download(
                BG_RAW, data / "translations" / "bg.json", opts["force"]
            )
            self.stdout.write("bg bg.json")
        if "lxx" in sources:
            for name, rel in LXX_FILES.items():
                fetched += _download(LXX_RAW + rel, data / "lxx" / name, opts["force"])
                self.stdout.write(f"lxx {name}")
        if "xref" in sources:
            import io
            import zipfile

            dest = data / "xref" / "cross_references.txt"
            if not dest.exists() or opts["force"]:
                with urllib.request.urlopen(XREF_ZIP, timeout=120) as resp:  # noqa: S310
                    zf = zipfile.ZipFile(io.BytesIO(resp.read()))
                name = next(n for n in zf.namelist() if n.endswith(".txt"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(name))
                fetched += 1
            self.stdout.write("xref cross_references.txt")
        if "step" in sources:
            for name, rel in STEP_FILES.items():
                fetched += _download(
                    STEP_RAW + rel, data / "step" / name, opts["force"]
                )
                self.stdout.write(f"step {name}")

        self.stdout.write(
            self.style.SUCCESS(f"Pobrano {fetched} nowych plików do {data}")
        )
