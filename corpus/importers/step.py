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


def clean_meaning(text: str, limit: int = 600) -> str:
    text = html.unescape(
        _TAG_RE.sub(
            " ",
            text.replace("<br>", "; ").replace("<BR>", "; ").replace("<BR />", "; "),
        )
    )
    text = re.sub(r"\s+", " ", text).strip(" ;")
    return text[:limit]


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
        }
