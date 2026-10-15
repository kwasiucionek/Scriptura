"""Kanoniczna postać transliteracji hebrajskiej/greckiej — żeby „hesed”, „chesed”, „ḥesed”,
„che.sed” (STEP) trafiały w to samo hasło leksykonu. Zdejmuje diakrytyki i kropki sylabowe STEP,
ujednolica polskie i angielskie konwencje (sz/sh/š -> s, ch/kh/ḥ -> h, q -> k, ts/c -> c, w/v -> v,
th -> t, ph -> f), zbija podwojenia i końcowe h.
"""

import re
import unicodedata

_MAP = [
    ("sch", "s"), ("tsch", "c"), ("sh", "s"), ("sz", "s"), ("ch", "h"), ("kh", "h"), ("th", "t"),
    ("ph", "f"), ("ts", "c"), ("tz", "c"), ("q", "k"), ("w", "v"), ("y", "j"), ("x", "ks"),
]  # fmt: skip


def canonical_translit(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = (
        t.replace("ʾ", "")
        .replace("ʿ", "")
        .replace("'", "")
        .replace("’", "")
        .replace("ā", "a")
    )
    t = re.sub(r"[^a-z]", "", t)  # kropki STEP (che.sed), myślniki, cyfry
    for a, b in _MAP:
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)  # podwojenia (chesed/hessed)
    t = re.sub(
        r"h$", "", t
    )  # końcowe -h (torah/tora, ruach/rua… już zjedzone przez ch->h)
    return t


# --- transliteracja greki do wyświetlania (interlinia; konwencja SBL bez akcentów) ----------

_GRC_LETTERS = {
    "α": "a", "β": "b", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "ē", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "ph", "χ": "ch", "ψ": "ps",
    "ω": "ō",
}  # fmt: skip
_GRC_NASAL = {"γ", "κ", "ξ", "χ"}  # γ przed nimi = n (ἄγγελος -> angelos)
_GRC_VOWELS = set("αεηιουω")
_ROUGH, _DIAERESIS, _IOTA_SUB = "̔", "̈", "ͅ"


def greek_translit(word: str) -> str:
    """ἁρπαγμὸν -> harpagmon, Ἰησοῦ -> Iēsou, ῥῆμα -> rhēma, εὐαγγέλιον -> euangelion."""
    letters: list[
        tuple[str, set[str], bool]
    ] = []  # (litera, znaki diakrytyczne, wielka)
    for ch in unicodedata.normalize("NFD", word):
        if unicodedata.combining(ch):
            if letters:
                letters[-1][1].add(ch)
            continue
        low = ch.lower()
        if low in _GRC_LETTERS:
            letters.append((low, set(), ch != low))
    out: list[str] = []
    for i, (ch, marks, upper) in enumerate(letters):
        prev = letters[i - 1][0] if i else ""
        nxt = letters[i + 1][0] if i + 1 < len(letters) else ""
        if ch == "γ" and nxt in _GRC_NASAL:
            t = "n"
        elif (
            ch == "υ"
            and _DIAERESIS not in marks
            and (
                (prev and prev in "αεηο")
                or (nxt == "ι" and _DIAERESIS not in letters[i + 1][1])
            )
        ):
            t = "u"  # dyftongi au, eu, ēu, ou, ui
        elif ch == "ρ" and _ROUGH in marks:
            t = "rh"
        else:
            t = _GRC_LETTERS[ch]
        rough = _ROUGH in marks and ch in _GRC_VOWELS
        # przydech na drugiej samogłosce dyftongu (αἱ, εὑ, οὑ) dotyczy całej sylaby
        if rough and i and prev in _GRC_VOWELS and out:
            out[-1] = (
                ("H" + out[-1].lower()) if out[-1][:1].isupper() else "h" + out[-1]
            )
        elif rough:
            t = "h" + t
        if _IOTA_SUB in marks:
            t += "i"
        if upper:
            t = t[:1].upper() + t[1:]
        out.append(t)
    return "".join(out)
