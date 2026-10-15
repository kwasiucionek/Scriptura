"""Słowniki Stronga (Greek 1890, Hebrew 1894; domena publiczna) w wersji JSON Open Scriptures
(CC BY-SA): definicja, pochodzenie słowa, przekłady w KJV, wymowa.

Plik .js to obiekt JavaScript poprzedzony komentarzem licencyjnym i `var … = {`; wyciągamy
literał obiektu i czytamy go jako JSON. Klucze: „G725”, „H7225” — numer bez zer wiodących,
bez litery homografu (STEP ma H1254a, H1254b — oba dostają hasło H1254).
"""

import json
import re
from pathlib import Path

_STRONG_BASE = re.compile(r"^([GH])0*(\d+)")


def base_number(strong: str) -> str:
    m = _STRONG_BASE.match(strong)
    return f"{m.group(1)}{int(m.group(2))}" if m else strong


def parse_js(path: Path) -> dict[str, dict]:
    text = path.read_text(encoding="utf-8")
    start, end = text.index("{", text.index("=")), text.rindex("}")
    return json.loads(text[start : end + 1])


def entry_fields(entry: dict) -> dict:
    """Pola Lexeme ze wpisu Stronga (puste, gdy brak)."""
    return {
        "strong_def": (entry.get("strongs_def") or "").strip(),
        "strong_deriv": (entry.get("derivation") or "").strip()[:300],
        "strong_kjv": (entry.get("kjv_def") or "").strip(),
        "strong_pron": (entry.get("pron") or "").strip()[:64],
    }
