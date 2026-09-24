"""Weryfikacja cytatów w odpowiedzi: czy przytoczony tekst zgadza się z korpusem.

Weryfikacja sigli sprawdza tylko, czy ADRES istnieje; tu sprawdzamy TREŚĆ.
Kandydaci na cytaty:
  1. tekst w cudzysłowach „…” "…" «…» (≥ 15 znaków),
  2. tekst po siglum z opcjonalnym kodem dzieła i dwukropkiem: "Mk 16,9 (SBLGNT): …" /
     "Mk 16,9: …" — do końca zdania, nowej linii lub następnego siglum,
  3. ciąg ≥ 3 słów w piśmie hebrajskim lub greckim, gdziekolwiek w odpowiedzi.
Każdy kandydat jest porównywany (po normalizacji) z wersetami z kontekstu i z chunkami
źródeł: zgodny, gdy jest podciągiem albo podobieństwo ≥ 0.92; „zmieniony", gdy
0.65 ≤ podobieństwo < 0.92 (blisko oryginału, ale nie ten sam tekst); poniżej — to nie
cytat z korpusu (np. własne sformułowanie modelu w cudzysłowie) i nie jest raportowany.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from corpus.normalize import normalize
from corpus.sigla import _EXTRACT_RE  # ten sam wzorzec sigli co w ekstrakcji

OK_THRESHOLD = 0.92
ALTERED_THRESHOLD = 0.65
MIN_QUOTE_CHARS = 15

_QUOTED_RE = re.compile(r"[„\"«]([^„\"”«»]{15,600})[”\"»]")
# ciąg ≥3 słów w piśmie hebrajskim/greckim — cytat w oryginale niezależnie od otoczenia
_ORIG_RUN_RE = re.compile(
    r"(?:[\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff][\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff\u2019’']*[\s,;·.:]+){2,}"
    r"[\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff][\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff\u2019’']*"
)
# wzorzec siglum bez nazwanych grup (używany dwukrotnie w jednym wyrażeniu)
_SIG = _EXTRACT_RE.pattern.replace("(?P<book>", "(?:").replace("(?P<tail>", "(?:")
_WORK = r"(?:\([A-Z0-9]{2,12}\)|[A-Z0-9]{3,12})?"
_SIGLUM_QUOTE_RE = re.compile(
    rf"(?P<ref>{_SIG})\s*{_WORK}\s*:\s*(?P<text>[^\n]+?)(?=\s+(?:{_SIG})\s*{_WORK}\s*:|\n|$)",
)


@dataclass
class QuoteCheck:
    quote: str
    status: str  # "ok" | "altered"
    ref: str = ""  # najbliższy werset / dokument
    ratio: float = 0.0


@dataclass
class QuoteReport:
    tradition: int = 0  # cytaty z Ojców/ANE (przekład modelu) — pominięte w weryfikacji
    verified: int = 0
    altered: list[QuoteCheck] = field(default_factory=list)
    checked: int = 0


def _guess_lang(text: str) -> str:
    if re.search(r"[\u0590-\u05ff]", text):
        return "hbo"
    if re.search(r"[\u0370-\u03ff\u1f00-\u1fff]", text):
        return "grc"
    return "pl"


def _norm(text: str) -> str:
    return normalize(text, _guess_lang(text))


def extract_candidates(answer: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _QUOTED_RE.finditer(answer):
        q = m.group(1).strip()
        if q not in seen:
            seen.add(q)
            out.append(q)
    for m in _SIGLUM_QUOTE_RE.finditer(answer):
        q = m.group("text").strip().rstrip(".;")
        if len(q) >= MIN_QUOTE_CHARS and q not in seen:
            seen.add(q)
            out.append(q)
    for m in _ORIG_RUN_RE.finditer(answer):
        q = m.group(0).strip().rstrip(".;:,")
        if len(q) >= MIN_QUOTE_CHARS and not any(q in x for x in seen):
            seen.add(q)
            out.append(q)
    return out


_TRAD_TAG_RE = re.compile(r"\[[PA]\d+(?:\s*[,–-]\s*[PA]?\d+)*\]")


def _from_tradition(answer: str, quote: str) -> bool:
    """Czy cytat stoi w zdaniu opatrzonym odsyłaczem [Pn]/[An] (Ojcowie / ANE)."""
    i = answer.find(quote)
    if i < 0:
        return False
    start = max(answer.rfind(".", 0, i), answer.rfind("\n", 0, i)) + 1
    end_dot = answer.find(".", i + len(quote))
    end = (
        len(answer) if end_dot < 0 else min(end_dot + 40, len(answer))
    )  # [P1] bywa po kropce
    return bool(_TRAD_TAG_RE.search(answer[start:end]))


def verify_quotes(
    answer: str, verses: list[tuple[str, str]], chunks: list[tuple[str, str]]
) -> QuoteReport:
    """verses: [(ref, text)], chunks: [(label, text)] -> raport."""
    report = QuoteReport()
    sources = [(ref, _norm(text)) for ref, text in verses] + [
        (lbl, _norm(text)) for lbl, text in chunks
    ]
    for quote in extract_candidates(answer):
        nq = _norm(quote)
        if len(nq) < MIN_QUOTE_CHARS:
            continue
        if _from_tradition(
            answer, quote
        ):  # cytat przy [P…]/[A…] to przekład modelu z angielskiego — nie weryfikujemy
            report.tradition += 1
            continue
        best_ratio, best_ref = 0.0, ""
        for ref, ns in sources:
            if nq in ns:
                best_ratio, best_ref = 1.0, ref
                break
            # porównuj z oknem podobnej długości, żeby długi chunk nie zaniżał wyniku
            window = ns if len(ns) <= 2 * len(nq) else _best_window(nq, ns)
            r = SequenceMatcher(None, nq, window).ratio()
            if r > best_ratio:
                best_ratio, best_ref = r, ref
        if best_ratio >= OK_THRESHOLD:
            report.verified += 1
            report.checked += 1
        elif best_ratio >= ALTERED_THRESHOLD:
            report.altered.append(
                QuoteCheck(quote[:160], "altered", best_ref, round(best_ratio, 2))
            )
            report.checked += 1
        # poniżej progu: własne sformułowanie modelu w cudzysłowie, nie cytat z korpusu
    return report


def _best_window(needle: str, hay: str) -> str:
    """Fragment `hay` o długości ~needle najbardziej podobny do needle (krok = 1/4 długości)."""
    n = len(needle)
    step = max(n // 4, 8)
    best, best_r = hay[:n], 0.0
    for i in range(0, max(len(hay) - n, 0) + 1, step):
        w = hay[i : i + n + n // 4]
        r = SequenceMatcher(None, needle, w).ratio()
        if r > best_r:
            best, best_r = w, r
    return best
