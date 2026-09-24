"""Maszynowy przekład chunków dokumentów obcojęzycznych na polski (TranslateGemma przez Ollamę).

    python manage.py translate_chunks                       # wszystkie dokumenty != pl bez text_pl
    python manage.py translate_chunks --documents 50 57 --force

Przekład trafia do Chunk.text_pl i służy WYŁĄCZNIE wyszukiwaniu (BM25 + embedding); w prompcie,
panelu źródeł i weryfikacji cytatów zostaje oryginał. To istotne licencyjnie (CC BY-ND zabrania
rozpowszechniania utworów zależnych; wewnętrzny indeks nim nie jest) i merytorycznie.
Potem: reindex_chunks --recreate (embeddingi liczone z tekstu polskiego).
TranslateGemma wymaga dokładnie tego formatu promptu (dwie puste linie przed tekstem); dla modeli
ogólnych (np. gemma4:31b-cloud) prompt dostaje dopisek o zachowaniu sigli i transliteracji.
    TRANSLATE_MODEL=gemma4:31b-cloud TRANSLATE_BASE_URL=https://ollama.com  -> przekład w chmurze
"""

import json
import time
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand

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
        "options": {"temperature": 0.0, "num_ctx": 8192},
        "keep_alive": "1h",
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        f"{base_url}/api/chat", data=json.dumps(body).encode(), headers=headers
    )
    with urllib.request.urlopen(req, timeout=600) as resp:  # noqa: S310
        return json.load(resp)["message"]["content"].strip()


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

    def handle(self, *args, **o):
        qs = Chunk.objects.exclude(document__language__startswith="pl").select_related(
            "document"
        )
        if o["documents"]:
            qs = qs.filter(document_id__in=o["documents"])
        if not o["force"]:
            qs = qs.filter(text_pl="")
        qs = qs.order_by("document_id", "order")
        if o["limit"]:
            qs = qs[: o["limit"]]
        total = qs.count()
        self.stdout.write(f"{total} chunków do przetłumaczenia modelem {o['model']}")
        done, t0 = 0, time.monotonic()
        for ch in qs.iterator(chunk_size=50):
            try:
                ch.text_pl = translate(
                    ch.text,
                    ch.document.language,
                    o["model"],
                    o["base_url"],
                    settings.OLLAMA_API_KEY,
                )
            except Exception as exc:
                self.stderr.write(f"  {ch.document_id}:{ch.order} błąd: {exc}")
                continue
            ch.save(update_fields=["text_pl"])
            done += 1
            if done % 10 == 0:
                rate = (time.monotonic() - t0) / done
                self.stdout.write(
                    f"  {done}/{total}  ~{rate:.1f} s/chunk, zostało ~{int(rate * (total - done) / 60)} min"
                )
        from library.search import reset_language_cache

        reset_language_cache()
        self.stdout.write(
            self.style.SUCCESS(
                f"przetłumaczono {done}/{total}; teraz: reindex_chunks --recreate"
            )
        )
