"""Import z eBL (electronic Babylonian Library, LMU): API /api/texts/{genre}/{category}/{index}
oraz /chapters/{stage}/{name}. Linia: number, variants[0].reconstruction (normalizacja z markerami
%n | ||), translation ("#tr.en: …", czasem poprzedzone liniami "// cf. …").
"""

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from django.db import transaction

from ane.models import AneChapter, AneLine, AnePassage, AneText

EBL_API = "https://www.ebl.lmu.de/api"
STAGE_ABBR = {
    "Standard Babylonian": "SB",
    "Old Babylonian": "OB",
    "Middle Babylonian": "MB",
    "Neo-Assyrian": "NA",
}
_TR_RE = re.compile(r"^#tr\.en:\s*(.*)$", re.M)


@dataclass
class LineDraft:
    number: str
    normalized: str
    translation_en: str
    note: str = ""


@dataclass
class ChapterDraft:
    stage: str
    name: str
    title: str = ""
    lines: list[LineDraft] = field(default_factory=list)


def clean_reconstruction(text: str) -> str:
    text = text.replace("%n", " ").replace("||", " ").replace("|", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_translation(raw) -> tuple[str, str]:
    """('przekład EN', 'notatki cf.') z pola translation (string albo lista)."""
    if isinstance(raw, list):
        raw = "\n".join(str(x) for x in raw)
    raw = str(raw or "")
    notes = [
        ln.strip("/ ").strip() for ln in raw.splitlines() if ln.strip().startswith("//")
    ]
    m = _TR_RE.findall(raw)
    return (" ".join(x.strip() for x in m).strip(), "; ".join(notes)[:300])


def parse_chapter(data: dict) -> ChapterDraft:
    lines = []
    for i, ln in enumerate(data.get("lines", [])):
        variants = ln.get("variants") or [{}]
        rec = clean_reconstruction(variants[0].get("reconstruction", "") or "")
        tr, note = parse_translation(ln.get("translation", ""))
        if not tr and not rec:
            continue
        lines.append(
            LineDraft(
                number=str(ln.get("number", i + 1)),
                normalized=rec,
                translation_en=tr,
                note=note,
            )
        )
    title = " ".join(
        p.get("text", "") for p in data.get("title", []) if isinstance(p, dict)
    ).strip()
    return ChapterDraft(
        stage=data.get("stage", ""),
        name=str(data.get("name", "")),
        title=title,
        lines=lines,
    )


def _get(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "scriptura-ane/0.1"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        return json.load(resp)


def fetch_text(genre: str, category: int, index: int) -> dict:
    return _get(f"{EBL_API}/texts/{genre}/{category}/{index}")


def fetch_chapter(genre: str, category: int, index: int, stage: str, name: str) -> dict:
    return _get(
        f"{EBL_API}/texts/{genre}/{category}/{index}/chapters/{urllib.parse.quote(stage)}/{urllib.parse.quote(name)}"
    )


@transaction.atomic
def save_text(
    meta: dict,
    chapters: list[ChapterDraft],
    source_id: str,
    name_pl: str = "",
    license: str = "CC BY-NC-SA 4.0 (eBL, LMU München)",
) -> AneText:
    text, _ = AneText.objects.update_or_create(
        source="ebl",
        source_id=source_id,
        defaults={
            "name": meta.get("name", source_id),
            "name_pl": name_pl,
            "intro": (meta.get("intro") or "")[:5000],
            "license": license,
            "url": f"https://www.ebl.lmu.de/library/{source_id.replace('/', '/')}",
        },
    )
    for order, ch in enumerate(chapters):
        chapter, _ = AneChapter.objects.update_or_create(
            text=text,
            stage=ch.stage,
            name=ch.name,
            defaults={"order": order, "title": ch.title[:300]},
        )
        chapter.lines.all().delete()
        AneLine.objects.bulk_create(
            [
                AneLine(
                    chapter=chapter,
                    order=i,
                    number=ln.number,
                    normalized=ln.normalized,
                    translation_en=ln.translation_en,
                    note=ln.note,
                )
                for i, ln in enumerate(ch.lines)
            ]
        )
        build_passages(chapter)
    return text


def build_passages(chapter: AneChapter, size: int = 12) -> int:
    """Pasaże po ~12 linii z przekładem (linie bez przekładu są pomijane w tekście, ale nie łamią pasażu)."""
    chapter.passages.all().delete()
    lines = list(chapter.lines.order_by("order"))
    passages = []
    buf: list[AneLine] = []
    for ln in lines:
        buf.append(ln)
        if len(buf) >= size:
            passages.append(buf)
            buf = []
    if buf:
        if passages and len(buf) < 4:
            passages[-1].extend(buf)
        else:
            passages.append(buf)
    objs = []
    for order, group in enumerate(passages):
        tr = " ".join(
            f"({ln.number}) {ln.translation_en}" for ln in group if ln.translation_en
        )
        if not tr.strip():
            continue
        objs.append(
            AnePassage(
                chapter=chapter,
                order=order,
                line_start=group[0].number,
                line_end=group[-1].number,
                normalized=" ".join(ln.normalized for ln in group if ln.normalized),
                translation_en=tr,
            )
        )
    AnePassage.objects.bulk_create(objs)
    return len(objs)
