"""Język tekstu po słowach funkcyjnych — bez zewnętrznych zależności.

Metadane (OpenAlex) zgadują język po tytule, więc polska recenzja włoskiej książki bywa „it”,
a stary artykuł po OCR — „de”. Przekład „z niemieckiego” tekstu, który jest polski, kończy się
zapętleniem modelu, a indeks dostaje przeróbkę zamiast oryginału. Dlatego ingestia i komenda
fix_languages ustalają język z treści: wynik tylko przy wyraźnej przewadze, inaczej "".
"""

import re
from collections import Counter

STOPWORDS: dict[str, frozenset[str]] = {
    "pl": frozenset(
        "się że jest nie oraz jak który która które przez dla jego jej także został może było "
        "tym tego jako jednak gdy ich również tylko w z na do od po ze czy są być".split()
    ),
    "en": frozenset(
        "the and of to is that which with for this from are was be by not have as it".split()
    ),
    "de": frozenset(
        "und der die das ist nicht mit von zu den auch sich ein eine dem des im auf".split()
    ),
    "it": frozenset(
        "il della che di e la per non con del una nel sono alla gli dei delle".split()
    ),
    "fr": frozenset(
        "le la les des et est que pour dans une qui pas sur au du ce sont".split()
    ),
    # łacina (dokumenty Kościoła, Ojcowie): słowa, których nie ma w pozostałych zestawach
    "la": frozenset(
        "et sunt quod qui quae quam cum sed enim autem ut etiam atque nec neque eius sicut "
        "ita nisi ergo igitur tamen quia esse hoc haec ab ex".split()
    ),
}
_WORD = re.compile(r"[a-ząćęłńóśźżäöüßàâçèéêëîïôùûÿ]+")
MIN_HITS = 5  # mniej trafień — za mało tekstu, by rozstrzygać
MIN_RATIO = 1.5  # zwycięzca musi wyraźnie wyprzedzać drugi język


def detect(text: str) -> str:
    """Kod języka (pl/en/de/it/fr/la) albo "" przy braku pewności."""
    words = _WORD.findall(text.lower())
    scores = sorted(
        ((sum(w in s for w in words), lang) for lang, s in STOPWORDS.items()),
        reverse=True,
    )
    (best, lang), (second, _) = scores[0], scores[1]
    if best < MIN_HITS or best < MIN_RATIO * max(second, 1):
        return ""
    return lang


def detect_document(
    texts: list[str], max_chunks: int = 40, majority: float = 0.6
) -> str:
    """Język dokumentu: głosowanie fragmentów z całego tekstu (do `max_chunks`, równomiernie).

    Nie pierwsze fragmenty: czasopisma zaczynają artykuł angielskim streszczeniem i słowami
    kluczowymi, więc początek polskiego artykułu wygląda na angielski. Krótkie teksty (< 3
    rozstrzygnięte fragmenty) — po całości."""
    if len(texts) > max_chunks:
        step = len(texts) / max_chunks
        texts = [texts[int(i * step)] for i in range(max_chunks)]
    votes = Counter(lang for lang in (detect(t) for t in texts) if lang)
    total = sum(votes.values())
    if total < 3:
        return detect(" ".join(texts))
    lang, n = votes.most_common(1)[0]
    return lang if n / total >= majority else ""
