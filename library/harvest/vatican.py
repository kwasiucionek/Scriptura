"""Dokumenty Kościoła o Piśmie Świętym ze strony Stolicy Apostolskiej (vatican.va).

Prawa: Libreria Editrice Vaticana / Dykasteria ds. Komunikacji. Ustawa watykańska o prawie
autorskim (2011) pozwala każdemu pobierać i rozpowszechniać materiały ze strony Stolicy
Apostolskiej bez celu zarobkowego, ale zakazuje zmieniania tekstów („L'Osservatore Romano”,
wyd. angielskie, 6.04.2011, s. 10). Dlatego bierzemy wyłącznie wersje z vatican.va (nie
przedruki z innych serwisów — polskie przekłady wydawców mają własne prawa), a w odpowiedziach
cytujemy oryginał; przekład maszynowy (Chunk.text_pl) służy tylko wyszukiwaniu.

Strona -> region treści (div.vaticanrichtext na /content/, div#corpo w /archive/ i /roman_curia/)
-> Markdown w DATA_DIR/library/vatican/<klucz>.md -> pozycja manifestu z local_file.
"""

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from library.harvest.blog import _Text
from library.harvest.manifest import ManifestEntry

BASE = "https://www.vatican.va"
LICENSE = "© LEV / Dykasteria ds. Komunikacji — niekomercyjnie, bez zmian treści (vatican.va)"
NOTE = (
    "Tekst z vatican.va. Ustawa watykańska o prawie autorskim (2011): materiały ze strony "
    "Stolicy Apostolskiej wolno pobierać i rozpowszechniać bez celu zarobkowego, bez zmian treści "
    "(L'Osservatore Romano, 6.04.2011)."
)


@dataclass(frozen=True)
class ChurchDocument:
    key: str
    title: str
    authors: tuple[str, ...]
    year: int
    path: str  # ścieżka na vatican.va
    language: str  # język wersji z vatican.va (polskiej często brak)


DOCUMENTS = (
    ChurchDocument(
        "providentissimus-deus",
        "Providentissimus Deus — encyklika o studiowaniu Pisma Świętego",
        ("Leon XIII",),
        1893,
        "/content/leo-xiii/en/encyclicals/documents/hf_l-xiii_enc_18111893_providentissimus-deus.html",
        "en",
    ),
    ChurchDocument(
        "divino-afflante-spiritu",
        "Divino afflante Spiritu — encyklika o rozwoju studiów biblijnych",
        ("Pius XII",),
        1943,
        "/content/pius-xii/en/encyclicals/documents/hf_p-xii_enc_30091943_divino-afflante-spiritu.html",
        "en",
    ),
    ChurchDocument(
        "humani-generis",
        "Humani generis — encyklika o niektórych fałszywych opiniach",
        ("Pius XII",),
        1950,
        "/content/pius-xii/en/encyclicals/documents/hf_p-xii_enc_12081950_humani-generis.html",
        "en",
    ),
    ChurchDocument(
        "sancta-mater-ecclesia",
        "Sancta Mater Ecclesia — instrukcja o historycznej prawdzie Ewangelii",
        ("Papieska Komisja Biblijna",),
        1964,
        "/roman_curia/congregations/cfaith/pcb_documents/rc_con_cfaith_doc_19640421_verita-vangeli_it.html",
        "it",
    ),
    ChurchDocument(
        "dei-verbum",
        "Dei Verbum — konstytucja dogmatyczna o Objawieniu Bożym",
        ("Sobór Watykański II",),
        1965,
        "/archive/hist_councils/ii_vatican_council/documents/vat-ii_const_19651118_dei-verbum_en.html",
        "en",
    ),
    ChurchDocument(
        "interpretacja-pisma-swietego-w-kosciele",
        "Interpretacja Pisma Świętego w Kościele",
        ("Papieska Komisja Biblijna",),
        1993,
        "/roman_curia/congregations/cfaith/pcb_documents/rc_con_cfaith_doc_19930415_interpretazione_it.html",
        "it",
    ),
    ChurchDocument(
        "verbum-domini",
        "Verbum Domini — adhortacja o Słowie Bożym w życiu i misji Kościoła",
        ("Benedykt XVI",),
        2010,
        "/content/benedict-xvi/pl/apost_exhortations/documents/hf_ben-xvi_exh_20100930_verbum-domini.html",
        "pl",
    ),
)

# wiersze bez treści dokumentu: pasek wersji językowych, stopka praw
_BOILERPLATE = re.compile(r"^\s*(\[[^\]]{0,300}\]|(copyright\b|©).*)\s*$", re.IGNORECASE)


def _is_content(attrs: dict) -> bool:
    return attrs.get("id") == "corpo" or "vaticanrichtext" in (attrs.get("class") or "")


class _Regions(_Text):
    """Tekst (z nagłówkami i akapitami jak w blog._Text) osobno dla każdego regionu treści."""

    def __init__(self):
        super().__init__()
        self.outside: list[str] = []
        self.parts = self.outside
        self.regions: list[list[str]] = []
        self.depth = 0  # zagnieżdżenie <div> w bieżącym regionie; 0 = poza regionem

    def handle_starttag(self, tag, attrs):
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif _is_content(dict(attrs)):
                self.depth = 1
                self.regions.append([])
                self.parts = self.regions[-1]
                return
        if self.depth:
            super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.depth and tag == "div":
            self.depth -= 1
            if not self.depth:
                self.parts = self.outside
                return
        if self.depth:
            super().handle_endtag(tag)

    def handle_data(self, data):
        if self.depth:
            super().handle_data(data)

    def region_texts(self) -> list[str]:
        out = []
        for region in self.regions:
            self.parts = region
            out.append(self.text())
        self.parts = self.outside
        return out


def page_text(raw: str) -> str:
    """Treść dokumentu ze strony vatican.va: najdłuższy region, bez paska języków i stopki."""
    p = _Regions()
    p.feed(raw)
    p.close()
    texts = p.region_texts()
    if not texts:
        raise ValueError("brak regionu treści (div.vaticanrichtext / div#corpo)")
    text = max(texts, key=len).replace("\xa0", " ")
    lines = [ln.strip() for ln in text.splitlines() if not _BOILERPLATE.match(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def decode(body: bytes, content_type: str = "") -> str:
    """Strony /archive/ deklarują iso-8859-1, a mają znaki cp1252 (cudzysłowy); /content/ to UTF-8."""
    m = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if m:
        charset = m.group(1)
    else:
        mb = re.search(rb"charset=[\"']?([\w-]+)", body[:4000], re.IGNORECASE)
        charset = mb.group(1).decode() if mb else "utf-8"
    charset = charset.lower()
    if charset in ("iso-8859-1", "latin-1", "latin1"):
        charset = "cp1252"
    try:
        return body.decode(charset)
    except (UnicodeDecodeError, LookupError):
        return body.decode("cp1252", errors="replace")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "scriptura-harvest/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return decode(resp.read(), resp.headers.get("Content-Type", ""))


def to_entry(doc: ChurchDocument, raw: str, out_dir: Path) -> ManifestEntry:
    """Zapisuje treść do .md i buduje pozycję manifestu (doc_type=church, access=open)."""
    body = page_text(raw)
    dest = out_dir / f"{doc.key}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# {doc.title}\n\n{body}\n", encoding="utf-8")
    return ManifestEntry(
        source="vatican",
        source_id=f"vatican-{doc.key}",
        title=doc.title,
        authors=list(doc.authors),
        doc_type="church",
        journal="vatican.va",
        year=doc.year,
        url=BASE + doc.path,
        license=LICENSE,
        access="open",
        language=doc.language,
        note=NOTE,
        register="mixed",
        local_file=str(dest),
    )


def harvest(out_dir: Path, keys: list[str] | None = None) -> list[ManifestEntry]:
    docs = [d for d in DOCUMENTS if not keys or d.key in keys]
    return [to_entry(d, fetch(BASE + d.path), out_dir) for d in docs]
