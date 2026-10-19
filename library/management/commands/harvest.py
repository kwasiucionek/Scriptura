"""Zbierz manifest prac ze źródła (bez pobierania plików).

    python manage.py harvest dspace --browse-author "Majewski, Marcin" --out data/manifests/majewski-open.jsonl
    python manage.py harvest dspace --query "Rosik Mariusz" --base-url https://open.icm.edu.pl ...
    python manage.py harvest openalex --author "Marcin Majewski"            # lista kandydatów z afiliacją
    python manage.py harvest openalex --author-id A5012345678 --only-oa --out data/manifests/majewski-oa.jsonl
    python manage.py harvest openalex --journal "Verbum Vitae"                 # kandydaci czasopism (S…)
    python manage.py harvest openalex --journal-id S4210212345 --only-oa --from-year 2015 --out data/manifests/vv.jsonl
    python manage.py harvest scholar --scholar-id r0NgMwMAAAAJ --out data/manifests/majewski-scholar.jsonl
    python manage.py harvest bibtex --file ~/Pobrane/citations.bib --out data/manifests/majewski-scholar.jsonl
    python manage.py harvest vatican --out data/manifests/vatican.jsonl        # dokumenty Kościoła o Piśmie (vatican.va)
    python manage.py harvest vatican --keys dei-verbum verbum-domini
    python manage.py harvest vatican --polish-pd --translate-model mistral-large-3:675b-cloud   # pełny przekład z łaciny (domena publiczna)

Manifest przejrzyj ręcznie (kolumna access: open/licensed/private/skip), potem `ingest_manifest`.
"""

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from library.harvest import (
    bibtex,
    blog,
    dspace,
    manifest,
    openalex,
    scholar,
    vatican,
    youtube,
)


class Command(BaseCommand):
    help = "Harvest metadanych do manifestu JSONL (dspace | openalex | scholar | bibtex | blog | youtube | vatican)"

    def add_arguments(self, parser):
        parser.add_argument(
            "source",
            choices=[
                "dspace",
                "openalex",
                "scholar",
                "bibtex",
                "blog",
                "youtube",
                "vatican",
            ],
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
            "--journal", help="openalex: nazwa czasopisma do wyszukania kandydatów"
        )
        parser.add_argument(
            "--journal-id", help="openalex: id czasopisma (S…) do pobrania prac"
        )
        parser.add_argument(
            "--from-year", type=int, help="openalex --journal-id: od rocznika"
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
            "--channel",
            help="youtube: adres kanału albo dowolny film z kanału — wszystkie filmy kanału",
        )
        parser.add_argument(
            "--min-minutes",
            type=float,
            default=10,
            help="youtube --channel: pomiń filmy krótsze niż N minut (zapowiedzi, fragmenty)",
        )
        parser.add_argument(
            "--skip-manifest",
            nargs="*",
            default=[],
            help="youtube: pomiń filmy już obecne w tych manifestach (po source_id)",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="youtube --channel: najwyżej N filmów"
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=8.0,
            help="youtube: przerwa (s) między filmami — YouTube ogranicza serie żądań (HTTP 429)",
        )
        parser.add_argument(
            "--retries",
            type=int,
            default=3,
            help="youtube: ponowienia po HTTP 429 (odczekanie 60 s, 120 s, 240 s…)",
        )
        parser.add_argument(
            "--access",
            default="",
            help="youtube: dostęp wpisów (domyślnie licensed — wymaga zgody; open po zgodzie)",
        )
        parser.add_argument(
            "--license",
            default="",
            dest="license_text",
            help="youtube: opis licencji/zgody, np. „udostępnione za zgodą autora (data)”",
        )
        parser.add_argument(
            "--keys",
            nargs="*",
            help="vatican: klucze dokumentów z library.harvest.vatican.DOCUMENTS (domyślnie wszystkie)",
        )
        parser.add_argument(
            "--polish-pd",
            action="store_true",
            help="vatican: pełny polski przekład roboczy z oryginału (łacina/greka/angielski) tekstów z domeny publicznej",
        )
        parser.add_argument(
            "--translate-model",
            default=settings.TRANSLATE_MODEL,
            help="vatican --polish-pd: model przekładu (Ollama, także tagi -cloud)",
        )
        parser.add_argument(
            "--translate-workers",
            type=int,
            default=4,
            help="vatican --polish-pd: równoległe żądania do modelu",
        )
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
            if not (o["video"] or o["channel"]) or not o["author"]:
                raise CommandError(
                    "youtube wymaga --video (adresy/id) albo --channel oraz --author"
                )
            if o["access"] and o["access"] not in ("open", "licensed", "private"):
                raise CommandError("--access: open | licensed | private")
            if o["access"] == "open" and not o["license_text"]:
                raise CommandError(
                    "--access open wymaga --license z opisem zgody autora"
                )
            videos = list(o["video"] or [])
            if o["channel"]:
                listed = youtube.channel_videos(
                    o["channel"], o["min_minutes"], o["limit"]
                )
                self.stdout.write(f"kanał: {len(listed)} filmów")
                videos += [v.video_id for v in listed]
            skip = {
                e.source_id
                for f in o["skip_manifest"]
                if Path(f).exists()
                for e in manifest.read(Path(f))
            }
            todo = list(dict.fromkeys(youtube.video_id(v) for v in videos))
            todo = [v for v in todo if v not in skip]
            if skip:
                self.stdout.write(
                    f"pomijam {len(videos) - len(todo)} już w manifestach"
                )
            extra = {}
            if o["access"]:
                extra["access"] = o["access"]
            if o["license_text"]:
                extra["license"] = o["license_text"]
            out_dir = settings.DATA_DIR / "library"
            out_path = (
                Path(o["out"])
                if o["out"]
                else settings.MANIFESTS_DIR / f"{source}.jsonl"
            )
            # --out już istnieje: dopisujemy (bez duplikatów), żeby ponowne uruchomienie
            # uzupełniało brakujące filmy zamiast nadpisywać zebrane
            entries = manifest.read(out_path) if out_path.exists() else []
            have = {e.source_id for e in entries}
            todo = [v for v in todo if v not in have]
            failed: list[str] = []
            for i, v in enumerate(todo):
                if i:
                    time.sleep(o["delay"])
                info = None
                for attempt in range(o["retries"] + 1):
                    try:
                        info = youtube.fetch(v)
                        break
                    except Exception as exc:  # noqa: BLE001 — jeden film nie zatrzymuje reszty
                        if youtube.is_rate_limited(exc) and attempt < o["retries"]:
                            wait = 60 * 2**attempt
                            self.stderr.write(f"  {v}: HTTP 429 — czekam {wait} s")
                            time.sleep(wait)
                            continue
                        self.stderr.write(f"  {v}: {exc}")
                        break
                if info is None:
                    failed.append(v)
                    continue
                if not info.captions:
                    self.stderr.write(f"  {info.title[:60]}: brak napisów — pomijam")
                    continue
                entries.append(youtube.to_entry(info, o["author"], out_dir, **extra))
                # zapis po każdym filmie: przerwanie nie gubi postępu
                manifest.write(out_path, entries)
                self.stdout.write(
                    f"  {info.title[:60]}: {len(info.captions)} napisów, "
                    f"{len(info.chapters)} rozdziałów ({info.caption_kind})"
                )
            if failed:
                self.stderr.write(
                    f"nieudane ({len(failed)}): {' '.join(failed)} — to samo polecenie "
                    "uruchomione ponownie dobierze brakujące"
                )

        elif source == "vatican":
            known = {d.key for d in vatican.DOCUMENTS}
            unknown = sorted(set(o["keys"] or []) - known)
            if unknown:
                raise CommandError(
                    f"nieznane klucze: {', '.join(unknown)} (dostępne: {', '.join(sorted(known))})"
                )
            out_dir = settings.DATA_DIR / "library" / "vatican"
            if o["polish_pd"]:
                from library.management.commands.translate_chunks import translate

                model = o["translate_model"]
                docs = [
                    d
                    for d in vatican.DOCUMENTS
                    if d.public_domain and (not o["keys"] or d.key in o["keys"])
                ]
                entries = []
                for d in docs:
                    lang = d.original_language
                    self.stdout.write(f"  {d.key}: przekład ({lang}) modelem {model}…")

                    def to_polish(text: str, lang: str = lang) -> str:
                        return translate(
                            text,
                            lang,
                            model,
                            settings.TRANSLATE_BASE_URL,
                            settings.OLLAMA_API_KEY,
                        )

                    entries.append(
                        vatican.polish_translation(
                            d,
                            vatican.source_text(d, d.original_url),
                            out_dir,
                            to_polish,
                            o["translate_workers"],
                        )
                    )
            else:
                entries = vatican.harvest(out_dir, o["keys"] or None)

        elif source == "bibtex":
            if not o["file"]:
                raise CommandError("bibtex wymaga --file")
            entries = bibtex.parse(Path(o["file"]).read_text(encoding="utf-8"))

        else:  # openalex
            if o["journal_id"]:
                entries = openalex.works_for_source(
                    o["journal_id"], mailto, o["only_oa"], o["from_year"]
                )
            elif o["journal"]:
                for src in openalex.find_sources(o["journal"], mailto):
                    self.stdout.write(
                        f"  {src['id'].rsplit('/', 1)[-1]:12} {src['display_name'][:50]:50} "
                        f"ISSN {src['issn'] or '-':9}  {src['works_count']:>6} prac  {src['publisher'][:30]}"
                    )
                self.stdout.write("Wybierz --journal-id i uruchom ponownie.")
                return
            elif o["author_id"]:
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
