"""Weryfikacja treści cytatów i przypisów względem źródeł dostarczonych modelowi.

Przypis wiąże cytat z konkretnym źródłem, nie z całym kontekstem. Nieznalezione
cytaty (także przekłady modelu z Ojców/ANE) wymagają jawnej kontroli człowieka.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from corpus.normalize import normalize
from corpus.sigla import _EXTRACT_RE

OK_THRESHOLD = 0.92
ALTERED_THRESHOLD = 0.65
MIN_QUOTE_CHARS = 15

_QUOTED_RE = re.compile(r"[„\"«“]([^„\"”«»“]{1,600})[”\"»]")
_ORIG_RUN_RE = re.compile(
    r"(?:[\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff][\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff\u2019’']*[\s,;·.:]+){2,}"
    r"[\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff][\u0590-\u05ff\u0370-\u03ff\u1f00-\u1fff\u2019’']*"
)
_SIG = _EXTRACT_RE.pattern.replace("(?P<book>", "(?:").replace("(?P<tail>", "(?:")
_WORK = r"(?:\([A-Z0-9]{2,12}\)|[A-Z0-9]{3,12})?"
_SIGLUM_QUOTE_RE = re.compile(
    rf"(?P<ref>{_SIG})\s*{_WORK}\s*:\s*(?P<text>[^\n]+?)(?=\s+(?:{_SIG})\s*{_WORK}\s*:|\n|$)",
)
_CITATION_RE = re.compile(r"\[(?P<body>\s*(?:[PA]\s*\d*|\d+)[^\[\]\n]*)\]")
_CITATION_PART_RE = re.compile(r"([PA]?)(\d{1,9})(?:\s*[-–—]\s*([PA]?)(\d{1,9}))?")
_MAX_CITATION_RANGE = 200


@dataclass
class CitationCheck:
    marker: str
    refs: list[str]
    status: str  # ok | unverified (existence only, not support for a claim)
    missing: list[str] = field(default_factory=list)
    reason: str = ""
    start: int = 0
    end: int = 0


def check_citations(answer: str, available: set[str]) -> list[CitationCheck]:
    """Expand lists/ranges, retaining invalid markers instead of silently dropping them.

    Labels are canonical: [1], [P1], [A1]. Prefix inheritance supports [P1, 2–3].
    Cross-family, descending and oversized ranges are explicitly unverified.
    """
    out = []
    for match in _CITATION_RE.finditer(answer):
        refs, prefix, reason = [], "", ""
        for part in re.split(r"\s*[,;]\s*", match.group("body").strip()):
            item = _CITATION_PART_RE.fullmatch(part.strip())
            if not item:
                reason = "invalid_marker"
                break
            p, first, p_end, last = item.groups()
            prefix = p or prefix
            a, b = int(first), int(last or first)
            if last and p_end and p_end != prefix:
                reason = "cross_family_range"
                break
            if b < a or b - a + 1 > _MAX_CITATION_RANGE:
                reason = "invalid_range"
                break
            refs.extend(f"[{prefix}{n}]" for n in range(a, b + 1))
        refs = list(dict.fromkeys(refs))
        missing = [ref for ref in refs if ref not in available]
        out.append(
            CitationCheck(
                match.group(),
                refs,
                "unverified" if reason or missing else "ok",
                missing,
                reason or ("missing_source" if missing else ""),
                match.start(),
                match.end(),
            )
        )
    return out


@dataclass
class QuoteCheck:
    quote: str
    status: str  # ok | altered | unverified
    ref: str = ""
    ratio: float = 0.0
    citations: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class QuoteReport:
    tradition: int = 0  # cytaty opatrzone [P…]/[A…]; NIE oznacza potwierdzenia
    verified: int = 0
    altered: list[QuoteCheck] = field(default_factory=list)
    checked: int = 0
    unverified: list[QuoteCheck] = field(default_factory=list)


def _guess_lang(text: str) -> str:
    if re.search(r"[\u0590-\u05ff]", text):
        return "hbo"
    if re.search(r"[\u0370-\u03ff\u1f00-\u1fff]", text):
        return "grc"
    return "pl"


def _norm(text: str) -> str:
    return normalize(text, _guess_lang(text))


def _candidates(answer: str) -> list[tuple[str, int, int]]:
    # Keep positions: identical wording can be correctly cited once and misattributed later.
    out = [
        (m.group(1).strip(), m.start(), m.end()) for m in _QUOTED_RE.finditer(answer)
    ]
    for pattern, group in ((_SIGLUM_QUOTE_RE, "text"), (_ORIG_RUN_RE, 0)):
        for m in pattern.finditer(answer):
            start, end = m.span(group)
            q = m.group(group).strip().rstrip(".;:,")
            if group == "text":
                marker = _CITATION_RE.search(q)
                if marker:
                    q = q[: marker.start()].rstrip(" .;:,")
                    end = start + len(q)
            if len(q) >= MIN_QUOTE_CHARS and not any(
                start < e and end > s for _, s, e in out
            ):
                out.append((q, start, end))
    return sorted(out, key=lambda c: c[1])


def extract_candidates(answer: str) -> list[str]:
    """Public compatibility API: unique candidate strings, as before."""
    return list(
        dict.fromkeys(q for q, _, _ in _candidates(answer) if len(q) >= MIN_QUOTE_CHARS)
    )


def _quote_citations(answer, start, end, citations, next_start):
    # A marker immediately after a quote may follow the closing sentence punctuation.
    suffix = answer[end:next_start]
    adjacent = re.match(r"[\s”\"».,;:()]*((?:\[[^\]\n]+\][\s,;]*)+)", suffix)
    if adjacent:
        stop = end + adjacent.end()
        return [c for c in citations if end <= c.start < stop]
    # Otherwise only this sentence, never a marker from the following sentence/quote.
    stop = end + len(re.split(r"[.!?\n]", suffix, maxsplit=1)[0])
    following = [c for c in citations if end <= c.start < stop]
    if following:
        return following
    begin = (
        max(
            answer.rfind(".", 0, start),
            answer.rfind("\n", 0, start),
            answer.rfind("”", 0, start),
            answer.rfind('"', 0, start),
        )
        + 1
    )
    preceding = [c for c in citations if begin <= c.start and c.end <= start]
    return preceding[-1:]


def _similarity(quote: str, sources: list[tuple[str, str]]) -> tuple[float, str]:
    best_ratio, best_ref = 0.0, ""
    for ref, text in sources:
        if quote in text:
            return 1.0, ref
        window = text if len(text) <= 2 * len(quote) else _best_window(quote, text)
        ratio = SequenceMatcher(None, quote, window).ratio()
        if ratio > best_ratio:
            best_ratio, best_ref = ratio, ref
    return best_ratio, best_ref


def verify_quotes(
    answer: str,
    verses: list[tuple[str, str]],
    chunks: list[tuple[str, str]],
    *,
    tradition: list[tuple[str, str]] | None = None,
) -> QuoteReport:
    """Source labels must start with [n]/[Pn]/[An]; compare only the cited sources.

    Without a footnote compare against the supplied context. With several footnotes
    every referenced source must support the quote. Translations are not verified
    automatically against English originals.
    """
    report = QuoteReport()
    sources = [
        (ref, _norm(text)) for ref, text in [*verses, *chunks, *(tradition or [])]
    ]
    by_marker: dict[str, list[tuple[str, str]]] = {}
    for ref, text in sources:
        marker = re.match(r"\[(?:[PA])?\d+\]", ref)
        if marker:
            by_marker.setdefault(marker.group(), []).append((ref, text))
    citations = check_citations(answer, set(by_marker))
    candidates = _candidates(answer)
    attached_citations = [
        _quote_citations(
            answer,
            start,
            end,
            citations,
            candidates[i + 1][1] if i + 1 < len(candidates) else len(answer),
        )
        for i, (_, start, end) in enumerate(candidates)
    ]
    # A trailing footnote can apply to several quotes in one sentence. Preserve
    # individual attachments; ignore punctuation inside the quotes, but never
    # inherit across a sentence/line boundary in the prose between them.
    for i in range(len(candidates) - 2, -1, -1):
        if attached_citations[i]:
            continue
        end = candidates[i][2]
        next_start, next_end = candidates[i + 1][1:]
        if not re.search(r"[.!?\n]", answer[end:next_start]):
            attached_citations[i] = [
                c for c in attached_citations[i + 1] if c.start >= next_end
            ]
    for (quote, _start, _end), attached in zip(
        candidates, attached_citations, strict=True
    ):
        markers = list(dict.fromkeys(ref for c in attached for ref in c.refs))
        is_tradition = any(re.match(r"\[\s*[PA]", c.marker) for c in attached)
        nq = _norm(quote)
        if len(nq) < MIN_QUOTE_CHARS and not attached:
            continue
        report.checked += 1
        report.tradition += int(is_tradition)
        reason = ""
        if not nq:
            ratio, ref, reason = 0.0, "", "no_matching_source"
        elif any(c.status != "ok" for c in attached):
            ratio, ref, reason = 0.0, "", "invalid_citation"
        elif markers:
            ratio, ref = min(
                (_similarity(nq, by_marker[m]) for m in markers),
                key=lambda pair: pair[0],
            )
        else:
            ratio, ref = _similarity(nq, sources)
        status = (
            "ok"
            if ratio >= OK_THRESHOLD
            else "altered"
            if ratio >= ALTERED_THRESHOLD
            else "unverified"
        )
        if status == "ok":
            report.verified += 1
        else:
            reason = reason or (
                "translation_or_mismatch"
                if is_tradition
                else "source_mismatch"
                if markers
                else "no_matching_source"
            )
            check = QuoteCheck(
                quote[:160], status, ref, round(ratio, 2), markers, reason
            )
            (report.altered if status == "altered" else report.unverified).append(check)
    return report


def _best_window(needle: str, hay: str) -> str:
    """Fragment `hay` o długości ~needle najbardziej podobny do needle."""
    n = len(needle)
    step = max(n // 4, 8)
    best, best_r = hay[:n], 0.0
    for i in range(0, max(len(hay) - n, 0) + 1, step):
        w = hay[i : i + n + n // 4]
        r = SequenceMatcher(None, needle, w).ratio()
        if r > best_r:
            best, best_r = w, r
    return best
