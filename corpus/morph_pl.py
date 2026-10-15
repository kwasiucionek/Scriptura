"""Polski opis kodów morfologicznych: MorphGNT (SBLGNT) i OSHM (Open Scriptures Hebrew Morphology, WLC).

MorphGNT: część mowy (2 znaki, Token.pos) + kod 8-znakowy (Token.morph):
osoba, czas, strona, tryb, przypadek, liczba, rodzaj, stopień; „-” = nie dotyczy.
OSHM: „H” (hebrajski) / „A” (aramejski), potem segmenty rozdzielone „/” (przedrostki, rdzeń,
przyrostki), np. HR/Ncfsa = przyimek + rzeczownik pospolity, ż., l.poj., stan absolutny.
Funkcje zwracają (skrót, pełny opis); nieznane znaki są pomijane, nigdy nie podnoszą wyjątku.
"""

GNT_POS = {
    "N-": "rzeczownik", "V-": "czasownik", "A-": "przymiotnik", "D-": "przysłówek",
    "C-": "spójnik", "P-": "przyimek", "X-": "partykuła", "I-": "wykrzyknik",
    "RA": "rodzajnik", "RP": "zaimek osobowy", "RD": "zaimek wskazujący",
    "RR": "zaimek względny", "RI": "zaimek pytajny/nieokreślony",
}  # fmt: skip
GNT_SHORT_POS = {
    "N-": "rz.", "V-": "cz.", "A-": "przym.", "D-": "przysł.", "C-": "spój.", "P-": "przyim.",
    "X-": "part.", "I-": "wykrz.", "RA": "rodz.", "RP": "zaim.", "RD": "zaim. wsk.",
    "RR": "zaim. wzgl.", "RI": "zaim. pyt.",
}  # fmt: skip
GNT_TENSE = {
    "P": ("praes.", "czas teraźniejszy"), "I": ("impf.", "imperfectum"),
    "F": ("fut.", "czas przyszły"), "A": ("aor.", "aoryst"), "X": ("perf.", "perfectum"),
    "Y": ("plqpf.", "plusquamperfectum"),
}  # fmt: skip
GNT_VOICE = {
    "A": ("akt.", "strona czynna"), "M": ("med.", "strona medialna"),
    "P": ("pass.", "strona bierna"),
}  # fmt: skip
GNT_MOOD = {
    "I": ("ind.", "tryb oznajmujący"), "D": ("imp.", "tryb rozkazujący"),
    "S": ("koni.", "tryb łączący (coniunctivus)"), "O": ("opt.", "tryb życzący (optativus)"),
    "N": ("bezok.", "bezokolicznik"), "P": ("imiesł.", "imiesłów"),
}  # fmt: skip
CASE = {
    "N": ("M.", "mianownik"), "G": ("D.", "dopełniacz"), "D": ("C.", "celownik"),
    "A": ("B.", "biernik"), "V": ("W.", "wołacz"),
}  # fmt: skip
NUMBER = {
    "S": ("l.poj.", "liczba pojedyncza"), "P": ("l.mn.", "liczba mnoga"),
    "s": ("l.poj.", "liczba pojedyncza"), "p": ("l.mn.", "liczba mnoga"),
    "d": ("l.podw.", "liczba podwójna"),
}  # fmt: skip
GENDER = {
    "M": ("m.", "rodzaj męski"), "F": ("ż.", "rodzaj żeński"), "N": ("n.", "rodzaj nijaki"),
    "m": ("m.", "rodzaj męski"), "f": ("ż.", "rodzaj żeński"), "c": ("wsp.", "rodzaj wspólny"),
    "b": ("m./ż.", "rodzaj męski lub żeński"),
}  # fmt: skip
DEGREE = {
    "C": ("st. wyższy", "stopień wyższy"),
    "S": ("st. najw.", "stopień najwyższy"),
}
PERSON = {
    "1": ("1 os.", "1 osoba"),
    "2": ("2 os.", "2 osoba"),
    "3": ("3 os.", "3 osoba"),
}


def _join(pairs: list[tuple[str, str]]) -> tuple[str, str]:
    return " ".join(p[0] for p in pairs), ", ".join(p[1] for p in pairs)


def morphgnt(pos: str, code: str) -> tuple[str, str]:
    """('cz. aor. med. ind. 3 os. l.poj.', 'czasownik, aoryst, strona medialna, …')."""
    pairs: list[tuple[str, str]] = []
    if pos in GNT_POS:
        pairs.append((GNT_SHORT_POS[pos], GNT_POS[pos]))
    code = (code or "").ljust(8, "-")
    person, tense, voice, mood, case, number, gender, degree = code[:8]
    for table, key in ((GNT_TENSE, tense), (GNT_VOICE, voice), (GNT_MOOD, mood)):
        if key in table:
            pairs.append(table[key])
    for table, key in (
        (CASE, case),
        (PERSON, person),
        (NUMBER, number),
        (GENDER, gender),
        (DEGREE, degree),
    ):
        if key in table:
            pairs.append(table[key])
    return _join(pairs)


# --- OSHM ------------------------------------------------------------------------------------

HEB_STEMS = {
    "q": "Qal", "N": "Nifal", "p": "Piel", "P": "Pual", "h": "Hifil", "H": "Hofal",
    "t": "Hitpael", "o": "Polel", "O": "Polal", "r": "Hitpolel", "m": "Poel", "M": "Poal",
    "k": "Palel", "K": "Pulal", "Q": "Qal pass.", "l": "Pilpel", "L": "Polpal",
    "f": "Hitpalpel", "D": "Nitpael", "j": "Pealal", "i": "Pilel", "u": "Hotpaal",
    "c": "Tifil", "v": "Hisztafel", "w": "Nitpalel", "y": "Nitpoel", "z": "Hitpoel",
}  # fmt: skip
ARC_STEMS = {
    "q": "Peal", "Q": "Peil", "u": "Hitpeel", "p": "Pael", "P": "Itpaal", "M": "Hitpaal",
    "a": "Afel", "h": "Hafel", "s": "Safel", "e": "Szafel", "H": "Hofal", "i": "Itpeel",
    "t": "Hisztafel", "v": "Isztafel", "w": "Hitafel", "o": "Polel", "z": "Itpoel",
    "r": "Hitpolel", "f": "Hitpalpel", "b": "Hefal", "c": "Tifel", "m": "Poel",
    "l": "Palpel", "L": "Itpalpel", "O": "Itpolel", "G": "Ittafal",
}  # fmt: skip
VERB_TYPES = {
    "p": ("perf.", "perfectum (qatal)"), "q": ("weqatal", "perfectum z waw (weqatal)"),
    "i": ("impf.", "imperfectum (jiqtol)"), "w": ("wajjiqtol", "imperfectum z waw (wajjiqtol)"),
    "h": ("kohort.", "kohortatyw"), "j": ("juss.", "jussyw"), "v": ("rozk.", "tryb rozkazujący"),
    "r": ("imiesł. czyn.", "imiesłów czynny"), "s": ("imiesł. bier.", "imiesłów bierny"),
    "a": ("bezok. abs.", "bezokolicznik absolutny"), "c": ("bezok. konstr.", "bezokolicznik konstrukcyjny"),
}  # fmt: skip
STATE = {
    "a": ("abs.", "stan absolutny"), "c": ("konstr.", "stan konstrukcyjny (status constructus)"),
    "d": ("emf.", "stan określony (emfatyczny)"),
}  # fmt: skip
NOUN_TYPES = {
    "c": ("rz.", "rzeczownik pospolity"), "g": ("rz. etn.", "rzeczownik etniczny"),
    "p": ("im. wł.", "imię własne"), "x": ("rz.", "rzeczownik"),
}  # fmt: skip
PROPER_KIND = {
    "m": ("os. m.", "osoba (mężczyzna)"), "f": ("os. ż.", "osoba (kobieta)"),
    "l": ("miejsce", "nazwa miejsca"), "t": ("tytuł", "tytuł"),
}  # fmt: skip
ADJ_TYPES = {
    "a": ("przym.", "przymiotnik"), "c": ("licz.", "liczebnik główny"),
    "g": ("przym. etn.", "przymiotnik etniczny"), "o": ("licz. porz.", "liczebnik porządkowy"),
}  # fmt: skip
PRON_TYPES = {
    "d": ("zaim. wsk.", "zaimek wskazujący"), "f": ("zaim. nieokr.", "zaimek nieokreślony"),
    "i": ("zaim. pyt.", "zaimek pytajny"), "p": ("zaim. os.", "zaimek osobowy"),
    "r": ("zaim. wzgl.", "zaimek względny"),
}  # fmt: skip
SUFFIX_TYPES = {
    "d": ("he kier.", "he kierunkowe"), "h": ("he parag.", "he paragogiczne"),
    "n": ("nun parag.", "nun paragogiczne"), "p": ("przyr. zaim.", "przyrostek zaimkowy"),
}  # fmt: skip
PARTICLE_TYPES = {
    "a": ("part. potw.", "partykuła potwierdzająca"), "d": ("rodz.", "rodzajnik określony"),
    "e": ("part. zach.", "partykuła zachęty"), "i": ("part. pyt.", "partykuła pytajna"),
    "j": ("wykrz.", "wykrzyknik"), "m": ("part. wsk.", "partykuła wskazująca"),
    "n": ("przecz.", "partykuła przecząca"), "o": ("ozn. dop.", "znacznik dopełnienia (et)"),
    "r": ("part. wzgl.", "partykuła względna"),
}  # fmt: skip


def _pgn(rest: str) -> list[tuple[str, str]]:
    """Osoba / rodzaj / liczba / stan w dowolnej kolejności wg znaków."""
    out = []
    if rest[:1] in PERSON:
        out.append(PERSON[rest[0]])
        rest = rest[1:]
    if rest[:1] in GENDER:
        out.append(GENDER[rest[0]])
        rest = rest[1:]
    if rest[:1] in NUMBER:
        out.append(NUMBER[rest[0]])
        rest = rest[1:]
    if rest[:1] in STATE:
        out.append(STATE[rest[0]])
    return out


def _oshm_segment(seg: str, aramaic: bool) -> list[tuple[str, str]]:
    if not seg:
        return []
    pos, rest = seg[0], seg[1:]
    if pos == "V":
        stems = ARC_STEMS if aramaic else HEB_STEMS
        out = [("cz.", "czasownik")]
        if rest[:1] in stems:
            out.append((stems[rest[0]], f"koniugacja {stems[rest[0]]}"))
        if rest[1:2] in VERB_TYPES:
            out.append(VERB_TYPES[rest[1]])
        return out + _pgn(rest[2:])
    if pos == "N":
        if rest[:1] == "p":
            out = [NOUN_TYPES["p"]]
            if rest[1:2] in PROPER_KIND:
                out.append(PROPER_KIND[rest[1]])
            return out
        out = [NOUN_TYPES.get(rest[:1], ("rz.", "rzeczownik"))]
        return out + _pgn(rest[1:])
    if pos == "A":
        return [ADJ_TYPES.get(rest[:1], ("przym.", "przymiotnik"))] + _pgn(rest[1:])
    if pos == "P":
        return [PRON_TYPES.get(rest[:1], ("zaim.", "zaimek"))] + _pgn(rest[1:])
    if pos == "S":
        return [SUFFIX_TYPES.get(rest[:1], ("przyr.", "przyrostek"))] + _pgn(rest[1:])
    if pos == "T":
        return [PARTICLE_TYPES.get(rest[:1], ("part.", "partykuła"))]
    if pos == "R":
        if rest[:1] == "d":
            return [("przyim. + rodz.", "przyimek z rodzajnikiem")]
        return [("przyim.", "przyimek")]
    if pos == "C":
        return [("spój.", "spójnik")]
    if pos == "D":
        return [("przysł.", "przysłówek")]
    return []


def oshm(code: str) -> tuple[str, str]:
    """('przyim. + rz. ż. l.poj. abs.', 'przyimek + rzeczownik pospolity, rodzaj żeński, …')."""
    if not code:
        return "", ""
    aramaic = code[0] == "A"
    body = code[1:] if code[0] in "HA" else code
    segs = [_oshm_segment(s, aramaic) for s in body.split("/")]
    segs = [s for s in segs if s]
    short = " + ".join(" ".join(p[0] for p in s) for s in segs)
    full = " + ".join(", ".join(p[1] for p in s) for s in segs)
    if aramaic:
        short, full = f"aram. {short}", f"aramejski: {full}"
    return short, full


def describe(language: str, pos: str, code: str) -> tuple[str, str]:
    if language in ("hbo", "arc"):
        return oshm(code)
    if language == "grc" and pos:
        return morphgnt(pos, code)
    return "", ""
