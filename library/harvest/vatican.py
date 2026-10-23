"""Dokumenty Kościoła o Piśmie Świętym: vatican.va oraz teksty z domeny publicznej.

Prawa: teksty z vatican.va należą do Libreria Editrice Vaticana / Dykasterii ds. Komunikacji.
Ustawa watykańska o prawie autorskim (2011) pozwala każdemu pobierać i rozpowszechniać materiały
ze strony Stolicy Apostolskiej bez celu zarobkowego, ale zakazuje zmieniania tekstów
(„L'Osservatore Romano”, wyd. angielskie, 6.04.2011, s. 10). Dlatego nie bierzemy przedruków
z innych serwisów (polskie przekłady wydawców mają własne prawa). Przekład roboczy
(Chunk.text_pl) służy wyszukiwaniu, a w odpowiedziach i panelu źródeł pokazujemy z niego tylko
fragmenty z odsyłaczem do oryginału (library.search.use_working_translations).

Teksty w domenie publicznej (autor zmarł ponad 70 lat temu: encykliki Leona XIII, Piusa X,
Benedykta XV, Piusa XI, dekrety soborów, Ojcowie) dostają pełny polski przekład roboczy
z oryginału: `harvest vatican --polish-pd`. Źródła oryginałów:
- vatican.va (HTML) i skany Acta Apostolicae Sedis (PDF z warstwą tekstową, zakres stron),
- łacińskie Wikiźródła (wydania z XVIII–XIX w., m.in. Patrologia Latina Migne'a),
- CCEL: angielskie przekłady ANF/NPNF (XIX w.), sekcja tomu ThML bez przypisów.
Wyciąg z dłuższej strony wyznaczają `start` i `end` (pierwsze i ostatnie słowa, włącznie).

Strona -> tekst (akapity) -> Markdown w DATA_DIR/library/vatican/<klucz>.md -> pozycja manifestu.
"""

import re
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from library.harvest.blog import _Text, html_to_text
from library.harvest.manifest import ManifestEntry

BASE = "https://www.vatican.va"
USER_AGENT = "Scriptura/0.1 (https://scriptura.cytr.us)"
LICENSE = (
    "© LEV / Dykasteria ds. Komunikacji — niekomercyjnie, bez zmian treści (vatican.va)"
)
NOTE = (
    "Tekst z vatican.va. Ustawa watykańska o prawie autorskim (2011): materiały ze strony "
    "Stolicy Apostolskiej wolno pobierać i rozpowszechniać bez celu zarobkowego, bez zmian treści "
    "(L'Osservatore Romano, 6.04.2011)."
)
_FROM = {"la": "z łaciny", "en": "z angielskiego", "grc": "z greki"}


@dataclass(frozen=True)
class ChurchDocument:
    key: str
    title: str
    authors: tuple[str, ...]
    year: int
    path: str  # wersja do korpusu: ścieżka na vatican.va (HTML/PDF) albo pełny URL
    language: str  # język tej wersji
    latin_path: str = ""  # oryginał pełnego przekładu (domena publiczna); puste = path
    public_domain: bool = False  # wolno publikować własny pełny przekład
    pages: tuple[int, int] | None = None  # PDF (AAS): strony 1-based, włącznie
    start: str = ""  # wyciąg od tych słów (włącznie)
    end: str = ""  # wyciąg do tych słów (włącznie)
    ccel_div: str = ""  # CCEL: id sekcji w XML tomu (path = strona tej sekcji)
    drop: str = ""  # regex wtrąceń wydawcy do usunięcia (np. numeracja Denzingera)
    source: str = "vatican.va"  # pole journal: vatican.va | Wikiźródła | CCEL
    original_language: str = "la"  # język oryginału przy pełnym przekładzie
    parts: tuple[
        str, ...
    ] = ()  # tekst z kilku stron (rozdziały na Wikiźródłach), łączony
    # ujęcie (Document.perspective): dokument urzędowy Kościoła katolickiego; starożytne
    # świadectwa kanonu (Muratori, Atanazy, Kanony Apostolskie, Laodycea) — bez ujęcia
    perspective: str = "magisterium"

    @property
    def url(self) -> str:
        return absolute(self.path)

    @property
    def original_url(self) -> str:
        return absolute(self.latin_path or self.path)


# Katechizm Kościoła Katolickiego na vatican.va (IntraText, angielski)
_CCC = "/archive/ENG0015/"
# nawigacja IntraText: okruszki na początku („Help … Previous - Next”) i stopka praw na końcu
_CCC_DROP = r"(?s)\AHelp\b.*?Previous - Next\s*|Previous - Next\s*Copyright\b.*\Z"
# Jan Paweł II: polskie wersje encyklik na vatican.va
_JP2 = "/content/john-paul-ii/pl/encyclicals/documents/"


def absolute(path: str) -> str:
    return path if path.startswith("http") else BASE + path


_AAS_1924 = "/archive/aas/documents/AAS-16-1924-ocr.pdf"
_AAS_1950 = "/archive/aas/documents/AAS-42-1950-ocr.pdf"
_WS = "https://la.wikisource.org/wiki/"
_CCEL = "https://www.ccel.org/ccel/schaff/"

DOCUMENTS = (
    # --- vatican.va: oryginał chroniony -> wersja z vatican.va + przekład roboczy we fragmentach
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
        "sedula-cura",
        "Sedula cura — motu proprio o Papieskiej Komisji Biblijnej",
        ("Paweł VI",),
        1971,
        "/content/paul-vi/it/motu_proprio/documents/hf_p-vi_motu-proprio_19710627_sedula-cura.html",
        "it",
    ),
    ChurchDocument(
        "dominum-et-vivificantem",
        "Dominum et Vivificantem — encyklika o Duchu Świętym w życiu Kościoła i świata",
        ("Jan Paweł II",),
        1986,
        "/content/john-paul-ii/pl/encyclicals/documents/hf_jp-ii_enc_18051986_dominum-et-vivificantem.html",
        "pl",
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
    # --- Maryja, Kościół i prymat: przeciwwaga dla egzegezy historyczno-krytycznej
    ChurchDocument(
        "lumen-gentium",
        "Lumen gentium — konstytucja dogmatyczna o Kościele",
        ("Sobór Watykański II",),
        1964,
        # polskiej wersji na vatican.va brak (404)
        "/archive/hist_councils/ii_vatican_council/documents/vat-ii_const_19641121_lumen-gentium_en.html",
        "en",
    ),
    ChurchDocument(
        "redemptoris-mater",
        "Redemptoris Mater — encyklika o błogosławionej Maryi Dziewicy w życiu pielgrzymującego Kościoła",
        ("Jan Paweł II",),
        1987,
        _JP2 + "hf_jp-ii_enc_25031987_redemptoris-mater.html",
        "pl",
    ),
    ChurchDocument(
        "ut-unum-sint",
        "Ut unum sint — encyklika o działalności ekumenicznej",
        ("Jan Paweł II",),
        1995,
        _JP2 + "hf_jp-ii_enc_25051995_ut-unum-sint.html",
        "pl",
    ),
    ChurchDocument(
        "katechizm-484-511",
        "Katechizm Kościoła Katolickiego, nr 484–511 — poczęty z Ducha Świętego, narodził się z Maryi Panny",
        (),
        1992,
        _CCC + "__P1K.HTM",
        "en",
        drop=_CCC_DROP,
    ),
    ChurchDocument(
        "katechizm-871-945",
        "Katechizm Kościoła Katolickiego, nr 871–945 — wierni: hierarchia, świeccy, życie konsekrowane",
        (),
        1992,
        _CCC + "__P2A.HTM",
        "en",
        drop=_CCC_DROP,
    ),
    ChurchDocument(
        "katechizm-963-975",
        "Katechizm Kościoła Katolickiego, nr 963–975 — Maryja, Matka Chrystusa, Matka Kościoła",
        (),
        1992,
        _CCC + "__P2C.HTM",
        "en",
        drop=_CCC_DROP,
    ),
    ChurchDocument(
        # podpis sekretarza (A. Miller †1963) — status domeny publicznej niepewny, więc jak
        # dokumenty chronione: łaciński oryginał z AAS + przekład roboczy we fragmentach
        "instrukcja-pkb-1950",
        "Instrukcja o właściwym nauczaniu Pisma Świętego w seminariach i kolegiach zakonnych",
        ("Papieska Komisja Biblijna",),
        1950,
        _AAS_1950,
        "la",
        pages=(495, 505),
        start="COMMISSIO PONTIFICIA DE RE BIBLICA",  # tytuł „I N S T R U C T I O” rozstrzelony w OCR
        end="Consultor ab Actis.",
        source="vatican.va (AAS 42, 1950, s. 495–505)",
    ),
    # --- domena publiczna: pełny polski przekład roboczy z oryginału
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
        "vigilantiae-studiique",
        "Vigilantiae studiique — list apostolski ustanawiający Papieską Komisję Biblijną",
        ("Leon XIII",),
        1902,
        "/content/leo-xiii/it/apost_letters/documents/hf_l-xiii_apl_19021030_vigilantiae-studiique.html",
        "it",
        latin_path="/content/leo-xiii/la/apost_letters/documents/hf_l-xiii_apl_19021030_vigilantiae-studiique.html",
        public_domain=True,
    ),
    ChurchDocument(
        "scripturae-sanctae",
        "Scripturae Sanctae — list apostolski o stopniach akademickich z Pisma Świętego",
        ("Pius X",),
        1904,
        "/content/pius-x/it/apost_letters/documents/hf_p-x_apl_19040223_scripturae-sanctae.html",
        "it",
        latin_path="/content/pius-x/la/apost_letters/documents/hf_p-x_apl_19040223_scripturae-sanctae.html",
        public_domain=True,  # Pius X †1914
    ),
    ChurchDocument(
        "praestantia-scripturae",
        "Praestantia Scripturae — motu proprio o decyzjach Papieskiej Komisji Biblijnej",
        ("Pius X",),
        1907,
        "/content/pius-x/la/motu_proprio/documents/hf_p-x_motu-proprio_19071118_praestantia-scripturae.html",
        "la",
        public_domain=True,
    ),
    ChurchDocument(
        "vinea-electa",
        "Vinea electa — list apostolski ustanawiający Papieski Instytut Biblijny",
        ("Pius X",),
        1909,
        "/content/pius-x/la/apost_letters/documents/hf_p-x_apl_19090507_vinea-electa.html",
        "la",
        public_domain=True,
    ),
    ChurchDocument(
        "spiritus-paraclitus",
        "Spiritus Paraclitus — encyklika o św. Hieronimie i Piśmie Świętym",
        ("Benedykt XV",),
        1920,
        "/content/benedict-xv/en/encyclicals/documents/hf_ben-xv_enc_15091920_spiritus-paraclitus.html",
        "en",
        latin_path="/content/benedict-xv/la/encyclicals/documents/hf_ben-xv_enc_15091920_spiritus-paraclitus.html",
        public_domain=True,  # Benedykt XV †1922
    ),
    ChurchDocument(
        "bibliorum-scientiam",
        "Bibliorum scientiam — motu proprio o wykładowcach Pisma Świętego",
        ("Pius XI",),
        1924,
        _AAS_1924,
        "la",
        public_domain=True,  # Pius XI †1939
        pages=(180, 182),
        start="Bibliorum scientiam quanti",
        end="Pontificatus Nostri tertio.",  # podpis „PIUS P P . X I” w OCR ze spacjami
        source="vatican.va (AAS 16, 1924, s. 180–182)",
    ),
    ChurchDocument(
        "kanon-muratoriego",
        "Kanon Muratoriego — najstarszy wykaz ksiąg Nowego Testamentu",
        (),
        180,
        _WS + "Fragmentum_acephalum_de_canone_scripturarum",
        "la",
        public_domain=True,
        start="QUIBUS tamen interfuit",
        end="Catafrygum constitutorem.",
        source="Wikiźródła (Migne, PL 3, 1844)",
        perspective="",
    ),
    ChurchDocument(
        "synod-w-laodycei-kanony-59-60",
        "Synod w Laodycei, kanony 59–60 — o księgach czytanych w Kościele",
        ("Synod w Laodycei",),
        363,
        _CCEL + "npnf214.viii.vii.iii.lxiv.html",
        "en",
        public_domain=True,
        ccel_div="viii.vii.iii.lxiv",  # kan. 59; kan. 60 — sekcja następna (dołączana niżej)
        original_language="en",
        source="CCEL (NPNF II/14, Percival 1900)",
        perspective="",
    ),
    ChurchDocument(
        "atanazy-list-39",
        "Atanazy, 39. list paschalny — o księgach przyjętych przez Kościół",
        ("Atanazy Wielki",),
        367,
        _CCEL + "npnf204.xxv.iii.iii.xxv.html",
        "en",
        public_domain=True,
        ccel_div="xxv.iii.iii.xxv",
        original_language="en",
        source="CCEL (NPNF II/4, 1892)",
        perspective="",
    ),
    ChurchDocument(
        "kanony-apostolskie-85",
        "Kanony Apostolskie, kanon 85 — wykaz ksiąg świętych",
        ("Kanony Apostolskie",),
        380,
        _CCEL + "anf07.ix.ix.vi.html",
        "en",
        public_domain=True,
        ccel_div="ix.ix.vi",
        start="Let the following books be esteemed venerable",
        end="and the Acts of us the Apostles.",
        original_language="en",
        source="CCEL (ANF 7, 1886)",
        perspective="",
    ),
    ChurchDocument(
        "synody-afrykanskie-kanon-o-pismach",
        "Synody w Hipponie (393) i Kartaginie (397) — kanon o Pismach kanonicznych",
        ("Synod w Hipponie", "Synod w Kartaginie"),
        397,
        _WS + "Breviarium_canonicum",
        "la",
        public_domain=True,
        start="Ut praeter Scripturas canonicas, nihil in Ecclesia legatur",
        end="Apocalypsis Joannis liber unus.",
        source="Wikiźródła (Migne, PL 88, 1850)",
    ),
    ChurchDocument(
        "innocenty-i-consulenti-tibi",
        "Innocenty I, list Consulenti tibi do Eksuperiusza — wykaz ksiąg kanonicznych",
        ("Innocenty I",),
        405,
        _WS + "Epistolae_et_decreta_(Innocentius_I)",
        "la",
        public_domain=True,
        start="CAP. VII. 13. Qui vero libri recipiantur",
        end="viris clarissimis consulibus.",
        source="Wikiźródła (Migne, PL 20)",
    ),
    ChurchDocument(
        "decretum-gelasianum",
        "Decretum Gelasianum — o księgach przyjmowanych i nieprzyjmowanych",
        ("Gelazjusz I (przypisywane)",),
        495,
        _WS + "Decretum_Gelasianum",
        "la",
        public_domain=True,
        start="LIBER PRIMUS",
        end="confitemur esse damnata.",
        source="Wikiźródła",
    ),
    ChurchDocument(
        "sobor-w-vienne-o-jezykach",
        "Sobór w Vienne (1311–1312) — dekret o nauczaniu języków biblijnych",
        ("Sobór w Vienne",),
        1312,
        _WS + "Concilium_Viennense",
        "la",
        public_domain=True,
        start="Inter sollicitudines nostris humeris",
        end="nolumus quoad alia praeiudicium generari.",
        source="Wikiźródła",
    ),
    ChurchDocument(
        "sobor-florencki-dekret-dla-jakobitow",
        "Sobór Florencki, dekret dla Jakobitów (1442) — o kanonie Pisma Świętego",
        ("Sobór Florencki",),
        1442,
        _WS + "Enchiridion_symbolorum,_definitionum_et_declarationum",
        "la",
        public_domain=True,
        start="Unum atque eundem Deum Veteris et Novi Testamenti",
        end="Actibus Apostolorum et Apocalypsi Johannis.",
        drop=r"\b1\d{3} \d{3}\b",  # numeracja Denzingera wewnątrz wyciągu
        source="Wikiźródła (Enchiridion symbolorum, tylko tekst dekretu)",
    ),
    ChurchDocument(
        "sobor-trydencki-sesja-iv",
        "Sobór Trydencki, sesja IV (1546) — dekrety o księgach kanonicznych i o Wulgacie",
        ("Sobór Trydencki",),
        1546,
        _WS + "Concilium_Tridentinum",
        "la",
        public_domain=True,
        start="[Decretum de Canonicis scripturis]",
        end="per episcopos coerceantur.",
        source="Wikiźródła (Patavii 1738)",
    ),
    ChurchDocument(
        "sobor-trydencki-sesja-v-lektura",
        "Sobór Trydencki, sesja V (1546) — dekret o wykładzie Pisma Świętego i przepowiadaniu",
        ("Sobór Trydencki",),
        1546,
        _WS + "Concilium_Tridentinum",
        "la",
        public_domain=True,
        start="[Decretum II: pro lectione Sacrae Scripturae",
        end="omnino arceantur.",
        source="Wikiźródła (Patavii 1738)",
    ),
    ChurchDocument(
        "sobor-watykanski-i-pastor-aeternus",
        "Sobór Watykański I, Pastor aeternus (1870) — konstytucja dogmatyczna o Kościele Chrystusowym",
        ("Sobór Watykański I",),
        1870,
        _WS + "Pastor_Aeternus",
        "la",
        public_domain=True,
        parts=tuple(
            _WS + "Pastor_Aeternus/" + part
            for part in ("Praefatio", "Caput_I", "Caput_II", "Caput_III", "Caput_IV")
        ),
        source="Wikiźródła",
    ),
    ChurchDocument(
        "sobor-watykanski-i-dei-filius-2",
        "Sobór Watykański I, Dei Filius, rozdz. II (1870) — o objawieniu",
        ("Sobór Watykański I",),
        1870,
        _WS + "Dei_Filius",
        "la",
        public_domain=True,
        start="CAPUT II DE REVELATIONE",
        end="ipsam Scripturam sacram interpretari.",
        source="Wikiźródła",
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
_MIGNE_COLUMN = re.compile(r"\s*\(\d{4}[A-D]\)\s*")  # „(0501A)” — łamanie kolumn w PL
# żywa pagina skanów AAS („180 Acta Apostolicae Sedis - Commentarium Officiale”, „Acta Pii PP. XI 181”)
_AAS_HEADER = re.compile(
    r"^\s*\d*\s*(Acta Apostolicae Sedis.*|Acta \w+ PP\. ?[IVXL]+.*|Commissio Pontificia de re Biblica.*)\s*\d*\s*$"
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


def pdf_text(data: bytes, pages: tuple[int, int]) -> str:
    """Tekst stron skanu AAS (warstwa OCR): bez żywej paginy, z połączonym dzieleniem wyrazów.
    Akapitów skan nie zachowuje — tekst dzielony po zdaniach na porcje ~1500 znaków."""
    import pymupdf

    first, last = pages
    lines: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for number in range(first, last + 1):
            for line in doc[number - 1].get_text().splitlines():
                if line.strip() and not _AAS_HEADER.match(line):
                    lines.append(line.strip())
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", "\n".join(lines))
    text = re.sub(r"\s+", " ", text).strip()
    return "\n\n".join(split_for_translation(text, 1500))


def _ccel_text(el: ET.Element) -> str:
    """Tekst elementu ThML bez przypisów (<note>) i odsyłaczy (<sup>); akapity po <p>."""
    local = el.tag.rsplit("}", 1)[-1]
    if local in ("note", "sup"):
        return el.tail or ""
    inner = (el.text or "") + "".join(_ccel_text(child) for child in el)
    if local in ("p", "h1", "h2", "h3", "h4", "h5", "h6"):
        inner = "\n\n" + inner.strip() + "\n\n"
    return inner + (el.tail or "")


_NOTES_RE = re.compile(
    r"\n\nNotes\.\n\n.*", re.S
)  # komentarz wydawcy po tekście kanonu (NPNF)


def ccel_section(xml: bytes, div_id: str, following: int = 0) -> str:
    """Tekst sekcji tomu CCEL (np. „viii.vii.iii.lxiv”) i `following` kolejnych sekcji tego
    samego poziomu, bez komentarza wydawcy („Notes.” i dalej). Id akapitów („…-p2.1”) też mają
    kropki, więc liczą się tylko elementy <divN>."""
    root = ET.fromstring(xml)
    depth = div_id.count(".")
    sections = [
        el
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1].lower().startswith("div")
        and el.get("id", "").count(".") == depth
    ]
    ids = [el.get("id") for el in sections]
    if div_id not in ids:
        raise ValueError(f"brak sekcji {div_id} w tomie CCEL")
    i = ids.index(div_id)
    parts = []
    for el in sections[i : i + 1 + following]:
        text = re.sub(r"\n\s*\n\s*", "\n\n", re.sub(r"[ \t]+", " ", _ccel_text(el)))
        parts.append(_NOTES_RE.sub("", "\n\n" + text.strip() + "\n\n").strip())
    return "\n\n".join(parts)


def _marker_re(marker: str) -> re.Pattern:
    return re.compile(r"\s+".join(re.escape(w) for w in marker.split()))


def excerpt(text: str, start: str = "", end: str = "") -> str:
    """Wycinek od pierwszego wystąpienia `start` do pierwszego `end` po nim (oba włącznie).
    Odstępy w znacznikach dowolne; brak znacznika = błąd (nie wczytujemy złego fragmentu)."""
    if start:
        m = _marker_re(start).search(text)
        if not m:
            raise ValueError(f"brak początku wyciągu: {start!r}")
        text = text[m.start() :]
    if end:
        m = _marker_re(end).search(text)
        if not m:
            raise ValueError(f"brak końca wyciągu: {end!r}")
        text = text[: m.end()]
    return text.strip()


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


_CACHE: dict[str, tuple[bytes, str]] = {}


def fetch_bytes(url: str) -> tuple[bytes, str]:
    """(treść, Content-Type); pamięć podręczna procesu — kilka dokumentów z tej samej strony/tomu."""
    if url not in _CACHE:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
            _CACHE[url] = (resp.read(), resp.headers.get("Content-Type", ""))
    return _CACHE[url]


def fetch(url: str) -> str:
    body, content_type = fetch_bytes(url)
    return decode(body, content_type)


def source_text(doc: ChurchDocument, url: str) -> str:
    """Tekst dokumentu z `url` według rodzaju źródła, przycięty do wyciągu start–end."""
    if doc.parts:
        # kilka stron (rozdziały): każda osobno, w kolejności, potem wspólny wyciąg
        text = "\n\n".join(
            source_text(
                ChurchDocument(**{**doc.__dict__, "parts": (), "start": "", "end": ""}),
                part,
            )
            for part in doc.parts
        )
        return excerpt(text, doc.start, doc.end)
    if doc.ccel_div:
        volume = re.search(r"/schaff/([a-z0-9]+)\.", url).group(1)
        xml, _ = fetch_bytes(f"https://www.ccel.org/ccel/schaff/{volume}.xml")
        # Laodycea: kanony 59 i 60 to dwie sąsiednie sekcje
        text = ccel_section(xml, doc.ccel_div, following=1 if "59-60" in doc.key else 0)
    elif url.lower().endswith(".pdf"):
        if not doc.pages:
            raise ValueError(f"{doc.key}: PDF bez zakresu stron")
        text = pdf_text(fetch_bytes(url)[0], doc.pages)
    elif "vatican.va" in url:
        text = page_text(fetch(url))
    else:
        render = url + ("&" if "?" in url else "?") + "action=render"
        text = html_to_text(fetch(render if "wikisource.org/wiki/" in url else url))
    # kolumny Migne'a usuwamy przed szukaniem znaczników — potrafią rozdzielić słowa wyciągu
    text = _MIGNE_COLUMN.sub(" ", text.replace("\xa0", " "))
    text = excerpt(text, doc.start, doc.end)
    if doc.drop:
        text = re.sub(doc.drop, " ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def to_entry(doc: ChurchDocument, text: str, out_dir: Path) -> ManifestEntry:
    """Zapisuje tekst do .md i buduje pozycję manifestu (doc_type=church, access=open)."""
    dest = out_dir / f"{doc.key}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# {doc.title}\n\n{text}\n", encoding="utf-8")
    return ManifestEntry(
        source="vatican",
        source_id=f"vatican-{doc.key}",
        title=doc.title,
        authors=list(doc.authors),
        doc_type="church",
        journal=doc.source,
        year=doc.year,
        url=doc.url + (f"#page={doc.pages[0]}" if doc.pages else ""),
        license=LICENSE,
        access="open",
        language=doc.language,
        note=NOTE,
        register="mixed",
        perspective=doc.perspective,
        local_file=str(dest),
    )


# granica zdania w łacinie/językach nowożytnych: znak końca + wielka litera, cudzysłów lub nawias
_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÀ-ÝĄĆĘŁŃÓŚŹŻ«„\"(\[])")


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


def pd_license(doc: ChurchDocument) -> tuple[str, str]:
    """(licencja ≤128 znaków, notatka ≤300) pełnego przekładu roboczego."""
    how = _FROM.get(doc.original_language, doc.original_language)
    base = (
        "Przekład angielski (XIX w.) i oryginał: domena publiczna"
        if doc.original_language == "en"
        else "Oryginał: domena publiczna"
    )
    note = (
        f"Przekład roboczy wykonany maszynowo (model językowy) {how}; źródło: {doc.source}. "
        "Nie jest przekładem oficjalnym. Tekst wyjściowy w domenie publicznej."
    )
    return f"{base}; przekład roboczy Scriptury (maszynowy, {how})", note


def polish_translation(
    doc: ChurchDocument,
    original: str,
    out_dir: Path,
    translate: Callable[[str], str],
    workers: int = 4,
) -> ManifestEntry:
    """Pełny polski przekład roboczy (tylko domena publiczna) -> .md i pozycja manifestu.
    `original` to tekst z akapitami (pusty wiersz); `translate` tłumaczy jedną porcję na polski;
    porcje idą równolegle, kolejność zostaje."""
    if not doc.public_domain:
        raise ValueError(f"{doc.key}: pełny przekład tylko dla domeny publicznej")
    paragraphs = [p for p in original.split("\n\n") if p.strip()]
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
    how = _FROM.get(doc.original_language, doc.original_language)
    title = f"{doc.title} (przekład roboczy {how})"
    dest = out_dir / f"{doc.key}-pl.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    license_, note = pd_license(doc)
    return ManifestEntry(
        source="vatican",
        source_id=f"vatican-{doc.key}-pl",
        title=title,
        authors=list(doc.authors),
        doc_type="church",
        journal=doc.source,
        year=doc.year,
        url=doc.original_url + (f"#page={doc.pages[0]}" if doc.pages else ""),
        license=license_,
        access="open",
        language="pl",
        note=note,
        register="mixed",
        perspective=doc.perspective,
        local_file=str(dest),
    )


def harvest(out_dir: Path, keys: list[str] | None = None) -> list[ManifestEntry]:
    """Wersje oryginalne (bez przekładu). Bez `keys` pomija domenę publiczną — jej wersją
    w korpusie jest pełny przekład roboczy (`--polish-pd`)."""
    if keys:
        docs = [d for d in DOCUMENTS if d.key in keys]
    else:
        docs = [d for d in DOCUMENTS if not d.public_domain]
    return [to_entry(d, source_text(d, d.url), out_dir) for d in docs]
