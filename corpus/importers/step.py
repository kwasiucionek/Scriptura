"""Import leksykonów STEPBible (Tyndale House, CC BY 4.0): TBESH (hebrajski/aramejski), TBESG (grecki).

Pliki TSV z długim nagłówkiem; wiersze danych: eStrong, dStrong, uStrong, forma, transliteracja,
morph, glosa, znaczenie (HTML). Bierzemy pierwszy wiersz na każdy eStrong (główne znaczenie);
warianty dStrong (H0001H, H0001I — osoby o tym samym imieniu) pomijamy.
Normalizacja Strong: H0001 -> H1, H1254a -> H1254a (zgodnie z OSHB: numer + litera homografu).
"""

import html
import re
from collections.abc import Iterator
from pathlib import Path

from corpus.normalize import normalize
from corpus.translit import canonical_translit

_STRONG_RE = re.compile(r"^([HG])0*(\d+)([a-z]?)$")
_TAG_RE = re.compile(r"<[^>]+>")


def normalize_strong(raw: str) -> str:
    m = _STRONG_RE.match(raw.strip())
    if not m:
        return raw.strip()
    return f"{m.group(1)}{int(m.group(2))}{m.group(3)}"


def clean_meaning(text: str, limit: int | None = 600) -> str:
    text = html.unescape(
        _TAG_RE.sub(
            " ",
            text.replace("<br>", "; ").replace("<BR>", "; ").replace("<BR />", "; "),
        )
    )
    text = re.sub(r"\s+", " ", text).strip(" ;")
    return text[:limit]


_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_REF_RE = re.compile(r"<ref='([^']*)'>(.*?)</ref>", re.IGNORECASE | re.DOTALL)
_KEEP_TAG_RE = re.compile(r"(</?[bi]>|\[\[[^\]]*\]\])", re.IGNORECASE)


def clean_definition(text: str) -> str:
    """Pełna definicja STEP do strony słownika: podziały wierszy zamiast <br>, pogrubienia
    i kursywy zostają (<b>, <i>), odsyłacze <ref='Php.2.6'>…</ref> -> [[Php.2.6|…]] (link do
    tekstu buduje szablon), pozostałe znaczniki i encje HTML znikają. Znaczniki „__1.” STEP
    (wcięcie punktu) -> sam numer."""
    text = _BR_RE.sub("\n", text)
    text = _REF_RE.sub(
        lambda m: f"[[{m.group(1).strip()}|{_TAG_RE.sub('', m.group(2)).strip()}]]",
        text,
    )
    parts = _KEEP_TAG_RE.split(text)
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part.lower() if part.startswith("<") else part)
        else:
            out.append(html.unescape(_TAG_RE.sub(" ", part)))
    text = "".join(out).replace("__", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip(" ;:\n")


def parse_file(path: Path) -> Iterator[dict]:
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if (
            not line
            or line.startswith(("#", "\t", "="))
            or not re.match(r"^[HG]\d", line)
        ):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        strong = normalize_strong(parts[0])
        if strong in seen:
            continue
        seen.add(strong)
        morph = parts[5].strip()
        lang = (
            "grc"
            if strong.startswith("G")
            else ("arc" if morph.startswith("A:") else "hbo")
        )
        yield {
            "strong": strong,
            "language": lang,
            "lemma": parts[3].strip()[:64],
            "lemma_norm": normalize(parts[3].strip(), lang)[:64],
            "transliteration": parts[4].strip()[:64],
            "translit_fold": canonical_translit(parts[4])[:64],
            "morph": morph[:24],
            "gloss": clean_meaning(parts[6], 200),
            "meaning": clean_meaning(parts[7]) if len(parts) > 7 else "",
            "definition": clean_definition(parts[7]) if len(parts) > 7 else "",
        }


# --- Strong dla tokenów SBLGNT (MorphGNT podaje tylko lemat) ---------------------------------

_MIDDLE_ENDINGS = [
    ("εομαι", "εω"), ("αομαι", "αω"), ("οομαι", "οω"), ("ομαι", "ω"),
    ("ιθεμαι", "ιθημι"), ("ισταμαι", "ιστημι"), ("ιεμαι", "ιημι"), ("οδομαι", "ιδωμι"),
    ("υμαι", "υμι"), ("αμαι", "ημι"), ("εμαι", "ημι"),
]  # fmt: skip
STANDARD_GREEK_MAX = 5624  # numery Stronga; wyższe to rozszerzenia STEP (głównie LXX)


def _strong_rank(strong: str) -> tuple[int, int, str]:
    m = _STRONG_RE.match(strong)
    num = int(m.group(2)) if m else 10**6
    return (num > STANDARD_GREEK_MAX, num, strong)


class GreekStrongIndex:
    """Lemat MorphGNT -> numer Stronga: najpierw dokładny lemat (z akcentami — rozróżnia
    πῶς/πως), potem postać znormalizowana, potem forma czynna lematu medialnego
    (καταποντίζομαι -> καταποντίζω). Przy kilku kandydatach wygrywa numer klasyczny (≤ 5624)
    i najniższy."""

    def __init__(self, rows):
        import unicodedata

        self._nfc = lambda s: unicodedata.normalize("NFC", s)
        self.exact: dict[str, list[str]] = {}
        self.norm: dict[str, list[str]] = {}
        for strong, lemma, lemma_norm in rows:
            self.exact.setdefault(self._nfc(lemma), []).append(strong)
            self.norm.setdefault(lemma_norm, []).append(strong)

    @staticmethod
    def _best(cands: list[str]) -> str:
        return min(cands, key=_strong_rank)

    def lookup(self, lemma: str, lemma_norm: str) -> str:
        if cands := self.exact.get(self._nfc(lemma)):
            return self._best(cands)
        if cands := self.norm.get(lemma_norm):
            return self._best(cands)
        for end, repl in _MIDDLE_ENDINGS:
            if lemma_norm.endswith(end):
                if cands := self.norm.get(lemma_norm[: -len(end)] + repl):
                    return self._best(cands)
                break
        return ""
