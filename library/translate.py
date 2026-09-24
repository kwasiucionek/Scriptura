"""Tłumaczenie zapytania na angielski (do BM25/kNN po angielskich dokumentach).

Jedno krótkie wywołanie modelu czatu (Ollama), wynik w pamięci podręcznej procesu.
Pomijane, gdy RAG_TRANSLATE_QUERY=false, gdy LLM_BACKEND != ollama, albo gdy pytanie
wygląda na już angielskie (brak polskich znaków i polskich słów funkcyjnych).
"""

import json
import logging
import re
import urllib.request
from functools import lru_cache

from django.conf import settings

log = logging.getLogger(__name__)

_POLISH_HINT = re.compile(
    r"[ąćęłńóśźż]|\b(?:jak|czy|co|jakie|dlaczego|który|która|w|i|z|na|do)\b", re.I
)


def looks_polish(text: str) -> bool:
    return bool(_POLISH_HINT.search(text))


@lru_cache(maxsize=2048)
def translate_query(question: str) -> str:
    """Angielska wersja pytania lub "" (gdy wyłączone / błąd / pytanie już angielskie)."""
    if not settings.RAG_TRANSLATE_QUERY or settings.LLM_BACKEND != "ollama":
        return ""
    if not looks_polish(question):
        return ""
    body = {
        "model": settings.RAG_TRANSLATE_MODEL or settings.OLLAMA_CHAT_MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Translate this Polish question about biblical studies into English. "
                    "Keep Hebrew/Greek/Ugaritic terms and transliterations unchanged. "
                    "Return only the translation.\n\n" + question
                ),
            }
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0, "num_predict": 120},
    }
    headers = {"Content-Type": "application/json"}
    if settings.OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {settings.OLLAMA_API_KEY}"
    req = urllib.request.Request(
        f"{settings.OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.load(resp)["message"]["content"].strip().strip('"')
    except Exception as exc:
        log.warning("tłumaczenie zapytania pominięte: %s", exc)
        return ""
