"""Tłumaczenie zapytania na angielski (do BM25/kNN po angielskich dokumentach).

Jedno krótkie wywołanie modelu czatu (library.llm), wynik w pamięci podręcznej procesu.
Pomijane, gdy RAG_TRANSLATE_QUERY=false, gdy LLM_BACKEND=echo, albo gdy pytanie
wygląda na już angielskie (brak polskich znaków i polskich słów funkcyjnych).
"""

import logging
import re
from functools import lru_cache

from django.conf import settings

from library import llm

log = logging.getLogger(__name__)

_POLISH_HINT = re.compile(
    r"[ąćęłńóśźż]|\b(?:jak|czy|co|jakie|dlaczego|który|która|w|i|z|na|do)\b", re.I
)


def looks_polish(text: str) -> bool:
    return bool(_POLISH_HINT.search(text))


@lru_cache(maxsize=2048)
def translate_query(question: str) -> str:
    """Angielska wersja pytania lub "" (gdy wyłączone / błąd / pytanie już angielskie)."""
    if not settings.RAG_TRANSLATE_QUERY or not llm.enabled():
        return ""
    if not looks_polish(question):
        return ""
    prompt = (
        "Translate this Polish question about biblical studies into English. "
        "Keep Hebrew/Greek/Ugaritic terms and transliterations unchanged. "
        "Return only the translation.\n\n" + question
    )
    try:
        return (
            llm.chat(
                [{"role": "user", "content": prompt}],
                model=settings.RAG_TRANSLATE_MODEL or None,
                temperature=0.0,
                max_tokens=120,
                timeout=30,
            )
            .strip()
            .strip('"')
        )
    except Exception as exc:
        log.warning("tłumaczenie zapytania pominięte: %s", exc)
        return ""
