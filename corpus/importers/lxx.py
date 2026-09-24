"""Import Septuaginty (Rahlfs 1935) z repozytorium Elirana Wonga (CC BY-NC-SA 4.0).

Źródło: https://github.com/eliranwong/LXX-Rahlfs-1935
  11_end-users_files/MyBible/Bibles/LXX_final_main.csv — werset per linia (numer księgi
      MyBible, rozdział, werset, tekst ze znacznikami):
      słowo<S>lexid</S><m>lxx.MORF</m>[<S>strong</S>][<S>ext</S>]
  02_lexemes/Lex_LXXno.csv + 02_lexemes/OSSP_lexemes.csv — per token: lexid i lemat
      (z tego budujemy mapę lexid -> lemat; 14 176 lematów, jednoznaczne)

Uwaga licencyjna: tekst bazowy pochodzi z CCAT/CATSS (UPenn) — pobranie wymaga wysłania
deklaracji użytkownika do CATSS (link w README repozytorium). Użytek niekomercyjny.

Wersyfikacja: Rahlfs. Psalmy przeliczone na numerację masorecką (tabela niżej); pozostałe
księgi 1:1 (Jr, Wj 36–40, Prz, Hi mają w LXX inną kolejność/zakres — na razie bez mapowania,
Work.versification = "lxx" to sygnalizuje).
"""

import re
from collections.abc import Iterator
from pathlib import Path

from corpus.importers.base import TokenRecord, VerseRecord

# numer księgi MyBible -> OSIS; None = poza kanonem katolickim (pomijane)
BOOK_MAP: dict[int, str | None] = {
    10: "Gen", 20: "Exod", 30: "Lev", 40: "Num", 50: "Deut", 60: "Josh", 70: "Judg", 80: "Ruth",
    90: "1Sam", 100: "2Sam", 110: "1Kgs", 120: "2Kgs", 130: "1Chr", 140: "2Chr", 150: "Ezra",
    160: "Neh", 165: None, 170: "Tob", 180: "Jdt", 190: "Esth", 220: "Job", 230: "Ps", 232: None,
    240: "Prov", 250: "Eccl", 260: "Song", 270: "Wis", 280: "Sir", 290: "Isa", 300: "Jer",
    310: "Lam", 315: "Bar", 320: "Bar", 325: "Dan", 330: "Ezek", 340: "Dan", 345: "Dan",
    350: "Hos", 360: "Joel", 370: "Amos", 380: "Obad", 390: "Jonah", 400: "Mic", 410: "Nah",
    420: "Hab", 430: "Zeph", 440: "Hag", 450: "Zech", 460: "Mal", 462: "1Macc", 464: "2Macc",
    466: None, 467: None, 800: None,
}  # fmt: skip
# części ksiąg zapisane osobno w Rahlfsie -> rozdział w kanonie katolickim
CHAPTER_OVERRIDE = {
    315: 6,
    325: 13,
    345: 14,
}  # List Jeremiasza = Ba 6, Zuzanna = Dn 13, Bel = Dn 14

_TOKEN_RE = re.compile(
    r"(?P<word>\S+?)<S>(?P<lex>\d{6})</S><m>(?P<morph>[^<]+)</m>(?P<rest>(?:<S>\d+</S>)*)"
)
_S_RE = re.compile(r"<S>(\d+)</S>")


def psalm_to_mt(chapter: int, verse: int) -> tuple[int, int] | None:
    """Numeracja Psalmów LXX -> masorecka (rozdział, werset); None dla Ps 151."""
    if chapter <= 8 or 148 <= chapter <= 150:
        return chapter, verse
    if chapter == 9:
        return (9, verse) if verse <= 21 else (10, verse - 21)
    if 10 <= chapter <= 112:
        return chapter + 1, verse
    if chapter == 113:
        return (114, verse) if verse <= 8 else (115, verse - 8)
    if chapter == 114:
        return 116, verse
    if chapter == 115:
        return 116, verse + 9
    if 116 <= chapter <= 145:
        return chapter + 1, verse
    if chapter == 146:
        return 147, verse
    if chapter == 147:
        return 147, verse + 11
    return None


def load_lexmap(lexno_path: Path, lexemes_path: Path) -> dict[str, str]:
    lexmap: dict[str, str] = {}
    with (
        lexno_path.open(encoding="utf-8") as fa,
        lexemes_path.open(encoding="utf-8") as fb,
    ):
        for la, lb in zip(fa, fb, strict=False):
            a, b = la.rstrip("\n").split("\t"), lb.rstrip("\n").split("\t")
            if len(a) > 1 and len(b) > 1:
                lexmap.setdefault(a[1], b[1])
    return lexmap


def parse_tokens(text: str, lexmap: dict[str, str]) -> list[TokenRecord]:
    tokens: list[TokenRecord] = []
    for m in _TOKEN_RE.finditer(text):
        morph = m.group("morph").removeprefix("lxx.")
        strong = ""
        for num in _S_RE.findall(m.group("rest")):
            if int(num) < 10000:  # Strong grecki; 7xxxx to numeracja rozszerzona Wonga
                strong = f"G{int(num)}"
                break
        tokens.append(
            TokenRecord(
                surface=m.group("word"),
                lemma=lexmap.get(m.group("lex"), ""),
                strong=strong,
                morph=morph,
                pos=morph.split(".")[0],
            )
        )
    return tokens


def parse_file(
    path: Path, lexmap: dict[str, str], only: set[str] | None = None
) -> Iterator[VerseRecord]:
    seen: set[tuple[str, int, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        book_no, chapter, verse, text = (
            int(parts[0]),
            int(parts[1]),
            int(parts[2]),
            parts[3],
        )
        osis = BOOK_MAP.get(book_no)
        if not osis or (only and osis not in only):
            continue
        if book_no in CHAPTER_OVERRIDE:
            if chapter != 1:  # te części mają w Rahlfsie jeden rozdział
                continue
            chapter = CHAPTER_OVERRIDE[book_no]
        if osis == "Ps":
            mapped = psalm_to_mt(chapter, verse)
            if mapped is None:
                continue
            chapter, verse = mapped
        key = (osis, chapter, verse)
        if key in seen:  # np. Bar 6 obok EpJer; bierzemy pierwsze wystąpienie
            continue
        seen.add(key)
        tokens = parse_tokens(text, lexmap)
        if not tokens:
            continue
        yield VerseRecord(
            osis=osis,
            chapter=chapter,
            verse=verse,
            text=" ".join(t.surface for t in tokens),
            tokens=tokens,
        )
