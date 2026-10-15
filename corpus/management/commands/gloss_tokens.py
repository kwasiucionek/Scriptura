"""Kontekstowe polskie glosy interlinii (Token.gloss_pl) dla SBLGNT i WLC — przekład roboczy modelu.

    python manage.py gloss_tokens                                # wszystko bez glos, obie księgi
    python manage.py gloss_tokens --works SBLGNT --books Phil John --workers 4
    python manage.py gloss_tokens --books Gen --force             # ponownie, także z glosami
    python manage.py gloss_tokens --base-url http://localhost:11434 --model deepseek-v4.1-flash:cloud

Wznawialne: bez --force bierze tylko wersety, w których któreś słowo nie ma glosy. Zapytania do
modelu idą w wątkach (--workers), zapis do bazy w głównym wątku po każdej paczce.
"""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from corpus import interlinear
from corpus.models import Book, Lexeme, Token, VerseText

DEFAULT_WORKS = ["SBLGNT", "WLC"]


def lexicon_glosses() -> tuple[dict[str, str], dict[str, str]]:
    """Glosy STEP (EN): po Strongu i — dla greki bez Stronga — po lemacie."""
    by_strong, by_lemma = {}, {}
    for strong, lang, lemma_norm, gloss in Lexeme.objects.values_list(
        "strong", "language", "lemma_norm", "gloss"
    ):
        by_strong[strong] = gloss
        if lang == "grc":
            by_lemma.setdefault(lemma_norm, gloss)
    return by_strong, by_lemma


def strong_gloss(by_strong: dict[str, str], strong: str) -> str:
    if not strong:
        return ""
    return by_strong.get(strong) or by_strong.get(
        strong.rstrip("abcdefghijklmnopqrstuvwxyz"), ""
    )


class Command(BaseCommand):
    help = "Polskie glosy kontekstowe słów SBLGNT/WLC (interlinia) modelem z Ollamy"

    def add_arguments(self, parser):
        parser.add_argument("--works", nargs="*", default=DEFAULT_WORKS)
        parser.add_argument(
            "--books", nargs="*", help="OSIS ksiąg (domyślnie wszystkie)"
        )
        parser.add_argument("--model", default=settings.INTERLINEAR_MODEL)
        parser.add_argument("--base-url", default=settings.INTERLINEAR_BASE_URL)
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--max-words", type=int, default=320)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--limit", type=int, default=0, help="maks. liczba paczek")

    def handle(self, *args, **o):
        api_key = settings.OLLAMA_API_KEY if "ollama.com" in o["base_url"] else ""
        by_strong, by_lemma = lexicon_glosses()
        books = Book.objects.order_by("order")
        if o["books"]:
            books = books.filter(osis__in=o["books"])
        pending = self._batches(
            o, books, by_strong, by_lemma
        )  # leniwie, księga po księdze
        tokens = Token.objects.filter(
            verse_text__work__code__in=o["works"], verse_text__verse__book__in=books
        )
        if not o["force"]:
            tokens = tokens.filter(gloss_pl="")
        total_words = tokens.count()
        self.stdout.write(
            f"ok. {total_words} słów do glosowania (paczki ≤ {o['max_words']} słów) — model {o['model']}"
        )
        done_words, failed_all, t0 = 0, [], time.monotonic()
        workers = max(1, o["workers"])
        with ThreadPoolExecutor(workers) as pool:
            queue = pending
            running = {}
            for batch in queue:
                running[pool.submit(self._work, batch, o, api_key)] = batch
                if len(running) >= workers * 2:
                    break
            limit_hit = ""
            while running:
                finished, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in finished:
                    batch = running.pop(fut)
                    try:
                        glosses, failed = fut.result()
                    except interlinear.UsageLimitError as exc:
                        limit_hit = str(exc)
                        continue
                    self._save(glosses)
                    failed_all += failed
                    done_words += sum(len(v.words) for v in batch)
                    nxt = None if limit_hit else next(queue, None)
                    if nxt is not None:
                        running[pool.submit(self._work, nxt, o, api_key)] = nxt
                if limit_hit:  # nie zjadamy dalej limitu, z którego korzysta asystent
                    # zostają tylko paczki już w toku (cancel() zwraca False)
                    running = {f: b for f, b in running.items() if not f.cancel()}
                elapsed = time.monotonic() - t0
                rate = done_words / elapsed if elapsed else 0
                eta = (total_words - done_words) / rate / 60 if rate else 0
                self.stdout.write(
                    f"  {done_words}/{total_words} słów · {rate:.0f}/s · ETA {eta:.0f} min"
                    f" · ostatnio {batch[0].osis}–{batch[-1].osis}"
                    + (f" · nieudane {len(failed_all)}" if failed_all else "")
                )
        if failed_all:
            self.stdout.write(
                self.style.WARNING(
                    f"bez glos ({len(failed_all)}): {' '.join(failed_all[:50])}"
                )
            )
        if limit_hit:
            raise CommandError(
                "zatrzymano: wyczerpany limit modelu w chmurze — uruchom ponownie później "
                f"(komenda wznowi od brakujących wersetów). {limit_hit[:200]}"
            )
        self.stdout.write(self.style.SUCCESS("gotowe"))

    def _batches(self, o, books, by_strong, by_lemma):
        n = 0
        for code in o["works"]:
            for book in books:
                verses = self._verses(code, book, o["force"], by_strong, by_lemma)
                for batch in interlinear.batches(verses, max_words=o["max_words"]):
                    if o["limit"] and n >= o["limit"]:
                        return
                    n += 1
                    yield batch

    @staticmethod
    def _work(batch, o, api_key):
        return interlinear.gloss_verses(batch, o["model"], o["base_url"], api_key)

    def _verses(self, code, book, force, by_strong, by_lemma):
        vts = VerseText.objects.filter(work__code=code, verse__book=book)
        if not force:
            vts = vts.filter(tokens__gloss_pl="").distinct()
        vts = vts.select_related("verse").order_by("verse__ordinal")
        out = []
        tokens = Token.objects.filter(verse_text__in=vts).order_by(
            "verse_text_id", "position"
        )
        by_vt: dict[int, list[Token]] = {}
        for t in tokens:
            by_vt.setdefault(t.verse_text_id, []).append(t)
        for vt in vts:
            words = []
            for t in by_vt.get(vt.id, []):
                gloss = strong_gloss(by_strong, t.strong) or by_lemma.get(
                    t.lemma_norm, ""
                )
                words.append(
                    {
                        "i": t.position,
                        "w": t.surface,
                        "lemma": t.lemma,
                        "morph": f"{t.pos} {t.morph}".strip() if t.pos else t.morph,
                        "gloss": gloss,
                    }
                )
            if words:
                out.append(interlinear.VerseItem(vt.verse.osis_id, vt.id, words))
        return out

    @staticmethod
    def _save(glosses: dict[int, dict[int, str]]) -> None:
        if not glosses:
            return
        tokens = list(Token.objects.filter(verse_text_id__in=list(glosses)))
        for t in tokens:
            t.gloss_pl = glosses[t.verse_text_id].get(t.position, t.gloss_pl)
        with transaction.atomic():
            Token.objects.bulk_update(tokens, ["gloss_pl"], batch_size=1000)
