"""Dokumenty Kościoła o Piśmie Świętym ze strony Stolicy Apostolskiej (vatican.va).

Prawa: Libreria Editrice Vaticana / Dykasteria ds. Komunikacji. Ustawa watykańska o prawie
autorskim (2011) pozwala każdemu pobierać i rozpowszechniać materiały ze strony Stolicy
Apostolskiej bez celu zarobkowego, ale zakazuje zmieniania tekstów („L'Osservatore Romano”,
wyd. angielskie, 6.04.2011, s. 10). Dlatego bierzemy wyłącznie wersje z vatican.va (nie
przedruki z innych serwisów — polskie przekłady wydawców mają własne prawa). Przekład roboczy
(Chunk.text_pl) służy wyszukiwaniu, a w odpowiedziach i panelu źródeł pokazujemy z niego tylko
fragmenty z odsyłaczem do vatican.va (rag.service: use_working_translations).

Dokumenty w domenie publicznej (autor zmarł ponad 70 lat temu, np. Leon XIII) dostają pełny
polski przekład roboczy z łacińskiego oryginału: `harvest vatican --polish-pd`.

Strona -> region treści (div.vaticanrichtext na /content/, div#corpo w /archive/ i /roman_curia/)
-> Markdown w DATA_DIR/library/vatican/<klucz>.md -> pozycja manifestu z local_file.
"""

import re
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from library.harvest.blog import _Text
from library.harvest.manifest import ManifestEntry

BASE = "https://www.vatican.va"
LICENSE = (
    "© LEV / Dykasteria ds. Komunikacji — niekomercyjnie, bez zmian treści (vatican.va)"
)
NOTE = (
    "Tekst z vatican.va. Ustawa watykańska o prawie autorskim (2011): materiały ze strony "
    "Stolicy Apostolskiej wolno pobierać i rozpowszechniać bez celu zarobkowego, bez zmian treści "
    "(L'Osservatore Romano, 6.04.2011)."
)
PD_LICENSE = (
    "Oryginał: domena publiczna; przekład roboczy Scriptury (maszynowy, z łaciny)"
)
PD_NOTE = (
    "Przekład roboczy wykonany maszynowo (model językowy) z łacińskiego oryginału na vatican.va; "
    "nie jest przekładem oficjalnym. Oryginał w domenie publicznej (autor zmarł ponad 70 lat temu)."
)


@dataclass(frozen=True)
class ChurchDocument:
    key: str
    title: str
    authors: tuple[str, ...]
    year: int
    path: str  # ścieżka na vatican.va
    language: str  # język wersji z vatican.va (polskiej często brak)
    latin_path: str = ""  # łaciński oryginał — źródło pełnego przekładu roboczego
    public_domain: bool = (
        False  # wolno publikować własny pełny przekład (autor †ponad 70 lat)
    )


DOCUMENTS = (
    ChurchDocument(
        "providentissimus-deus",
        "Providentissimus Deus — encyklika o studiowaniu Pisma Świętego",
        ("Leon XIII",),
        1893,
        "/content/leo-xiii/en/encyclicals/documents/hf_l-xiii_enc_18111893_providentissimus-deus.html",
        "en",
        latin_path="/content/leo-xiii/la/encyclicals/documents/hf_l-xiii_enc_18111893_providentissimus-deus.html",
        public_domain=True,  # Leon XIII †1903
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
        # polska strona na vatican.va jest pusta (PDF też), więc wersja angielska
        "/content/benedict-xvi/en/apost_exhortations/documents/hf_ben-xvi_exh_20100930_verbum-domini.html",
        "en",
    ),
)

# wiersze bez treści dokumentu: pasek wersji językowych, stopka praw
_BOILERPLATE = re.compile(
    r"^\s*(\[[^\]]{0,300}\]|(copyright\b|©).*)\s*$", re.IGNORECASE
)
# pasek wersji językowych sklejony z blokiem tytułowym: "[AR - BE - ... - ZH] DOGMATIC CONSTITUTION"
_LANG_BAR = re.compile(
    r"\[\s*(?:[A-Z]{2}[A-Za-z-]{0,3}\s*-\s*)+[A-Z]{2}[A-Za-z-]{0,3}\s*\]\s*"
)


def _is_content(attrs: dict) -> bool:
    return attrs.get("id") == "corpo" or "vaticanrichtext" in (attrs.get("class") or "")


class _Regions(_Text):
    """Tekst (z nagłówkami i akapitami jak w blog._Text) osobno dla każdego regionu treści.
    Region = element `tag` spełniający `match` (domyślnie div.vaticanrichtext / div#corpo)."""

    def __init__(self, tag: str = "div", match=_is_content):
        super().__init__()
        self.outside: list[str] = []
        self.parts = self.outside
        self.regions: list[list[str]] = []
        self.tag, self.match = tag, match
        self.depth = 0  # zagnieżdżenie `tag` w bieżącym regionie; 0 = poza regionem

    def handle_starttag(self, tag, attrs):
        if tag == self.tag:
            if self.depth:
                self.depth += 1
            elif self.match(dict(attrs)):
                self.depth = 1
                self.regions.append([])
                self.parts = self.regions[-1]
                return
        if self.depth:
            super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.depth and tag == self.tag:
            self.depth -= 1
            if not self.depth:
                self.parts = self.outside
                return
        if self.depth:
            super().handle_endtag(tag)

    def handle_data(self, data):
        if self.depth:  # w HTML znak nowej linii w tekście to spacja (strony /archive/ łamią akapity)
            super().handle_data(re.sub(r"\s+", " ", data))

    def region_texts(self) -> list[str]:
        out = []
        for region in self.regions:
            self.parts = region
            out.append(self.text())
        self.parts = self.outside
        return out


def page_text(raw: str) -> str:
    """Treść dokumentu ze strony vatican.va: najdłuższy region, bez paska języków i stopki."""
    texts: list[str] = []
    # najstarsze strony (np. dokumenty PKB z lat 60.) nie mają kontenera treści — wtedy <body>
    for tag, match in (("div", _is_content), ("body", lambda _attrs: True)):
        p = _Regions(tag, match)
        p.feed(raw)
        p.close()
        texts = [t for t in p.region_texts() if t.strip()]
        if texts:
            break
    if not texts:
        raise ValueError("brak treści (div.vaticanrichtext / div#corpo / body)")
    text = max(texts, key=len).replace("\xa0", " ")
    # <br> w obrębie akapitu (bloki tytułowe, adresaci) -> spacja; inaczej każda linia tytułu
    # WIELKIMI LITERAMI stałaby się osobną sekcją z jednozdaniowym chunkiem
    text = re.sub(r"(?<=\S)[ \t]*\n(?=[ \t]*[^\s\-#>])", " ", text)
    text = _LANG_BAR.sub("", text)
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


# granica zdania w łacinie/językach nowożytnych: znak końca + wielka litera, cudzysłów lub nawias
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-ÝĄĆĘŁŃÓŚŹŻ«„\"(])")


def split_for_translation(paragraph: str, max_chars: int = 2500) -> list[str]:
    """Akapit -> porcje ≤ max_chars na granicach zdań (zdanie dłuższe niż limit zostaje całe)."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces, cur = [], ""
    for sent in _SENTENCE_RE.split(paragraph):
        if cur and len(cur) + len(sent) + 1 > max_chars:
            pieces.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        pieces.append(cur)
    return pieces


def polish_translation(
    doc: ChurchDocument,
    raw_latin: str,
    out_dir: Path,
    translate: Callable[[str], str],
    workers: int = 4,
) -> ManifestEntry:
    """Pełny polski przekład roboczy z łaciny (tylko domena publiczna) -> .md i pozycja manifestu.
    `translate` tłumaczy jedną porcję łaciny na polski; porcje idą równolegle, kolejność zostaje."""
    if not doc.public_domain:
        raise ValueError(f"{doc.key}: pełny przekład tylko dla domeny publicznej")
    paragraphs = [p for p in page_text(raw_latin).split("\n\n") if p.strip()]
    pieces = [
        (i, piece)
        for i, p in enumerate(paragraphs)
        for piece in split_for_translation(p)
    ]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        translated = list(pool.map(lambda item: translate(item[1]), pieces))
    by_paragraph: dict[int, list[str]] = {}
    for (i, _), text in zip(pieces, translated, strict=True):
        by_paragraph.setdefault(i, []).append(text.strip())
    body = "\n\n".join(" ".join(by_paragraph[i]) for i in sorted(by_paragraph))
    title = f"{doc.title} (przekład roboczy z łaciny)"
    dest = out_dir / f"{doc.key}-pl.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return ManifestEntry(
        source="vatican",
        source_id=f"vatican-{doc.key}-pl",
        title=title,
        authors=list(doc.authors),
        doc_type="church",
        journal="vatican.va",
        year=doc.year,
        url=BASE + doc.latin_path,
        license=PD_LICENSE,
        access="open",
        language="pl",
        note=PD_NOTE,
        register="mixed",
        local_file=str(dest),
    )


def harvest(out_dir: Path, keys: list[str] | None = None) -> list[ManifestEntry]:
    """Wersje z vatican.va. Bez `keys` pomija dokumenty z domeny publicznej z łacińskim
    oryginałem — ich wersją w korpusie jest pełny przekład roboczy (`--polish-pd`)."""
    if keys:
        docs = [d for d in DOCUMENTS if d.key in keys]
    else:
        docs = [d for d in DOCUMENTS if not (d.public_domain and d.latin_path)]
    return [to_entry(d, fetch(BASE + d.path), out_dir) for d in docs]
