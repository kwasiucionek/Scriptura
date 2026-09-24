"""Włączanie bazy ANE w RAG: jawnie (opcja) albo gdy pytanie wymienia tekst / postać z ANE."""

import re
import unicodedata

from django.conf import settings

from ane.search import AneHit, retrieve

ANE_TRIGGERS = [
    "gilgamesz", "gilgamesh", "utnapisztim", "utnapishtim", "enkidu", "enuma elisz", "enuma elish",
    "tiamat", "marduk", "apsu", "atrahasis", "atra-hasis", "ziusudra", "anzu", "erra", "isztar", "ishtar",
    "enki", "enlil", "szamasz", "shamash", "hammurabi", "hammurapi", "ugaryck", "ugarit", "baal",
    "aqhat", "danel", "keret", "kirta", "mezopotam", "babiloń", "sumer", "akadyj", "asyryj", "epos o",
    "mit o potopie", "potop mezopotamski", "teksty starożytnego bliskiego wschodu", "ane",
]  # fmt: skip


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


_WORD_TRIGGERS = {
    "ea",
    "el",
    "elohim",
}  # tylko jako całe słowo (inaczej „zmienił”, „Ela” itd.)


def wants_ane(question: str) -> bool:
    q = _fold(question)
    if any(re.search(rf"(?<![a-z]){re.escape(_fold(t))}", q) for t in ANE_TRIGGERS):
        return True
    return any(
        re.search(rf"(?<![a-z]){w}(?![a-z])", q) for w in _WORD_TRIGGERS - {"elohim"}
    )


def ane_for_question(question: str, include: bool | None = None) -> list[AneHit]:
    """include=True wymusza, False wyłącza, None = automatycznie po wyzwalaczach."""
    if not settings.RAG_ANE_PASSAGES:
        return []
    if include is False or (include is None and not wants_ane(question)):
        return []
    return retrieve(question, k=settings.RAG_ANE_PASSAGES)
