"""Maszynowy przekład chunków dokumentów obcojęzycznych na polski (TranslateGemma przez Ollamę).

    python manage.py translate_chunks                       # wszystkie dokumenty != pl bez text_pl
    python manage.py translate_chunks --documents 50 57 --force

Przekład trafia do Chunk.text_pl i służy wyszukiwaniu (BM25 + embedding); w prompcie,
panelu źródeł i weryfikacji cytatów zostaje oryginał. To istotne licencyjnie (CC BY-ND zabrania
rozpowszechniania utworów zależnych; wewnętrzny indeks nim nie jest) i merytorycznie.
Wyjątek: dokumenty Kościoła (doc_type=church) — przekład roboczy idzie do promptu, a panel
pokazuje z niego fragment z oryginałem i odsyłaczem (library.search.use_working_translations).
Potem: reindex_chunks --recreate (embeddingi liczone z tekstu polskiego).
TranslateGemma wymaga dokładnie tego formatu promptu (dwie puste linie przed tekstem); dla modeli
ogólnych (np. gemma4:31b-cloud) prompt dostaje dopisek o zachowaniu sigli i transliteracji.
    TRANSLATE_MODEL=gemma4:31b-cloud TRANSLATE_BASE_URL=https://ollama.com  -> przekład w chmurze
"""

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand

from library.batching import in_batches
from library.models import Chunk

LANG_NAMES = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "la": "Latin",
}
PROMPT = (
    "You are a professional {src_name} ({src}) to Polish (pl) translator. Your goal is to accurately "
    "convey the meaning and nuances of the original {src_name} text while adhering to Polish grammar, "
    "vocabulary, and cultural sensitivities.\nProduce only the Polish translation, without any additional "
    "explanations or commentary. Please translate the following {src_name} text into Polish:\n\n\n{text}"
)


# Dopisek dla modeli ogólnych (Gemma 4 itp.) — TranslateGemma go nie rozumie i dostaje czysty format
PRESERVE_NOTE = (
    "\n\nKeep unchanged: biblical references (e.g. Gen 1:1, Wj 40,34), Hebrew, Greek and Ugaritic "
    "words and their transliterations (e.g. miškān, ṭḫ, tohu wabohu), proper names and bibliographic "
    "citations. Use Polish biblical terminology (Księga Wyjścia, Przybytek, Septuaginta)."
)


# wstęp, który dopisują modele ogólne (Mistral: „Oto polskie tłumaczenie:”)
_PREFACE_RE = re.compile(
    r"^\s*(?:oto|poniżej)\b[^\n]{0,80}?(?:tłumaczenie|przekład)[^\n]{0,40}:\s*\n+",
    re.IGNORECASE,
)
_EMPHASIS_RE = re.compile(r"\*{1,3}([^*\n]+?)\*{1,3}")


def clean_translation(text: str, source: str) -> str:
    """Przekład bez wstępu modelu i markdownowych gwiazdek, których nie było w oryginale."""
    text = _PREFACE_RE.sub("", text, count=1)
    if "*" not in source:
        text = _EMPHASIS_RE.sub(r"\1", text)
    return text.strip()


def build_prompt(text: str, src: str, model: str) -> str:
    src_name = LANG_NAMES.get(src[:2], src)
    prompt = PROMPT.format(src=src[:2], src_name=src_name, text=text)
    if not model.lower().startswith("translategemma"):
        prompt = prompt.replace("\n\n\n" + text, PRESERVE_NOTE + "\n\n" + text)
    return prompt


def translate(text: str, src: str, model: str, base_url: str, api_key: str = "") -> str:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": build_prompt(text, src, model)}],
        "stream": False,
        "think": False,
        # limit odpowiedzi ~2× długość wejścia: zapętlony model kończy się szybko, zamiast trzymać wątek
        "options": {
            "temperature": 0.0,
            "num_ctx": 8192,
            "num_predict": len(text) // 2 + 256,
        },
        "keep_alive": "1h",
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        f"{base_url}/api/chat", data=json.dumps(body).encode(), headers=headers
    )
    with urllib.request.urlopen(req, timeout=settings.TRANSLATE_TIMEOUT) as resp:  # noqa: S310
        return clean_translation(json.load(resp)["message"]["content"], text)


class Command(BaseCommand):
    help = "Przetłumacz chunki obcojęzyczne na polski (do wyszukiwania) modelem TranslateGemma"

    def add_arguments(self, parser):
        parser.add_argument("--documents", nargs="*", type=int)
        parser.add_argument("--model", default=settings.TRANSLATE_MODEL)
        parser.add_argument("--base-url", default=settings.TRANSLATE_BASE_URL)
        parser.add_argument(
            "--force", action="store_true", help="tłumacz także chunki z text_pl"
        )
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument(
            "--workers",
            type=int,
            default=1,
            help="równoległe żądania do modelu (model w chmurze); zapis do bazy zawsze w głównym wątku",
        )

    def handle(self, *args, **o):
        qs = Chunk.objects.exclude(document__language__startswith="pl").select_related(
            "document"
        )
        if o["documents"]:
            qs = qs.filter(document_id__in=o["documents"])
        if not o["force"]:
            qs = qs.filter(text_pl="")
        qs = qs.order_by("document_id", "order")
        total = min(qs.count(), o["limit"]) if o["limit"] else qs.count()
        self.stdout.write(f"{total} chunków do przetłumaczenia modelem {o['model']}")
        done, t0 = 0, time.monotonic()
        workers = max(1, o["workers"])

        def _one(ch):  # wątek: tylko żądanie do modelu, bez dostępu do bazy
            try:
                text = translate(
                    ch.text,
                    ch.document.language,
                    o["model"],
                    o["base_url"],
                    settings.OLLAMA_API_KEY,
                )
                return ch, text, None
            except Exception as exc:
                return ch, None, exc

        def _flush(pool, batch) -> int:
            n = 0
            for ch, text, exc in pool.map(_one, batch):
                if exc is not None:
                    self.stderr.write(f"  {ch.document_id}:{ch.order} błąd: {exc}")
                    continue
                ch.text_pl = text
                ch.save(update_fields=["text_pl"])
                n += 1
            return n

        # bez otwartego kursora w czasie zapisów (SQLite WAL: „database is locked”) — patrz library.batching
        with ThreadPoolExecutor(max_workers=workers) as pool:
            batch: list[Chunk] = []
            for ch in in_batches(qs, 50, o["limit"]):
                batch.append(ch)
                if len(batch) >= max(10, workers * 4):
                    done += _flush(pool, batch)
                    batch = []
                    rate = (time.monotonic() - t0) / max(done, 1)
                    self.stdout.write(
                        f"  {done}/{total}  ~{rate:.1f} s/chunk, zostało ~{int(rate * (total - done) / 60)} min"
                    )
            if batch:
                done += _flush(pool, batch)
        from library.search import reset_language_cache

        reset_language_cache()
        self.stdout.write(
            self.style.SUCCESS(
                f"przetłumaczono {done}/{total}; teraz: reindex_chunks --recreate"
            )
        )
