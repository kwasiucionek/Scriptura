"""Harvest wykładów z YouTube: metadane + rozdziały (znaczniki czasu z opisu) + napisy (wgrane
lub automatyczne) przez yt-dlp -> tekst z sekcjami czasowymi -> manifest (doc_type=video).

Tekst: „## [mm:ss] Tytuł rozdziału” i akapity po ~PARAGRAPH_SECONDS mowy z nagłówkiem
„[mm:ss]” w treści — chunk niesie znacznik czasu, a panel źródeł linkuje do &t=<s>.
Napisy automatyczne nie mają interpunkcji i bywają błędne (nazwy własne, hebrajski) — to
materiał do wyszukiwania i cytowania „z odcinka”, nie tekst do cytowania dosłownego.
"""

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from corpus.spoken import normalize_spoken_sigla
from library.harvest.manifest import ManifestEntry

PARAGRAPH_SECONDS = 60
_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})")


@dataclass
class Caption:
    start: float
    text: str


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel: str
    upload_date: str  # YYYYMMDD
    description: str
    chapters: list[tuple[float, str]] = field(default_factory=list)  # (start_s, title)
    captions: list[Caption] = field(default_factory=list)
    caption_kind: str = ""  # "napisy" | "napisy automatyczne"
    tags: list[str] = field(default_factory=list)


def video_id(url_or_id: str) -> str:
    m = _ID_RE.search(url_or_id)
    return m.group(1) if m else url_or_id.strip()


def mmss(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_json3(raw: str) -> list[Caption]:
    """Format json3 YouTube: events[{tStartMs, segs:[{utf8}]}] -> lista (start, tekst)."""
    data = json.loads(raw)
    out: list[Caption] = []
    for ev in data.get("events", []):
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if text:
            out.append(Caption(start=ev.get("tStartMs", 0) / 1000.0, text=text))
    return out


def fetch(
    url_or_id: str, langs: tuple[str, ...] = ("pl", "pl-orig", "en")
) -> VideoInfo:
    """Metadane i napisy przez yt-dlp (bez pobierania wideo)."""
    import yt_dlp  # zależność opcjonalna: pip install yt-dlp

    vid = video_id(url_or_id)
    with yt_dlp.YoutubeDL(
        {"quiet": True, "skip_download": True, "no_warnings": True}
    ) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={vid}", download=False
        )
    chapters = [
        (float(c.get("start_time", 0)), c.get("title", "").strip())
        for c in info.get("chapters") or []
    ]
    captions: list[Caption] = []
    kind = ""
    for source, label in (
        (info.get("subtitles") or {}, "napisy"),
        (info.get("automatic_captions") or {}, "napisy automatyczne"),
    ):
        for lang in langs:
            fmts = source.get(lang) or []
            j3 = next((f for f in fmts if f.get("ext") == "json3"), None)
            if j3 and j3.get("url"):
                with urllib.request.urlopen(j3["url"], timeout=60) as resp:  # noqa: S310
                    captions = parse_json3(resp.read().decode("utf-8"))
                kind = label
                break
        if captions:
            break
    return VideoInfo(
        video_id=vid,
        title=info.get("title", vid),
        channel=info.get("channel") or info.get("uploader") or "",
        upload_date=info.get("upload_date") or "",
        description=info.get("description") or "",
        chapters=chapters,
        captions=captions,
        caption_kind=kind,
        tags=[t for t in (info.get("tags") or []) if t],
    )


_TS_LINE = re.compile(r"^\W*\[?\d{1,2}:\d{2}(?::\d{2})?\]?(\(http[^)]*\))?\s*[-–—]?\s*")
_URL_ONLY = re.compile(
    r"^\W*(?:https?://\S+|\[?[\w.-]+\.(?:pl|com|org|net)\]?\(?https?://\S*\)?)\W*$"
)
_SOURCES_HEAD = re.compile(
    r"^\W*(ŹRÓDŁA|Źródła|BIBLIOGRAFIA|Bibliografia|LITERATURA|Literatura)\s*:?\s*$"
)
_PROMO = re.compile(
    r"^\W*(Moja strona|Wesprzyj|Subskrybuj|Kontakt|Zapisz się|Patronite|Facebook|Instagram)",
    re.I,
)
_STOP_HEAD = re.compile(
    r"^\W*(W ODCINKU|W odcinku|Wykładowca|Moja strona|Wesprzyj|Subskrybuj|Kontakt)"
)


def split_description(desc: str) -> tuple[list[str], list[str]]:
    """Opis odcinka bez znaczników czasu i samych linków; osobno pozycje bibliograficzne po „ŹRÓDŁA:”."""
    body: list[str] = []
    sources: list[str] = []
    in_sources = False
    for raw in desc.splitlines():
        line = raw.strip()
        # „W ODCINKU: [00:00](…) - Wstęp” — rozdziały są w chapters; linie promo/kontaktowe pomijamy
        line = re.sub(r"^\W*W ODCINKU\s*:\s*", "", line, flags=re.I)
        if (
            not line
            or _TS_LINE.match(line)
            or _URL_ONLY.match(line)
            or _PROMO.match(line)
        ):
            continue
        line = re.sub(
            r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", line
        )  # markdown-linki z YouTube
        if _SOURCES_HEAD.match(line):
            in_sources = True
            continue
        if in_sources and _STOP_HEAD.match(line):
            in_sources = False
        (sources if in_sources else body).append(line)
    return body, sources


def build_text(v: VideoInfo) -> str:
    """Tekst: tytuł, opis odcinka (temat, słowa kluczowe), źródła z opisu, tagi, potem rozdziały
    z akapitami co PARAGRAPH_SECONDS, każdy z [mm:ss]. Sigla mówione i z opisu dopisywane kanonicznie."""
    lines = [f"# {v.title}", ""]
    body, sources = split_description(v.description or "")
    if body:
        lines += (
            ["## Opis odcinka", ""] + [normalize_spoken_sigla(b) for b in body] + [""]
        )
    if sources:
        lines += ["## Źródła podane w odcinku", ""] + [f"- {x}" for x in sources] + [""]
    if v.tags:
        lines += ["Słowa kluczowe: " + ", ".join(v.tags), ""]
    chapters = sorted(v.chapters) or [(0.0, "")]
    bounds = [c[0] for c in chapters[1:]] + [float("inf")]
    ci = 0
    for start, title in chapters:
        end = bounds[ci]
        ci += 1
        lines.append(
            f"## [{mmss(start)}] {title}".rstrip() if title else f"## [{mmss(start)}]"
        )
        lines.append("")
        chunk_caps = [c for c in v.captions if start <= c.start < end]
        para: list[str] = []
        para_start = start
        for c in chunk_caps:
            if para and c.start - para_start >= PARAGRAPH_SECONDS:
                lines += [
                    f"[{mmss(para_start)}] " + normalize_spoken_sigla(" ".join(para)),
                    "",
                ]
                para = []
            if not para:
                para_start = c.start
            para.append(c.text)
        if para:
            lines += [
                f"[{mmss(para_start)}] " + normalize_spoken_sigla(" ".join(para)),
                "",
            ]
    return "\n".join(lines).strip() + "\n"


def to_entry(
    v: VideoInfo, author: str, out_dir: Path, register: str = "popular"
) -> ManifestEntry:
    dest = out_dir / "youtube" / f"{v.video_id}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(build_text(v), encoding="utf-8")
    year = int(v.upload_date[:4]) if v.upload_date[:4].isdigit() else None
    return ManifestEntry(
        source="youtube",
        source_id=v.video_id,
        title=v.title,
        authors=[author],
        doc_type="video",
        journal=v.channel,
        year=year,
        url=f"https://www.youtube.com/watch?v={v.video_id}",
        license="wszelkie prawa zastrzeżone (wykład autora; wymaga zgody)",
        access="licensed",
        language="pl",
        note=f"{v.caption_kind or 'bez napisów'}; {len(v.chapters)} rozdziałów; nagranie {v.upload_date[:4]}-{v.upload_date[4:6]}-{v.upload_date[6:]}"
        if v.upload_date
        else v.caption_kind,
        register=register,
        abstract=v.description[:1500],
        local_file=str(dest),
    )
