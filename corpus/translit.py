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
