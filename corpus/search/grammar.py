"""Gramatyka zapytań wyszukiwarki, wspólna dla backendów (OpenSearch, baza).

słowo słowo          AND
"dokładna fraza"     fraza
-słowo               wykluczenie
a NEAR/3 b           a i b w obrębie 3 słów (kolejność dowolna); NEAR = NEAR/2
H430 / G746          numer Stronga
lemma:λόγος          lemat
"""

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')
_NEAR_RE = re.compile(r"NEAR(?:/(\d+))?", re.IGNORECASE)


@dataclass
class LexicalQuery:
    words: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    near: list[tuple[str, str, int]] = field(default_factory=list)
    strongs: list[str] = field(default_factory=list)
    lemmas: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.words,
                self.excluded,
                self.phrases,
                self.near,
                self.strongs,
                self.lemmas,
            )
        )

    @property
    def has_text(self) -> bool:
        return bool(self.words or self.phrases or self.near or self.excluded)


def parse_query(q: str) -> LexicalQuery:
    lq = LexicalQuery()
    tokens: list[tuple[str, bool]] = []  # (tekst, czy_fraza)
    for m in _TOKEN_RE.finditer(q.strip()):
        phrase, word = m.group(1), m.group(2)
        tokens.append((phrase, True) if phrase else (word, False))

    i = 0
    while i < len(tokens):
        text, is_phrase = tokens[i]
        if is_phrase:
            lq.phrases.append(text)
        elif (nm := _NEAR_RE.fullmatch(text)) and 0 < i < len(tokens) - 1:
            left = lq.words.pop() if lq.words else tokens[i - 1][0]
            right = tokens[i + 1][0]
            lq.near.append((left, right, int(nm.group(1) or 2)))
            i += 2
            continue
        elif text.startswith("-") and len(text) > 1:
            lq.excluded.append(text[1:])
        elif re.fullmatch(r"[HG]\d+[a-z]?", text, re.IGNORECASE):
            lq.strongs.append(text.upper())
        elif text.lower().startswith("lemma:"):
            lq.lemmas.append(text[6:])
        else:
            lq.words.append(text)
        i += 1
    return lq
