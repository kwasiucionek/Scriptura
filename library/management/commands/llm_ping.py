"""Test połączenia z backendem czatu: python manage.py llm_ping [--think] [--prompt "..."]

Drukuje backend, model, strumień odpowiedzi, długość rozumowania, tokeny i czas —
do sprawdzenia konfiguracji po zmianie LLM_BACKEND / OPENAI_* / OLLAMA_*.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from library import llm


class Command(BaseCommand):
    help = "Krótkie wywołanie modelu czatu przez library.llm (diagnostyka backendu)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--prompt",
            default="Odpowiedz jednym zdaniem po polsku: czym jest Septuaginta?",
        )
        parser.add_argument(
            "--think", action="store_true", help="włącz tryb rozumowania"
        )
        parser.add_argument("--model", default="", help="nadpisz model backendu")

    def handle(self, *args, **o):
        self.stdout.write(
            f"backend={settings.LLM_BACKEND} model={llm.model_name(o['model'] or None)}"
        )
        if settings.LLM_BACKEND == "openai":
            self.stdout.write(
                f"url={settings.OPENAI_BASE_URL} thinking_kwargs={settings.OPENAI_THINKING_KWARGS}"
                f" key={'tak' if settings.OPENAI_API_KEY else 'BRAK'}"
            )
        t0 = time.monotonic()
        reasoning = 0
        last = None
        for d in llm.chat_stream(
            [{"role": "user", "content": o["prompt"]}],
            model=o["model"] or None,
            temperature=0.2,
            max_tokens=400,
            think=o["think"],
            timeout=180,
        ):
            if d.done:
                last = d
                break
            reasoning += len(d.reasoning)
            if d.content:
                self.stdout.write(d.content, ending="")
                self.stdout.flush()
        self.stdout.write("")
        ms = int((time.monotonic() - t0) * 1000)
        self.stdout.write(
            f"--- {ms} ms · rozumowanie {reasoning} zn. · tokeny "
            f"{last.prompt_tokens if last else '?'}/{last.completion_tokens if last else '?'}"
        )
