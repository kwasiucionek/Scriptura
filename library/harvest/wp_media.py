"""Harvest publikacji udostępnionych przez autora na jego stronie WordPress: pliki PDF z
biblioteki mediów (REST /wp/v2/media) z tytułem, kategorią i opisem z własnego typu wpisu
(np. „publikacja” na majewskimarcin.pl), gdy strona go ma.

Biblioteka mediów zawiera też pliki administracyjne (umowy, ubezpieczenia, programy wyjazdów,
wnioski wizowe) — odrzuca je filtr nazw (`DEFAULT_EXCLUDE` + własny), a ich nie pobieramy wcale.
Wynik to manifest do przejrzenia (rejestr, pominięcia), potem `ingest_manifest`.
Używać tylko za zgodą autora: pliki ze strony nie mają licencji CC.
"""

import html
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass

from library.harvest.manifest import ManifestEntry

USER_AGENT = "Scriptura/0.1 (https://scriptura.cytr.us)"
# pliki niebędące publikacjami: dokumenty wyjazdów, umowy, ubezpieczenia, instrukcje, reklamy
DEFAULT_EXCLUDE = (
    r"umowa|ubezpiecz|wniosek|eta-il|ziemia.?swieta|turcja|informacje.?praktyczne|"
    r"instrukcja|agencj|conference|konferencj|dorobek|complete.?list|faktur|regulamin"
)
# kategoria publikacji (slug) -> (doc_type, rejestr)
CATEGORIES = {
    "ksiazki": ("book", "scientific"),
    "skrypty": ("script", "mixed"),
    "artykuly": ("article", "scientific"),
    "recenzje": ("review", "scientific"),
    "wywiady": ("article", "popular"),
    "biblia-krok-po-kroku": ("script", "popular"),
}
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


@dataclass
class Publication:
    post_id: int
    title: str
    link: str
    category: str  # slug kategorii, np. „artykuly”
    description: str  # opis ze strony (bez HTML)


def _get(url: str, timeout: int = 30) -> tuple[list | dict, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — adres strony autora
        return json.load(resp), dict(resp.headers)


def _paged(base: str, params: dict, delay: float = 0.5) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        data, headers = _get(
            f"{base}?{urllib.parse.urlencode({**params, 'page': page})}"
        )
        out.extend(data)
        pages = int(
            headers.get("X-WP-TotalPages") or headers.get("x-wp-totalpages") or 1
        )
        if page >= pages or not data:
            return out
        page += 1
        time.sleep(delay)


def _text(rendered: str) -> str:
    t = re.sub(r"<[^>]+>", " ", rendered or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def fold(text: str) -> str:
    """Tytuł/nazwa pliku do porównań: bez diakrytyków i znaków spoza liter i cyfr."""
    t = unicodedata.normalize("NFKD", text or "").replace("ł", "l").replace("Ł", "L")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def list_media(site: str) -> list[dict]:
    """Pliki PDF z biblioteki mediów: id, tytuł, adres pliku."""
    rows = _paged(
        f"{site.rstrip('/')}/wp-json/wp/v2/media",
        {
            "mime_type": "application/pdf",
            "per_page": 100,
            "_fields": "id,title,source_url,date",
        },
    )
    return [
        {
            "id": r["id"],
            "title": html.unescape(r["title"]["rendered"]),
            "url": r["source_url"],
            "date": r.get("date", ""),
        }
        for r in rows
    ]


def list_publications(site: str, post_type: str = "publikacja") -> list[Publication]:
    rows = _paged(
        f"{site.rstrip('/')}/wp-json/wp/v2/{post_type}",
        {"per_page": 100, "_fields": "id,title,link,content,class_list"},
    )
    out = []
    for r in rows:
        cats = [
            c.split("kategoria-publikacji-", 1)[1]
            for c in r.get("class_list") or []
            if c.startswith("kategoria-publikacji-")
        ]
        out.append(
            Publication(
                post_id=r["id"],
                title=html.unescape(r["title"]["rendered"]).strip(),
                link=r["link"],
                category=cats[0] if cats else "",
                description=_text(r["content"]["rendered"])[:1500],
            )
        )
    return out


def candidates(media: dict, publications: list[Publication]) -> list[Publication]:
    """Publikacje pasujące do pliku: nazwa pliku (bez rozszerzenia i „ (1)”) albo tytuł pliku
    jest początkiem tytułu publikacji (nazwy plików są ucinane do ~40 znaków). Kilka części
    tego samego tytułu (cz. 1, cz. 2) daje kilku kandydatów — w kolejności tytułów."""
    name = fold(media["url"].rsplit("/", 1)[-1].rsplit(".", 1)[0])
    keys = {
        re.sub(r"\s\d$", "", name)[:36],
        re.sub(r"\s\d$", "", fold(media["title"]))[:36],
    }
    found: dict[int, tuple[int, Publication]] = {}
    for p in publications:
        pt = fold(p.title)
        for k in keys:
            if len(k) >= 12 and (pt.startswith(k) or k.startswith(pt[:36])):
                found[p.post_id] = max(
                    found.get(p.post_id, (0, p)), (len(k), p), key=lambda x: x[0]
                )
    return [
        p for _, p in sorted(found.values(), key=lambda x: (-x[0], fold(x[1].title)))
    ]


def entries(
    site: str,
    author: str,
    *,
    access: str,
    license: str,
    exclude: str = "",
    known_titles: set[str] | None = None,
    post_type: str = "publikacja",
) -> tuple[list[ManifestEntry], list[str]]:
    """(wpisy manifestu, pominięte z powodem). Kopie tego samego pliku („… (1)”) i pozycje
    o tytule już obecnym w korpusie (`known_titles`, po `fold`) odpadają."""
    pattern = re.compile(
        "|".join(p for p in (DEFAULT_EXCLUDE, exclude) if p), re.IGNORECASE
    )
    known = {fold(t)[:60] for t in known_titles or set()}
    publications = list_publications(site, post_type) if post_type else []
    out: list[ManifestEntry] = []
    skipped: list[str] = []
    seen: set[str] = set()
    used: set[int] = set()
    for m in sorted(list_media(site), key=lambda r: r["id"]):
        fname = m["url"].rsplit("/", 1)[-1]
        if pattern.search(fold(fname)) or pattern.search(fold(m["title"])):
            skipped.append(f"plik administracyjny: {fname}")
            continue
        cands = candidates(m, publications)
        pub = next((p for p in cands if p.post_id not in used), None)
        if cands and pub is None:
            skipped.append(f"kopia pliku: {fname}")
            continue
        if pub:
            used.add(pub.post_id)
        title = pub.title if pub else re.sub(r"[_\s]+", " ", m["title"]).strip()
        title = re.sub(r"\s*\(\d\)$", "", title)
        key = fold(title)
        if key in seen:
            skipped.append(f"kopia pliku: {fname}")
            continue
        seen.add(key)
        if key[:60] in known:
            skipped.append(f"już w korpusie: {title}")
            continue
        doc_type, register = CATEGORIES.get(
            pub.category if pub else "", ("script", "popular")
        )
        desc = pub.description if pub else ""
        year = _YEAR_RE.search(desc)
        out.append(
            ManifestEntry(
                source="wp",
                source_id=f"{urllib.parse.urlparse(site).netloc}-{m['id']}",
                title=title,
                authors=[author],
                doc_type=doc_type,
                journal=urllib.parse.urlparse(site).netloc,
                year=int(year.group(1)) if year else None,
                url=pub.link if pub else m["url"],
                pdf_url=m["url"],
                license=license,
                access=access,
                language="pl",
                note=(
                    f"strona autora: {pub.category or 'publikacja'}"
                    if pub
                    else "strona autora: materiał do wykładu/webinaru"
                ),
                register=register,
                abstract=desc,
            )
        )
    return out, skipped
