"""Syntetyczny zestaw ewaluacyjny: losowe chunki -> pytania wygenerowane przez model czatu.

    python manage.py eval_bootstrap --n 24 --out data/eval/questions.jsonl [--seed 7]

Dobór chunków: warstwowo po dokumentach (max 3 na dokument), preferowane chunki z siglami
i terminami oryginalnymi, długość 400–1400 znaków, bez chunków-bibliografii.
Typ pytania: pl+orig gdy chunk ma hebrajski/grecki, pl->en gdy dokument angielski, inaczej pl.
Pytanie generuje LLM (LLM_BACKEND=ollama); w trybie echo — szablon z nagłówka sekcji.
Plik JSONL do ręcznego przejrzenia, potem: scripts/eval_retrieval.py <plik> [--no-rerank].
"""

import json
import random
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from library.models import Chunk
from library.search import original_terms

PROMPT = (
    "Poniżej fragment tekstu z zakresu teologii biblijnej. Ułóż JEDNO naturalne pytanie po polsku, "
    "jakie mógłby zadać {audience}, na które ten fragment odpowiada. Pytanie ma być samodzielne "
    "(bez zwrotów typu 'w tym fragmencie', 'autor'), konkretne, 8–20 słów.{extra} Zwróć tylko treść pytania.\n\n"
    "FRAGMENT:\n{text}"
)
AUDIENCE = {
    "pl": "czytelnik zainteresowany Biblią",
    "pl+orig": "student biblistyki",
    "pl->en": "czytelnik polski",
}
EXTRA = {
    "pl+orig": " Użyj w pytaniu terminu hebrajskiego lub greckiego z fragmentu (w oryginale lub transliteracji).",
    "pl->en": " Fragment jest po angielsku, ale pytanie ma być po polsku.",
    "pl": "",
}
_BIBLIO_RE = re.compile(r"\b(?:jw\.|tamże|op\. cit\.|ibid\.)|\(\d{4}\),? \d+", re.I)


def _chat(prompt: str) -> str:
    body = {
        "model": settings.OLLAMA_CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.7},
    }
    headers = {"Content-Type": "application/json"}
    if settings.OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OLLAMA_API_KEY}"
    req = urllib.request.Request(
        f"{settings.OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        return json.load(resp)["message"]["content"].strip().strip('„”"')


def _question_type(chunk: Chunk) -> str:
    if chunk.document.language.startswith("en"):
        return "pl->en"
    grc, hbo = original_terms(chunk.text)
    return "pl+orig" if (grc or hbo) else "pl"


class Command(BaseCommand):
    help = "Wygeneruj pytania ewaluacyjne z losowych chunków"

    def add_arguments(self, parser):
        parser.add_argument("--n", type=int, default=24)
        parser.add_argument("--per-document", type=int, default=3)
        parser.add_argument("--seed", type=int, default=7)
        parser.add_argument("--out", default="data/eval/questions.jsonl")
        parser.add_argument("--access", nargs="*", default=["open"])
        parser.add_argument(
            "--documents",
            nargs="*",
            type=int,
            help="ogranicz losowanie do tych dokumentów",
        )

    def handle(self, *args, **o):
        rng = random.Random(o["seed"])
        qs = Chunk.objects.filter(document__access__in=o["access"]).select_related(
            "document"
        )
        if o["documents"]:
            qs = qs.filter(document_id__in=o["documents"])
        pool = [
            c for c in qs
            if 400 <= len(c.text) <= 1400 and not _BIBLIO_RE.search(c.text[:300])
        ]  # fmt: skip
        rng.shuffle(pool)
        # preferencja: chunki z siglami lub terminami oryginalnymi na początek
        pool.sort(key=lambda c: 0 if c.sigla or any(original_terms(c.text)) else 1)
        per_doc: dict[int, int] = defaultdict(int)
        chosen: list[Chunk] = []
        for c in pool:
            if per_doc[c.document_id] >= o["per_document"]:
                continue
            per_doc[c.document_id] += 1
            chosen.append(c)
            if len(chosen) >= o["n"]:
                break
        rng.shuffle(chosen)

        out = Path(o["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for c in chosen:
            qtype = _question_type(c)
            if settings.LLM_BACKEND == "ollama":
                try:
                    q = _chat(
                        PROMPT.format(
                            audience=AUDIENCE[qtype],
                            extra=EXTRA[qtype],
                            text=c.text[:1400],
                        )
                    )
                except Exception as exc:
                    self.stderr.write(f"  LLM: {exc} — szablon")
                    q = f"Co wiadomo o: {c.section or c.document.title[:60]}?"
            else:
                q = f"Co wiadomo o: {c.section or c.document.title[:60]}?"
            rows.append(
                {
                    "q": q,
                    "type": qtype,
                    "relevant": [f"{c.document_id}:{c.order}"],
                    "note": f"{c.document.title[:50]} · {c.section[:40]}",
                }
            )
            self.stdout.write(f"  [{qtype:7}] {c.document_id}:{c.order:<4} {q[:90]}")
        with out.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(rows)} pytań -> {out}  (przejrzyj i popraw przed ewaluacją)"
            )
        )
