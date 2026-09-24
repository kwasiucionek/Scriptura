"""Sigla mówione (transkrypcje wykładów) -> kanoniczne, dopisane w nawiasie.

„Powtórzonego Prawa 32 8” -> „Powtórzonego Prawa 32 8 (Pwt 32,8)”, „piąta Księga Mojżeszowa
32 wers 8 do 9” -> „… (Pwt 32,8-9)”, „Ewangelia Marka rozdział 16 werset 8” -> „… (Mk 16,8)”,
„pierwszy list do Koryntian 15 3” -> „… (1 Kor 15,3)”, „Psalm 82 werset 6” -> „… (Ps 82,6)”.
Parser sigli (corpus.sigla) zostaje ostry — normalizacja dotyczy tylko tekstu z mowy.
"""

import re
import unicodedata

from corpus.books import BOOKS

_ORD = {"pierwsz": 1, "drug": 2, "trzeci": 3, "czwart": 4, "piąt": 5, "piat": 5}
_MOJZ = {1: "Rdz", 2: "Wj", 3: "Kpł", 4: "Lb", 5: "Pwt"}


def _fold(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _spoken_names() -> list[tuple[str, str]]:
    """(wzorzec nazwy w mowie, skrót) — dłuższe nazwy pierwsze."""
    out: list[tuple[str, str]] = []
    for b in BOOKS:
        name = b.name_pl
        num = ""
        m = re.match(r"^([1-3])\s+(.*)$", name)
        if m:
            num, name = m.group(1), m.group(2)
        core = re.sub(
            r"^(Księga|List do|List|Ewangelia według św\.|Apokalipsa św\.|Mądrość)\s+",
            "",
            name,
        ).strip()
        forms = {core}
        if name.startswith("Ewangelia"):
            forms |= {
                f"Ewangelia (?:według |wg )?(?:świętego |św\\.? )?{core}",
                f"Ewangelii (?:według |wg )?(?:świętego |św\\.? )?{core}",
            }
        if name.startswith("List do"):
            forms |= {f"List(?:u|em|ie)? do {core}"}
        if name.startswith("Księga"):
            forms |= {f"Księg[aięo]\\w* {core}"}
        if b.abbr == "Ps":
            forms |= {"Psalm(?:u|ie|ów|y)?"}
        if b.abbr == "Ap":
            forms |= {"Apokalips[aiey]"}
        if b.abbr == "Dz":
            forms |= {"Dziej(?:e|ach|ów) Apostolskich?", "Dziej(?:e|ach|ów)"}
        prefix = ""
        if num:
            words = {"1": "pierwsz\\w+", "2": "drug\\w+", "3": "trzeci\\w*"}[num]
            prefix = f"(?:{num}|{words})\\s+"
        for f in forms:
            out.append((prefix + f, b.abbr))
    out.sort(key=lambda p: -len(p[0]))
    return out


_NAMES = _spoken_names()
_NUM = r"\d{1,3}"
# ogon mówiony: „32 8”, „32 wers(et) 8”, „rozdział 32 werset 8 do 9”, „32,8”, „32 8-9”
_TAIL = re.compile(
    rf"(?:\s+rozdzia\w+)?\s+(?P<ch>{_NUM})(?:\s*,\s*|\s+(?:wers\w*|w\.)\s+|\s+)(?P<v1>{_NUM})"
    rf"(?:\s*(?:-|–|do)\s*(?P<v2>{_NUM}))?(?![\d,])"
)
_MOJZ_RE = re.compile(
    r"(?P<n>pierwsz\w+|drug\w+|trzeci\w*|czwart\w+|pi[ąa]t\w+|[1-5])\s+Księg[aięo]\w*\s+Mojżeszow\w+"
    + _TAIL.pattern,
    re.IGNORECASE,
)
_BOOK_RES = [
    (re.compile(r"(?<!\w)(?:" + pat + ")" + _TAIL.pattern, re.IGNORECASE), abbr)
    for pat, abbr in _NAMES
]


def _canon(abbr: str, m: re.Match) -> str:
    ch, v1, v2 = m.group("ch"), m.group("v1"), m.group("v2")
    return f"{abbr} {ch},{v1}" + (f"-{v2}" if v2 else "")


def normalize_spoken_sigla(text: str) -> str:
    """Dopisuje kanoniczne siglum w nawiasie po siglum mówionym (bez podwajania, gdy już jest)."""

    def add(m: re.Match, abbr: str) -> str:
        s = m.group(0)
        canon = _canon(abbr, m)
        rest = text[m.end() : m.end() + len(canon) + 4]
        return s if canon in rest else f"{s} ({canon})"

    def mojz(m: re.Match) -> str:
        n = m.group("n").lower()
        num = (
            int(n)
            if n.isdigit()
            else next((v for k, v in _ORD.items() if _fold(n).startswith(k)), 0)
        )
        return add(m, _MOJZ[num]) if num in _MOJZ else m.group(0)

    text = _MOJZ_RE.sub(mojz, text)
    for rx, abbr in _BOOK_RES:
        text = rx.sub(lambda m, a=abbr: add(m, a), text)
    return text
