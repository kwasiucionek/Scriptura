"""Normalizacja form wyrazowych do kolumny `surface_norm` (indeks leksykalny).

- greka: bez akcentów, przydechów, iota subscriptum; sigma końcowa -> σ; lowercase
- hebrajski: bez nikud, akcentów (te'amim) i znaków kantylacji; bez maqaf;
  usunięty separator morfemów "/" z OSHB
- polski: lowercase, bez interpunkcji, NFC

Wyszukiwarka użytkownika działa na tej samej funkcji, więc zapytanie
"πατηρ" trafi w "πατήρ", a "אלהים" w "אֱלֹהִ֑ים".
"""

import re
import unicodedata

_COMBINING_RE = re.compile(r"[\u0300-\u036f\u0591-\u05c7\u1dc0-\u1dff]")
_HEBREW_PUNCT_RE = re.compile(
    r"[\u05be\u05c0\u05c3\u05c6\u05f3\u05f4׃]"
)  # maqaf, paseq, sof pasuq...
_POLISH_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_greek(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    stripped = _COMBINING_RE.sub("", decomposed)
    return unicodedata.normalize("NFC", stripped).replace("ς", "σ").lower().strip()


def normalize_hebrew(text: str) -> str:
    text = text.replace("/", "")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = _COMBINING_RE.sub("", decomposed)
    stripped = _HEBREW_PUNCT_RE.sub("", stripped)
    return unicodedata.normalize("NFC", stripped).strip()


def normalize_polish(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    return " ".join(_POLISH_PUNCT_RE.sub(" ", text).split())


def normalize(text: str, language: str) -> str:
    """Wybór normalizacji po kodzie języka: 'grc', 'hbo', 'pl' (domyślnie pl)."""
    if language == "grc":
        return normalize_greek(text)
    if language == "hbo":
        return normalize_hebrew(text)
    return normalize_polish(text)
