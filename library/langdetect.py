"""Język tekstu po słowach funkcyjnych — bez zewnętrznych zależności.

Metadane (OpenAlex) zgadują język po tytule, więc polska recenzja włoskiej książki bywa „it”,
a stary artykuł po OCR — „de”. Przekład „z niemieckiego” tekstu, który jest polski, kończy się
zapętleniem modelu, a indeks dostaje przeróbkę zamiast oryginału. Dlatego ingestia i komenda
fix_languages ustalają język z treści: wynik tylko przy wyraźnej przewadze, inaczej "".
"""

import re

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
}
_WORD = re.compile(r"[a-ząćęłńóśźżäöüßàâçèéêëîïôùûÿ]+")
MIN_HITS = 5  # mniej trafień — za mało tekstu, by rozstrzygać
MIN_RATIO = 1.5  # zwycięzca musi wyraźnie wyprzedzać drugi język


def detect(text: str) -> str:
    """Kod języka (pl/en/de/it/fr) albo "" przy braku pewności."""
    words = _WORD.findall(text.lower())
    scores = sorted(
        ((sum(w in s for w in words), lang) for lang, s in STOPWORDS.items()),
        reverse=True,
    )
    (best, lang), (second, _) = scores[0], scores[1]
    if best < MIN_HITS or best < MIN_RATIO * max(second, 1):
        return ""
    return lang


def detect_document(texts: list[str], sample: int = 4) -> str:
    """Język dokumentu z pierwszych `sample` fragmentów."""
    return detect(" ".join(texts[:sample]))
