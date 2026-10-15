"""Polskie znaczenia haseł leksykonu (Lexeme.gloss_pl) — przekład roboczy glos STEP modelem z Ollamy.

    python manage.py translate_lexicon                 # hasła bez gloss_pl
    python manage.py translate_lexicon --strongs G725 H7225 --force
    python manage.py translate_lexicon --used-only     # tylko hasła występujące w tokenach

Model dostaje paczkę haseł (Strong, lemat, glosa EN, początek definicji) i zwraca
{"G725": "grabież, zdobycz, rzecz do zagarnięcia", ...}. Na stronie słownika glosa polska
jest opisana jako przekład roboczy; definicje STEP i Stronga zostają w oryginale.
"""

import json
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from corpus import interlinear
from corpus.models import Lexeme, Token

SYSTEM_PROMPT = (
    "Jesteś leksykografem biblijnym. Dla każdego hasła słownika (hebrajskiego, aramejskiego albo "
    "greckiego) podaj jego polskie odpowiedniki: 1–5 słów lub krótkich wyrażeń, oddzielonych "
    "przecinkami, od podstawowego znaczenia do pochodnych, w formie słownikowej (rzeczownik w "
    "mianowniku, czasownik w bezokoliczniku). Imiona i nazwy własne w formie przyjętej w polskich "
    "przekładach Biblii (np. Abraham, Jerozolima, Kafarnaum), z krótkim dopowiedzeniem, gdy "
    "glosa je zawiera (np. „Abiasz (król Judy)”). Zwróć wyłącznie JSON: "
    '{"<Strong>": "<odpowiedniki>", ...} — po jednym kluczu dla każdego hasła z wejścia.'
)


def build_user(entries: list[Lexeme]) -> str:
    payload = [
        {
            "s": e.strong,
            "lemma": e.lemma,
            "gloss": e.gloss,
            "def": (e.meaning or "")[:200],
        }
        for e in entries
    ]
    return "Hasła:\n" + json.dumps(payload, ensure_ascii=False)


def parse(text: str, entries: list[Lexeme]) -> dict[str, str]:
    data = interlinear.extract_json(text)
    if not isinstance(data, dict):
        return {}
    wanted = {e.strong for e in entries}
    out = {}
    for k, v in data.items():
        if k in wanted and isinstance(v, str) and v.strip():
            v = " ".join(v.split())[:200]
            if not interlinear.ORIGINAL_SCRIPT.search(v):
                out[k] = v
    return out


class Command(BaseCommand):
    help = (
        "Polskie odpowiedniki haseł leksykonu STEP (Lexeme.gloss_pl) modelem z Ollamy"
    )

    def add_arguments(self, parser):
        parser.add_argument("--strongs", nargs="*")
        parser.add_argument("--used-only", action="store_true")
        parser.add_argument("--model", default=settings.INTERLINEAR_MODEL)
        parser.add_argument("--base-url", default=settings.INTERLINEAR_BASE_URL)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--batch", type=int, default=60)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **o):
        api_key = settings.OLLAMA_API_KEY if "ollama.com" in o["base_url"] else ""
        qs = Lexeme.objects.exclude(gloss="").order_by("strong")
        if o["strongs"]:
            qs = qs.filter(strong__in=o["strongs"])
        if not o["force"]:
            qs = qs.filter(gloss_pl="")
        if o["used_only"]:
            used = set(
                Token.objects.exclude(strong="")
                .values_list("strong", flat=True)
                .distinct()
            )
            qs = qs.filter(strong__in=used)
        entries = list(qs)
        chunks = [
            entries[i : i + o["batch"]] for i in range(0, len(entries), o["batch"])
        ]
        self.stdout.write(
            f"{len(entries)} haseł w {len(chunks)} paczkach — model {o['model']}"
        )
        t0, done, missing = time.monotonic(), 0, []

        def _one(
            chunk,
        ):  # wątek: tylko model; brakujące hasła ponawiane raz w mniejszej paczce
            def ask(items):
                try:
                    return parse(
                        interlinear.chat(
                            SYSTEM_PROMPT,
                            build_user(items),
                            o["model"],
                            o["base_url"],
                            api_key,
                            num_predict=len(items) * 60 + 300,
                        ),
                        items,
                    )
                except interlinear.UsageLimitError:
                    raise
                except Exception:  # noqa: BLE001
                    return {}

            got = ask(chunk)
            rest = [e for e in chunk if e.strong not in got]
            if rest:
                got.update(ask(rest))
            return chunk, got

        limit_hit = ""
        with ThreadPoolExecutor(max(1, o["workers"])) as pool:
            futures = [pool.submit(_one, c) for c in chunks]
            for fut in as_completed(futures):
                try:
                    chunk, got = fut.result()
                except interlinear.UsageLimitError as exc:
                    # nie zjadamy dalej limitu, z którego korzysta asystent
                    limit_hit = str(exc)
                    for f in futures:
                        f.cancel()
                    continue
                except CancelledError:
                    continue
                for e in chunk:
                    if e.strong in got:
                        e.gloss_pl = got[e.strong]
                    else:
                        missing.append(e.strong)
                with transaction.atomic():
                    Lexeme.objects.bulk_update(
                        [e for e in chunk if e.strong in got], ["gloss_pl"]
                    )
                done += len(chunk)
                self.stdout.write(
                    f"  {done}/{len(entries)} · {time.monotonic() - t0:.0f} s"
                    + (f" · bez wyniku {len(missing)}" if missing else "")
                )
        if missing:
            self.stdout.write(
                self.style.WARNING("bez wyniku: " + " ".join(missing[:80]))
            )
        if limit_hit:
            raise CommandError(
                "zatrzymano: wyczerpany limit modelu w chmurze — uruchom ponownie później. "
                + limit_hit[:200]
            )
        self.stdout.write(self.style.SUCCESS("gotowe"))
