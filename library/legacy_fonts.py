"""Dekodowanie hebrajskiego z fontu BibleWorks (Bwhebb) w PDF-ach sprzed ery Unicode.

Font rysował litery hebrajskie pod kodami ASCII; ekstrakcja tekstu daje ciąg klawiszy
w kolejności wizualnej od lewej. Reguły wyprowadzone empirycznie z tekstów Majewskiego:
- odwrócenie ciągu daje kolejność logiczną spółgłosek,
- samogłoski dolne (kamec, patach, segol, cere, chirek, kubuc, szewa, chatafy) stoją
  PRZED swoją spółgłoską, cholem zapisany jako 'A' stoi ZA nią,
- wielkie litery B G D K M P T = spółgłoska z dageszem, W = szurek (וּ).
Nieznane znaki przechodzą bez zmian.
"""

import re
import unicodedata

DAGESH = "\u05bc"

CONSONANTS = {
    "a": "א", "b": "ב", "g": "ג", "d": "ד", "h": "ה", "w": "ו", "z": "ז", "x": "ח", "j": "ט",
    "y": "י", "k": "כ", "$": "ך", "l": "ל", "m": "מ", "~": "ם", "n": "נ", "!": "ן", "s": "ס",
    "[": "ע", "p": "פ", "@": "ף", "c": "צ", "#": "ץ", "q": "ק", "r": "ר", "v": "שׁ", "f": "שׂ",
    "t": "ת",
    # z dageszem
    "B": "ב" + DAGESH, "G": "ג" + DAGESH, "D": "ד" + DAGESH, "K": "כ" + DAGESH,
    "M": "מ" + DAGESH, "P": "פ" + DAGESH, "T": "ת" + DAGESH, "W": "ו" + DAGESH,
}  # fmt: skip

# samogłoski stojące przed spółgłoską (po odwróceniu)
VOWELS_BEFORE = {
    ";": "\u05b7", ":": "\u05b7",  # patach
    ",": "\u05b6",  # segol
    "'": "\u05b8", '"': "\u05b8",  # kamec
    "e": "\u05b5", "E": "\u05b5",  # cere
    "i": "\u05b4", "I": "\u05b4",  # chirek
    "u": "\u05bb", "U": "\u05bb",  # kubuc
    ">": "\u05b0", ".": "\u05b0",  # szewa
    "]": "\u05b2",  # chataf patach
    "}": "\u05b1",  # chataf segol
    "{": "\u05b3",  # chataf kamec
    "o": "\u05b9", "O": "\u05b9",  # cholem (wariant rysowany z prawej)
}  # fmt: skip
VOWELS_AFTER = {"A": "\u05b9"}  # cholem rysowany z lewej -> w ekstrakcji za spółgłoską

FONT_RE = re.compile(r"bwhebb|bwhebl", re.I)


def decode_bwhebb(text: str) -> str:
    out: list[str] = []
    pending: list[str] = []
    for ch in reversed(text):
        if ch in CONSONANTS:
            out.append(CONSONANTS[ch])
            out.extend(pending)
            pending = []
        elif ch in VOWELS_BEFORE:
            pending.append(VOWELS_BEFORE[ch])
        elif ch in VOWELS_AFTER:
            if out and out[-1] and "\u05d0" <= out[-1][0] <= "\u05ea":
                out[-1] = out[-1] + VOWELS_AFTER[ch]
            else:
                pending.append(VOWELS_AFTER[ch])
        elif ch.isspace():
            out.extend(pending)
            pending = []
            out.append(ch)
        else:
            out.append(ch)
    out.extend(pending)
    # NFC porządkuje znaki łączące (dagesz, punkty szin/sin, samogłoski) kanonicznie
    return unicodedata.normalize("NFC", "".join(out))


def is_legacy_hebrew_font(font_name: str) -> bool:
    return bool(FONT_RE.search(font_name))
