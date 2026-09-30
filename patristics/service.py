"""Włączanie bazy Ojców w RAG: jawnie (opcja) albo gdy pytanie wymienia Ojców / tradycję / patrystykę."""

import unicodedata

from django.conf import settings

from patristics.search import PatHit, retrieve

PAT_TRIGGERS = [
    "ojcowie", "ojców", "ojcow", "patrysty", "tradycja patrystyczna", "tradycja kościoła",
    "orygenes", "augustyn", "chryzostom", "ireneusz", "tertulian", "hieronim", "ambroży",
    "bazyli", "grzegorz z nyssy", "grzegorz z nazjanzu", "grzegorz wielki", "atanazy",
    "cyryl aleksandryjski", "cyryl jerozolimski", "justyn", "klemens aleksandryjski",
    "klemens rzymski", "ignacy antiocheński", "polikarp", "euzebiusz", "efrem", "kasjan",
    "leon wielki", "hipolit", "cyprian", "laktancjusz", "egzegeza alegoryczna",
    "szkoła aleksandryjska", "szkoła antiocheńska", "list barnaby", "pasterz hermasa",
    "didache", "ojcowie apostolscy", "papiasz", "list do diogneta",
]  # fmt: skip

def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def wants_patristics(question: str) -> bool:
    q = _fold(question)
    return any(_fold(t) in q for t in PAT_TRIGGERS)


def patristics_for_question(
    question: str, ranges: list[tuple[int, int]], include: bool | None = None
) -> list[PatHit]:
    if not settings.RAG_PATRISTIC_PASSAGES:
        return []
    if include is False or (include is None and not wants_patristics(question)):
        return []
    return retrieve(question, k=settings.RAG_PATRISTIC_PASSAGES, ranges=ranges)
