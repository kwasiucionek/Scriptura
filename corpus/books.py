"""Kanoniczna tabela ksiąg biblijnych (kanon katolicki, 73 księgi).

Jedno źródło prawdy dla:
- identyfikatorów OSIS (klucz kanoniczny w bazie),
- polskich skrótów wg Biblii Tysiąclecia + aliasy (BW, BG, potoczne),
- mapowania na nazwy plików/kluczy w źródłach (OSHB, MorphGNT, JSON BG).

Kolejność w liście = kolejność kanoniczna (BT). `order` służy do sortowania
i budowy `Verse.ordinal`.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BookSpec:
    osis: str  # identyfikator OSIS, np. "Gen", "Matt"
    order: int  # pozycja kanoniczna 1..73
    testament: str  # "OT" | "NT"
    abbr: str  # skrót BT, np. "Rdz", "Mt"
    name_pl: str  # pełna polska nazwa
    aliases: tuple[str, ...] = ()  # inne skróty spotykane w literaturze
    deuterocanonical: bool = False
    oshb_file: str | None = None  # nazwa pliku w openscriptures/morphhb/wlc
    morphgnt_code: str | None = None  # dwucyfrowy kod księgi w MorphGNT
    en_name: str | None = None  # klucz w JSON Biblii Gdańskiej (bible-api-io)


# fmt: off
BOOKS: list[BookSpec] = [
    # --- Pięcioksiąg ---
    BookSpec("Gen", 1, "OT", "Rdz", "Księga Rodzaju", ("Gn", "1 Mojż", "1Mojż", "1 Mż", "I Mojż"), oshb_file="Gen", en_name="Genesis"),
    BookSpec("Exod", 2, "OT", "Wj", "Księga Wyjścia", ("Ex", "2 Mojż", "2Mojż", "2 Mż", "II Mojż"), oshb_file="Exod", en_name="Exodus"),
    BookSpec("Lev", 3, "OT", "Kpł", "Księga Kapłańska", ("Kpl", "Lv", "3 Mojż", "3Mojż", "3 Mż", "III Mojż"), oshb_file="Lev", en_name="Leviticus"),
    BookSpec("Num", 4, "OT", "Lb", "Księga Liczb", ("Nm", "4 Mojż", "4Mojż", "4 Mż", "IV Mojż"), oshb_file="Num", en_name="Numbers"),
    BookSpec("Deut", 5, "OT", "Pwt", "Księga Powtórzonego Prawa", ("Dt", "5 Mojż", "5Mojż", "5 Mż", "V Mojż"), oshb_file="Deut", en_name="Deuteronomy"),
    # --- Księgi historyczne ---
    BookSpec("Josh", 6, "OT", "Joz", "Księga Jozuego", ("Jos",), oshb_file="Josh", en_name="Joshua"),
    BookSpec("Judg", 7, "OT", "Sdz", "Księga Sędziów", ("Sędz", "Sdz."), oshb_file="Judg", en_name="Judges"),
    BookSpec("Ruth", 8, "OT", "Rt", "Księga Rut", ("Rut",), oshb_file="Ruth", en_name="Ruth"),
    BookSpec("1Sam", 9, "OT", "1 Sm", "1 Księga Samuela", ("1Sm", "1 Sam", "1Sam", "I Sm", "I Sam"), oshb_file="1Sam", en_name="1 Samuel"),
    BookSpec("2Sam", 10, "OT", "2 Sm", "2 Księga Samuela", ("2Sm", "2 Sam", "2Sam", "II Sm", "II Sam"), oshb_file="2Sam", en_name="2 Samuel"),
    BookSpec("1Kgs", 11, "OT", "1 Krl", "1 Księga Królewska", ("1Krl", "1 Król", "1Król", "I Krl", "I Król"), oshb_file="1Kgs", en_name="1 Kings"),
    BookSpec("2Kgs", 12, "OT", "2 Krl", "2 Księga Królewska", ("2Krl", "2 Król", "2Król", "II Krl", "II Król"), oshb_file="2Kgs", en_name="2 Kings"),
    BookSpec("1Chr", 13, "OT", "1 Krn", "1 Księga Kronik", ("1Krn", "1 Kron", "1Kron", "I Krn", "I Kron"), oshb_file="1Chr", en_name="1 Chronicles"),
    BookSpec("2Chr", 14, "OT", "2 Krn", "2 Księga Kronik", ("2Krn", "2 Kron", "2Kron", "II Krn", "II Kron"), oshb_file="2Chr", en_name="2 Chronicles"),
    BookSpec("Ezra", 15, "OT", "Ezd", "Księga Ezdrasza", ("Ezdr", "Esd"), oshb_file="Ezra", en_name="Ezra"),
    BookSpec("Neh", 16, "OT", "Ne", "Księga Nehemiasza", ("Neh",), oshb_file="Neh", en_name="Nehemiah"),
    BookSpec("Tob", 17, "OT", "Tb", "Księga Tobiasza", ("Tob",), deuterocanonical=True),
    BookSpec("Jdt", 18, "OT", "Jdt", "Księga Judyty", ("Jud.",), deuterocanonical=True),
    BookSpec("Esth", 19, "OT", "Est", "Księga Estery", ("Estery",), oshb_file="Esth", en_name="Esther"),
    BookSpec("1Macc", 20, "OT", "1 Mch", "1 Księga Machabejska", ("1Mch", "1 Mach", "1Mach", "I Mch"), deuterocanonical=True),
    BookSpec("2Macc", 21, "OT", "2 Mch", "2 Księga Machabejska", ("2Mch", "2 Mach", "2Mach", "II Mch"), deuterocanonical=True),
    # --- Księgi mądrościowe ---
    BookSpec("Job", 22, "OT", "Hi", "Księga Hioba", ("Hiob", "Job"), oshb_file="Job", en_name="Job"),
    BookSpec("Ps", 23, "OT", "Ps", "Księga Psalmów", ("Psalm", "Psalmy", "Psalmów"), oshb_file="Ps", en_name="Psalms"),
    BookSpec("Prov", 24, "OT", "Prz", "Księga Przysłów", ("Przyp", "Przysł", "Prov"), oshb_file="Prov", en_name="Proverbs"),
    BookSpec("Eccl", 25, "OT", "Koh", "Księga Koheleta", ("Kazn", "Ekl", "Ecc", "Qoh"), oshb_file="Eccl", en_name="Ecclesiastes"),
    BookSpec("Song", 26, "OT", "Pnp", "Pieśń nad Pieśniami", ("PnP", "Pnp.", "Cant"), oshb_file="Song", en_name="Song of Solomon"),
    BookSpec("Wis", 27, "OT", "Mdr", "Księga Mądrości", ("Mądr", "Sap"), deuterocanonical=True),
    BookSpec("Sir", 28, "OT", "Syr", "Mądrość Syracha", ("Syrach", "Ekli", "Eccli"), deuterocanonical=True),
    # --- Prorocy ---
    BookSpec("Isa", 29, "OT", "Iz", "Księga Izajasza", ("Izaj", "Is"), oshb_file="Isa", en_name="Isaiah"),
    BookSpec("Jer", 30, "OT", "Jr", "Księga Jeremiasza", ("Jer", "Jerem"), oshb_file="Jer", en_name="Jeremiah"),
    BookSpec("Lam", 31, "OT", "Lm", "Lamentacje", ("Lam", "Treny", "Tr"), oshb_file="Lam", en_name="Lamentations"),
    BookSpec("Bar", 32, "OT", "Ba", "Księga Barucha", ("Bar",), deuterocanonical=True),
    BookSpec("Ezek", 33, "OT", "Ez", "Księga Ezechiela", ("Ezech", "Ezk"), oshb_file="Ezek", en_name="Ezekiel"),
    BookSpec("Dan", 34, "OT", "Dn", "Księga Daniela", ("Dan", "Da"), oshb_file="Dan", en_name="Daniel"),
    BookSpec("Hos", 35, "OT", "Oz", "Księga Ozeasza", ("Oze", "Hos"), oshb_file="Hos", en_name="Hosea"),
    BookSpec("Joel", 36, "OT", "Jl", "Księga Joela", ("Joel",), oshb_file="Joel", en_name="Joel"),
    BookSpec("Amos", 37, "OT", "Am", "Księga Amosa", ("Amos",), oshb_file="Amos", en_name="Amos"),
    BookSpec("Obad", 38, "OT", "Ab", "Księga Abdiasza", ("Abd", "Ob"), oshb_file="Obad", en_name="Obadiah"),
    BookSpec("Jonah", 39, "OT", "Jon", "Księga Jonasza", ("Jn.",), oshb_file="Jonah", en_name="Jonah"),
    BookSpec("Mic", 40, "OT", "Mi", "Księga Micheasza", ("Mich", "Mic"), oshb_file="Mic", en_name="Micah"),
    BookSpec("Nah", 41, "OT", "Na", "Księga Nahuma", ("Nah", "Nahum"), oshb_file="Nah", en_name="Nahum"),
    BookSpec("Hab", 42, "OT", "Ha", "Księga Habakuka", ("Hab", "Habakuk"), oshb_file="Hab", en_name="Habakkuk"),
    BookSpec("Zeph", 43, "OT", "So", "Księga Sofoniasza", ("Sof", "Sofon"), oshb_file="Zeph", en_name="Zephaniah"),
    BookSpec("Hag", 44, "OT", "Ag", "Księga Aggeusza", ("Agg", "Hag"), oshb_file="Hag", en_name="Haggai"),
    BookSpec("Zech", 45, "OT", "Za", "Księga Zachariasza", ("Zach", "Zch"), oshb_file="Zech", en_name="Zechariah"),
    BookSpec("Mal", 46, "OT", "Ml", "Księga Malachiasza", ("Mal", "Malach"), oshb_file="Mal", en_name="Malachi"),
    # --- Nowy Testament ---
    BookSpec("Matt", 47, "NT", "Mt", "Ewangelia według św. Mateusza", ("Mat", "Mateusz", "Mt."), morphgnt_code="01", en_name="Matthew"),
    BookSpec("Mark", 48, "NT", "Mk", "Ewangelia według św. Marka", ("Mar", "Mr", "Marek"), morphgnt_code="02", en_name="Mark"),
    BookSpec("Luke", 49, "NT", "Łk", "Ewangelia według św. Łukasza", ("Łuk", "Lk", "Luk", "Łukasz"), morphgnt_code="03", en_name="Luke"),
    BookSpec("John", 50, "NT", "J", "Ewangelia według św. Jana", ("Jan", "Jn", "Ja"), morphgnt_code="04", en_name="John"),
    BookSpec("Acts", 51, "NT", "Dz", "Dzieje Apostolskie", ("Dz Ap", "DzAp", "Dzieje", "Act", "Ac"), morphgnt_code="05", en_name="Acts"),
    BookSpec("Rom", 52, "NT", "Rz", "List do Rzymian", ("Rzym", "Rom", "Ro"), morphgnt_code="06", en_name="Romans"),
    BookSpec("1Cor", 53, "NT", "1 Kor", "1 List do Koryntian", ("1Kor", "I Kor", "1 Ko", "1Ko", "1 Cor"), morphgnt_code="07", en_name="1 Corinthians"),
    BookSpec("2Cor", 54, "NT", "2 Kor", "2 List do Koryntian", ("2Kor", "II Kor", "2 Ko", "2Ko", "2 Cor"), morphgnt_code="08", en_name="2 Corinthians"),
    BookSpec("Gal", 55, "NT", "Ga", "List do Galatów", ("Gal", "Galat"), morphgnt_code="09", en_name="Galatians"),
    BookSpec("Eph", 56, "NT", "Ef", "List do Efezjan", ("Efez", "Eph"), morphgnt_code="10", en_name="Ephesians"),
    BookSpec("Phil", 57, "NT", "Flp", "List do Filipian", ("Fil", "Filip", "Php", "Phil"), morphgnt_code="11", en_name="Philippians"),
    BookSpec("Col", 58, "NT", "Kol", "List do Kolosan", ("Col", "Kolos"), morphgnt_code="12", en_name="Colossians"),
    BookSpec("1Thess", 59, "NT", "1 Tes", "1 List do Tesaloniczan", ("1Tes", "I Tes", "1 Tess", "1 Th", "1Th"), morphgnt_code="13", en_name="1 Thessalonians"),
    BookSpec("2Thess", 60, "NT", "2 Tes", "2 List do Tesaloniczan", ("2Tes", "II Tes", "2 Tess", "2 Th", "2Th"), morphgnt_code="14", en_name="2 Thessalonians"),
    BookSpec("1Tim", 61, "NT", "1 Tm", "1 List do Tymoteusza", ("1Tm", "1 Tym", "1Tym", "I Tm", "1 Ti"), morphgnt_code="15", en_name="1 Timothy"),
    BookSpec("2Tim", 62, "NT", "2 Tm", "2 List do Tymoteusza", ("2Tm", "2 Tym", "2Tym", "II Tm", "2 Ti"), morphgnt_code="16", en_name="2 Timothy"),
    BookSpec("Titus", 63, "NT", "Tt", "List do Tytusa", ("Tyt", "Tit"), morphgnt_code="17", en_name="Titus"),
    BookSpec("Phlm", 64, "NT", "Flm", "List do Filemona", ("Filem", "Phm", "Flm."), morphgnt_code="18", en_name="Philemon"),
    BookSpec("Heb", 65, "NT", "Hbr", "List do Hebrajczyków", ("Hebr", "Heb", "Żyd"), morphgnt_code="19", en_name="Hebrews"),
    BookSpec("Jas", 66, "NT", "Jk", "List św. Jakuba", ("Jak", "Jas", "Jkb"), morphgnt_code="20", en_name="James"),
    BookSpec("1Pet", 67, "NT", "1 P", "1 List św. Piotra", ("1P", "1 Pt", "1Pt", "1 Piotra", "I P", "1 Pe"), morphgnt_code="21", en_name="1 Peter"),
    BookSpec("2Pet", 68, "NT", "2 P", "2 List św. Piotra", ("2P", "2 Pt", "2Pt", "2 Piotra", "II P", "2 Pe"), morphgnt_code="22", en_name="2 Peter"),
    BookSpec("1John", 69, "NT", "1 J", "1 List św. Jana", ("1J", "1 Jn", "1Jn", "1 Jana", "I J"), morphgnt_code="23", en_name="1 John"),
    BookSpec("2John", 70, "NT", "2 J", "2 List św. Jana", ("2J", "2 Jn", "2Jn", "2 Jana", "II J"), morphgnt_code="24", en_name="2 John"),
    BookSpec("3John", 71, "NT", "3 J", "3 List św. Jana", ("3J", "3 Jn", "3Jn", "3 Jana", "III J"), morphgnt_code="25", en_name="3 John"),
    BookSpec("Jude", 72, "NT", "Jud", "List św. Judy", ("Judy", "Jd"), morphgnt_code="26", en_name="Jude"),
    BookSpec("Rev", 73, "NT", "Ap", "Apokalipsa św. Jana", ("Obj", "Apok", "Rev", "Re"), morphgnt_code="27", en_name="Revelation"),
]
# fmt: on

BY_OSIS: dict[str, BookSpec] = {b.osis: b for b in BOOKS}
BY_ORDER: dict[int, BookSpec] = {b.order: b for b in BOOKS}
BY_MORPHGNT: dict[str, BookSpec] = {
    b.morphgnt_code: b for b in BOOKS if b.morphgnt_code
}
BY_EN_NAME: dict[str, BookSpec] = {b.en_name: b for b in BOOKS if b.en_name}


def _norm_key(s: str) -> str:
    """Klucz porównawczy skrótu: małe litery, bez kropek i spacji."""
    return s.lower().replace(".", "").replace(" ", "").replace("\u00a0", "")


# Mapa: znormalizowany skrót/alias -> BookSpec. Budowana raz przy imporcie.
_ABBR_INDEX: dict[str, BookSpec] = {}
for _b in BOOKS:
    for _key in (_b.abbr, _b.osis, _b.name_pl, *_b.aliases):
        _ABBR_INDEX.setdefault(_norm_key(_key), _b)


def lookup(abbr: str) -> BookSpec | None:
    """Rozpoznaj księgę po skrócie w dowolnej z obsługiwanych konwencji."""
    return _ABBR_INDEX.get(_norm_key(abbr))


def all_abbrs_sorted() -> list[str]:
    """Wszystkie skróty, najdłuższe najpierw — do budowy wyrażeń regularnych."""
    keys: set[str] = set()
    for b in BOOKS:
        keys.update((b.abbr, *b.aliases))
    return sorted(keys, key=len, reverse=True)


# Aliasy, które nie mają być używane przy skanowaniu wolnego tekstu (za krótkie
# lub kolidujące ze zwykłymi słowami); pozostają dostępne w lookup().
FREE_TEXT_EXCLUDE = frozenset(
    {
        "Ja",
        "Jn.",
        "Jud.",
        "Sdz.",
        "Pnp.",
        "Flm.",
        "Mt.",
        "Da",
        "Ro",
        "Ac",
        "Re",
        "Ob",
        "Is",
        "Tr",
        "Ex",
        "Lv",
        "Nm",
        "Dt",
        "Gn",
    }
)


@dataclass
class ParsedRef:
    """Wynik parsowania pojedynczego siglum (może obejmować zakres)."""

    book: BookSpec
    chapter: int
    verse_start: int | None = None  # None = cały rozdział
    chapter_end: int | None = None  # ustawione tylko przy zakresie międzyrozdziałowym
    verse_end: int | None = None  # None przy całym rozdziale; -1 = do końca rozdziału
    raw: str = ""
    extras: list[str] = field(default_factory=list)

    @property
    def osis_ref(self) -> str:
        """Zapis w stylu OSIS, np. Mark.1.1-Mark.1.8."""
        start = f"{self.book.osis}.{self.chapter}"
        if self.verse_start is not None:
            start += f".{self.verse_start}"
        end_ch = self.chapter_end or self.chapter
        if self.verse_end is None and self.chapter_end is None:
            return start
        end = f"{self.book.osis}.{end_ch}"
        if self.verse_end is not None and self.verse_end != -1:
            end += f".{self.verse_end}"
        return f"{start}-{end}" if end != start else start
