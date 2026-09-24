"""Pobierz pliki z manifestu i zaindeksuj (access != skip; domyślnie tylko open).

    python manage.py ingest_manifest data/manifests/majewski-open.jsonl
    python manage.py ingest_manifest data/manifests/x.jsonl --access open licensed --dry-run

Dublety: pomijane po source_id (ten sam rekord) i po znormalizowanym tytule
(ten sam tekst z innego źródła: OPEN vs OpenAlex vs Scholar). Kolejność ingestii
ma znaczenie — najpierw źródło z pełnymi plikami (repozytorium autora).

Pobieranie udaje przeglądarkę na tyle, na ile wymaga zapora repozytorium:
UA + Accept-Language + ciasteczko (HARVEST_*) i, gdy ustawiono HARVEST_REFERER_TEMPLATE,
Referer zbudowany z URL-a pliku (OPEN ICM: strona captcha z parametrem protected=).
Plik bez nagłówka %PDF- jest odrzucany.
"""

import re
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from library.harvest import manifest
from library.models import Document


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def _title_key(t: str) -> str:
    """Tytuł bez diakrytyków i interpunkcji, do pierwszej kropki (podtytuły bywają różne)."""
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 .]+", " ", t).split(".")[0].strip()


def _title_exists(
    title: str, authors: list[str] | None = None, threshold: float = 0.9
) -> bool:
    """Ten sam tytuł w innym zapisie -> nie dubluj dokumentu. Ale ten sam tytuł u RÓŻNYCH
    autorów to różne teksty (np. dwie recenzje tej samej książki) — dublet tylko przy wspólnym
    autorze albo gdy istniejący dokument nie ma autorów."""
    k = _title_key(title)
    if not k:
        return False
    wanted = {a.strip().lower() for a in (authors or []) if a.strip()}
    for d in Document.objects.prefetch_related("authors"):
        if SequenceMatcher(None, k, _title_key(d.title)).ratio() < threshold:
            continue
        existing = {a.name.strip().lower() for a in d.authors.all()}
        if not existing or not wanted or existing & wanted:
            return True
    return False


def _download_headers(url: str) -> dict[str, str]:
    """Nagłówki jak z przeglądarki; Referer wg szablonu, gdy zapora go wymaga."""
    headers = {
        "User-Agent": settings.HARVEST_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": settings.HARVEST_ACCEPT_LANGUAGE,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if settings.HARVEST_COOKIE:
        headers["Cookie"] = settings.HARVEST_COOKIE
    p = urllib.parse.urlparse(url)
    if settings.HARVEST_REFERER_TEMPLATE and p.netloc in settings.HARVEST_REFERER_HOSTS:
        origin = f"{p.scheme}://{p.netloc}"
        headers["Referer"] = settings.HARVEST_REFERER_TEMPLATE.format(
            origin=origin, path=p.path
        )
    return headers


def download_pdf(url: str, dest: Path) -> bool:
    """Pobierz plik; False, gdy odpowiedź nie jest PDF-em (strona zapory itp.)."""
    req = urllib.request.Request(url, headers=_download_headers(url))
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as f:  # noqa: S310
        f.write(resp.read())
    if dest.read_bytes()[:5] != b"%PDF-":
        dest.unlink()
        return False
    return True


class Command(BaseCommand):
    help = "Ingestia pozycji z manifestu JSONL"

    def add_arguments(self, parser):
        parser.add_argument("manifest")
        parser.add_argument(
            "--access", nargs="*", default=["open"], help="które poziomy ingestować"
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-index", action="store_true")

    def handle(self, *args, **o):
        entries = [
            e
            for e in manifest.read(Path(o["manifest"]))
            if e.access in o["access"] and (e.pdf_url or e.local_file)
        ]
        self.stdout.write(f"{len(entries)} pozycji do ingestii")
        files_dir: Path = settings.DATA_DIR / "library"
        files_dir.mkdir(parents=True, exist_ok=True)
        done = skipped = 0
        for e in entries:
            if Document.objects.filter(
                source_path__contains=e.source_id
            ).exists() or _title_exists(e.title):
                self.stdout.write(f"  pomijam (już jest): {e.title[:60]}")
                skipped += 1
                continue
            if e.local_file:  # tekst już pobrany przez harvest (np. wpis z bloga)
                dest = Path(e.local_file)
                self.stdout.write(f"  {e.title[:70]}  <- {dest.name}")
                if o["dry_run"]:
                    continue
                if not dest.exists():
                    self.stderr.write(f"  brak pliku {dest} — pomijam")
                    continue
            else:
                dest = files_dir / f"{e.source}-{_slug(e.source_id)}.pdf"
                self.stdout.write(f"  {e.title[:70]}  <- {e.pdf_url}")
            if o["dry_run"]:
                continue
            if not e.local_file and not dest.exists():
                try:
                    ok = download_pdf(e.pdf_url, dest)
                except (
                    Exception
                ) as exc:  # 403, timeout, DNS — pozycja pominięta, ingestia idzie dalej
                    self.stderr.write(
                        f"  błąd pobierania ({exc}): {e.pdf_url} — pomijam"
                    )
                    dest.unlink(missing_ok=True)
                    skipped += 1
                    continue
                if not ok:
                    self.stderr.write(
                        f"  to nie jest PDF (zapora/HTML): {e.pdf_url} — pomijam"
                    )
                    skipped += 1
                    continue
            args = [
                "--file", str(dest), "--title", e.title, "--type", e.doc_type, "--journal", e.journal,
                "--doi", e.doi, "--url", e.url, "--license", e.license, "--access", e.access,
                "--language", e.language,
            ]  # fmt: skip
            if e.year:
                args += ["--year", str(e.year)]
            if getattr(e, "register", ""):
                args += ["--register", e.register]
            for a in e.authors:
                args += ["--author", a]
            if o["no_index"]:
                args.append("--no-index")
            call_command("ingest_document", *args)
            done += 1
        self.stdout.write(
            self.style.SUCCESS(f"zaindeksowano {done}, pominięto {skipped}")
        )
