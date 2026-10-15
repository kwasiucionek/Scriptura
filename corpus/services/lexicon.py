"""Strona hasła słownika (/text/slownik/<Strong>/): leksykon STEP + Strong + użycie w tekście.

Hasło = Lexeme (STEP TBESH/TBESG; numer Stronga rozszerzony o literę homografu, np. H1254a).
Do tego: polskie odpowiedniki (przekład roboczy), glosy z interlinii z liczbą użyć, formy
w tekście z morfologią po polsku, rozkład po dziełach i księgach, wystąpienia (stronicowane)
i fragmenty literatury, w których słowo pada.
"""

import re
from dataclasses import dataclass, field

from django.core.paginator import Page, Paginator
from django.db.models import Count, F, Q

from corpus.models import Lexeme, Token
from corpus.morph_pl import describe
from corpus.normalize import normalize, normalize_greek, normalize_hebrew
from corpus.translit import canonical_translit, greek_translit

OCCURRENCES_PER_PAGE = 50
LANGUAGE_LABELS = {"grc": "grecki", "hbo": "hebrajski", "arc": "aramejski"}
# STEP: „G:N-M”, „H:V”, „A:N-F” (aramejski) — część mowy i rodzaj
STEP_POS = {
    "N": "rzeczownik", "V": "czasownik", "A": "przymiotnik", "Adv": "przysłówek",
    "Prep": "przyimek", "Conj": "spójnik", "Prt": "partykuła", "Art": "rodzajnik",
    "R": "zaimek", "P": "zaimek", "Intj": "wykrzyknik", "Int": "wykrzyknik",
    "D": "przysłówek", "Neg": "partykuła przecząca", "Pron": "zaimek",
}  # fmt: skip
STEP_GENDER = {"M": "rodzaj męski", "F": "rodzaj żeński", "N": "rodzaj nijaki"}
STEP_NAME = {
    "N-M-P": "imię własne (osoba)",
    "N-F-P": "imię własne (osoba)",
    "N-L": "nazwa miejsca",
}

_STRONG_RE = re.compile(r"^([GH])0*(\d{1,5})([a-z]?)$", re.IGNORECASE)
_GREEK_WORD = re.compile("[\u0370-\u03ff\u1f00-\u1fff]+")
_HEBREW_WORD = re.compile("[\u0590-\u05ff]+")
_PUNCT = re.compile(
    "^[^\\w\u0590-\u05ff]+|[^\\w\u0590-\u05ff]+$"
)  # interpunkcja wokół formy


def normalize_strong(raw: str) -> str:
    """g725 / G0725 -> G725; h1254A -> H1254a."""
    m = _STRONG_RE.match(raw.strip())
    if not m:
        return ""
    return f"{m.group(1).upper()}{int(m.group(2))}{m.group(3).lower()}"


def find_lexeme(strong: str) -> Lexeme | None:
    """Hasło po numerze; numer bez litery homografu trafia w pierwszy wariant (H1254 -> H1254a)."""
    strong = normalize_strong(strong)
    if not strong:
        return None
    lex = Lexeme.objects.filter(strong=strong).first()
    if lex is None and strong[-1].isdigit():
        lex = (
            Lexeme.objects.filter(strong__regex=rf"^{strong}[a-z]$")
            .order_by("strong")
            .first()
        )
    return lex


def step_pos_label(code: str) -> str:
    """„G:N-M” -> „rzeczownik, rodzaj męski”; nieznany kod -> pusty."""
    body = code.split(":", 1)[-1]
    if body in STEP_NAME or body.startswith("N-") and body.endswith("-P"):
        return STEP_NAME.get(body, "imię własne")
    if body.startswith("N-L"):
        return "nazwa miejsca"
    parts = body.split("-")
    pos = STEP_POS.get(parts[0], "")
    gender = STEP_GENDER.get(parts[1], "") if len(parts) > 1 else ""
    return ", ".join(p for p in (pos, gender) if p)


def translit_of(lex: Lexeme) -> str:
    if lex.language == "grc":
        return greek_translit(lex.lemma)
    return lex.transliteration.replace(".", "")


@dataclass
class LexiconEntry:
    lexeme: Lexeme
    language_label: str
    pos_label: str
    translit: str
    gloss_pl: list[str]
    total: int = 0
    by_work: list[dict] = field(default_factory=list)  # [{"code", "n"}]
    by_book: list[dict] = field(default_factory=list)  # [{"abbr", "n"}]
    interlinear: list[dict] = field(default_factory=list)  # [{"gloss", "n"}]
    forms: list[dict] = field(
        default_factory=list
    )  # [{"surface", "morph", "morph_full", "n"}]
    page: Page | None = None
    literature: list = field(default_factory=list)

    @property
    def work_codes(self) -> list[str]:
        return [w["code"] for w in self.by_work]


def occurrences(lex: Lexeme, access: list[str] | None):
    from corpus.services.text import work_access_q

    return Token.objects.filter(
        work_access_q(access, "verse_text__work__"), strong=lex.strong
    ).select_related("verse_text__verse__book", "verse_text__work")


def build_entry(
    lex: Lexeme,
    access: list[str] | None,
    page_number: int = 1,
    literature_access: list[str] | None = None,
    user_id: int | None = None,
    with_literature: bool = True,
) -> LexiconEntry:
    entry = LexiconEntry(
        lexeme=lex,
        language_label=LANGUAGE_LABELS.get(lex.language, lex.language),
        pos_label=step_pos_label(lex.morph),
        translit=translit_of(lex),
        gloss_pl=[g.strip() for g in lex.gloss_pl.split(",") if g.strip()],
    )
    qs = occurrences(lex, access)
    entry.by_work = list(
        qs.values(code=F("verse_text__work__code"))
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    entry.total = sum(r["n"] for r in entry.by_work)
    entry.by_book = list(
        qs.values(
            abbr=F("verse_text__verse__book__abbr"),
            order=F("verse_text__verse__book__order"),
        )
        .annotate(n=Count("id"))
        .order_by("order")
    )
    entry.interlinear = list(
        qs.exclude(gloss_pl="")
        .exclude(gloss_pl="-")
        .values(gloss=F("gloss_pl"))
        .annotate(n=Count("id"))
        .order_by("-n", "gloss")[:20]
    )
    forms = (
        qs.values("surface_norm", "surface", "morph", "pos")
        .annotate(n=Count("id"))
        .order_by("-n")[:200]
    )
    merged: dict[tuple[str, str], dict] = {}
    for f in forms:  # ta sama forma z interpunkcją („θεῷ,”) liczy się raz
        surface = _PUNCT.sub("", f["surface"])
        key = (f["surface_norm"], f["morph"])
        if key in merged:
            merged[key]["n"] += f["n"]
            continue
        short, full = describe(lex.language, f["pos"], f["morph"])
        merged[key] = {
            "surface": surface,
            "translit": greek_translit(surface) if lex.language == "grc" else "",
            "morph": short or f["morph"],
            "morph_full": full,
            "n": f["n"],
        }
    entry.forms = sorted(merged.values(), key=lambda f: -f["n"])[:30]
    entry.page = Paginator(
        qs.order_by("verse_text__verse__ordinal", "position"), OCCURRENCES_PER_PAGE
    ).get_page(page_number)
    if with_literature:
        entry.literature = literature(lex, merged.values(), literature_access, user_id)
    return entry


def literature(lex: Lexeme, forms, access, user_id) -> list:
    """Fragmenty literatury z lematem, formą z tekstu albo transliteracją; z wycinkiem wokół słowa."""
    from library.search import term_mentions

    words = [lex.lemma] + [f["surface"] for f in forms]
    grc = words if lex.language == "grc" else []
    hbo = words if lex.language in ("hbo", "arc") else []
    translits = [t for t in {translit_of(lex)} if t]
    try:
        hits = term_mentions(grc, hbo, translits, k=8, access=access, user_id=user_id)
    except Exception:  # noqa: BLE001 — brak indeksu nie psuje strony hasła
        return []
    norm = normalize_greek if lex.language == "grc" else normalize_hebrew
    targets = {norm(w) for w in words}
    for h in hits:
        h.snippet = snippet(h.text, targets, norm, translits)
    return hits


def snippet(
    text: str, targets: set[str], norm, translits: list[str], width: int = 220
) -> str:
    pattern = _GREEK_WORD if norm is normalize_greek else _HEBREW_WORD
    pos = -1
    for m in pattern.finditer(text):
        if norm(m.group(0)) in targets:
            pos = m.start()
            break
    if pos < 0:
        low = canonical_translit_text(text)
        for t in translits:
            i = low.find(t.lower())
            if i >= 0:
                pos = i
                break
    if pos < 0:
        pos = 0
    start = max(0, pos - width // 2)
    end = min(len(text), pos + width)
    out = text[start:end].strip()
    return ("… " if start else "") + out + (" …" if end < len(text) else "")


def canonical_translit_text(text: str) -> str:
    """Tekst do szukania transliteracji: małe litery bez diakrytyków, ta sama długość."""
    import unicodedata

    return "".join((unicodedata.normalize("NFKD", c)[:1] or c).lower() for c in text)


def search(q: str, limit: int = 60) -> list[Lexeme]:
    """Hasła po numerze Stronga, lemacie (z akcentami lub bez), transliteracji albo polskim
    lub angielskim znaczeniu."""
    q = q.strip()
    if not q:
        return []
    if strong := normalize_strong(q):
        lex = find_lexeme(strong)
        return [lex] if lex else []
    if _GREEK_WORD.search(q):
        return list(Lexeme.objects.filter(lemma_norm=normalize(q, "grc"))[:limit])
    if _HEBREW_WORD.search(q):
        return list(Lexeme.objects.filter(lemma_norm=normalize(q, "hbo"))[:limit])
    fold = canonical_translit(q)
    cond = Q(gloss_pl__icontains=q) | Q(gloss__icontains=q)
    if len(fold) >= 3:
        cond |= Q(translit_fold=fold)
    exact = list(Lexeme.objects.filter(translit_fold=fold)[:limit]) if fold else []
    rest = Lexeme.objects.filter(cond).exclude(id__in=[x.id for x in exact])
    return exact + list(rest.order_by("strong")[: max(0, limit - len(exact))])
