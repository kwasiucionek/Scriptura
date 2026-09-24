"""Harvest wpisów z bloga WordPress (REST API /wp-json/wp/v2/posts).

Dla każdego wpisu: tytuł, data, URL, treść HTML -> Markdown-owy tekst (nagłówki, akapity,
cytaty, listy; bez nawigacji i skryptów), zapisany do DATA_DIR/library/blog/<host>/<slug>.md.
Manifest: doc_type=blog, register=popular, access=licensed (blog publiczny, ale „wszelkie prawa
zastrzeżone” — do korpusu po zgodzie autora), local_file -> ingest_manifest bez pobierania.
Przyrostowo: --since YYYY-MM-DD filtruje po dacie modyfikacji (modified_after w API).
"""

import html
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

from library.harvest.manifest import ManifestEntry

_BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "li",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "br",
    "tr",
    "figure",
    "figcaption",
    "pre",
}
_SKIP = {
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "button",
    "iframe",
    "svg",
}


class _Text(HTMLParser):
    """HTML -> tekst z zachowaniem struktury (nagłówki jako '## ', cytaty jako '> ', listy jako '- ')."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.quote = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "blockquote":
            self.quote += 1
            self.parts.append("\n\n> ")
        elif tag in _BLOCK:
            if self.quote and tag != "br":
                self.parts.append("\n> ")  # akapity wewnątrz cytatu zostają w cytacie
            else:
                self.parts.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "blockquote":
            self.quote = max(0, self.quote - 1)
        if tag in _BLOCK and tag not in ("br", "li") and not self.quote:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self) -> str:
        t = "".join(self.parts)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r" *\n *", "\n", t)
        t = re.sub(r"(\n>\s*)+(?=\S)", "\n> ", t)  # cytat: jeden znacznik na akapit
        t = re.sub(r"\n> (> )+", "\n> ", t)
        t = re.sub(r"\n>\s*\n", "\n", t)  # pusty wiersz cytatu
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def html_to_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    return p.text()


def _get_json(url: str) -> tuple[list, dict]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "scriptura-harvest/0.1", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.load(resp), dict(resp.headers)


def fetch_posts(
    site: str, since: str | None = None, per_page: int = 50
) -> Iterator[dict]:
    """Wszystkie opublikowane wpisy (stronicowanie po X-WP-TotalPages)."""
    base = site.rstrip("/") + "/wp-json/wp/v2/posts"
    page = 1
    while True:
        params = {
            "per_page": per_page,
            "page": page,
            "status": "publish",
            "_fields": "id,date,modified,slug,link,title,content,excerpt,categories",
        }
        if since:
            params["modified_after"] = f"{since}T00:00:00"
        data, headers = _get_json(base + "?" + urllib.parse.urlencode(params))
        if not data:
            return
        yield from data
        total = int(headers.get("X-WP-TotalPages", headers.get("x-wp-totalpages", "1")))
        if page >= total:
            return
        page += 1


def post_to_entry(post: dict, author: str, out_dir: Path, site: str) -> ManifestEntry:
    """Zapisuje treść wpisu do .md i buduje pozycję manifestu."""
    host = urllib.parse.urlparse(site).netloc
    title = html.unescape(
        re.sub(r"<[^>]+>", "", post.get("title", {}).get("rendered", ""))
    ).strip()
    body = html_to_text(post.get("content", {}).get("rendered", ""))
    excerpt = html_to_text(post.get("excerpt", {}).get("rendered", ""))
    year = int(post.get("date", "0000")[:4]) or None
    dest = out_dir / host / f"{post.get('slug') or post['id']}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return ManifestEntry(
        source="blog",
        source_id=f"{host}-{post['id']}",
        title=title,
        authors=[author],
        doc_type="blog",
        journal=host,
        year=year,
        url=post.get("link", ""),
        license="wszelkie prawa zastrzeżone (blog autora; wymaga zgody)",
        access="licensed",
        language="pl",
        note=f"wpis {post.get('date', '')[:10]}, mod. {post.get('modified', '')[:10]}",
        register="popular",
        abstract=excerpt[:600],
        local_file=str(dest),
    )
