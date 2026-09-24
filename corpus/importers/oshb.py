"""Import Open Scriptures Hebrew Bible (WLC + morfologia, OSIS XML, CC BY 4.0).

Źródło: https://github.com/openscriptures/morphhb (katalog wlc/*.xml)
Format słowa: <w lemma="b/7225" morph="HR/Ncfsa">בְּ/רֵאשִׁ֖ית</w>
  lemma  = [prefiksy/]numer_Stronga[ litera_homografu]
  morph  = kod OSHM (H + kategorie, "/" oddziela morfemy)
Ketiv/qere: bierzemy tylko bezpośrednie <w> wersetu (ketiv); qere siedzi w <note>.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

from corpus.books import BOOKS
from corpus.importers.base import TokenRecord, VerseRecord

NS = {"osis": "http://www.bibletechnologies.net/2003/OSIS/namespace"}
_LEMMA_RE = re.compile(
    r"^(?P<prefix>(?:[a-z]/)*)(?P<num>\d+)(?:\s*(?P<homograph>[a-z]))?$"
)


def _parse_lemma(raw: str) -> tuple[str, str]:
    """'b/7225' -> ('H7225', 'b'); '1254 a' -> ('H1254a', '')."""
    m = _LEMMA_RE.match(raw.strip())
    if not m:
        return "", ""
    strong = f"H{m.group('num')}{m.group('homograph') or ''}"
    prefixes = m.group("prefix").replace("/", "")
    return strong, prefixes


def parse_file(path: Path, osis: str) -> Iterator[VerseRecord]:
    root = ET.parse(path).getroot()
    for verse_el in root.iter(f"{{{NS['osis']}}}verse"):
        osis_id = verse_el.get("osisID", "")
        try:
            _, ch, v = osis_id.split(".")
        except ValueError:
            continue
        tokens: list[TokenRecord] = []
        for w in verse_el.findall("osis:w", NS):
            surface = (w.text or "").strip()
            if not surface:
                continue
            strong, prefixes = _parse_lemma(w.get("lemma", ""))
            tokens.append(
                TokenRecord(
                    surface=surface.replace("/", ""),
                    strong=strong,
                    morph=w.get("morph", ""),
                    prefixes=prefixes,
                )
            )
        text = " ".join(t.surface for t in tokens)
        yield VerseRecord(
            osis=osis, chapter=int(ch), verse=int(v), text=text, tokens=tokens
        )


def parse_dir(wlc_dir: Path, only: set[str] | None = None) -> Iterator[VerseRecord]:
    for spec in BOOKS:
        if not spec.oshb_file or (only and spec.osis not in only):
            continue
        path = wlc_dir / f"{spec.oshb_file}.xml"
        if not path.exists():
            continue
        yield from parse_file(path, spec.osis)
