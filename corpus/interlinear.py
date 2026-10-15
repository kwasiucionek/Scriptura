"""Interlinia: kontekstowe polskie glosy słów tekstu oryginalnego (SBLGNT, WLC).

Glosy powstają maszynowo (model językowy w Ollamie, domyślnie deepseek-v4.1-flash:cloud bez trybu
„thinking”) i są przekładem roboczym: model dostaje kolejne wersety jednej księgi, a przy każdym
słowie lemat, kod morfologiczny i angielską glosę słownikową STEP; zwraca JSON
{"<osis>": [{"i": pozycja, "g": "glosa"}, ...]}. Wyrównanie sprawdzamy po pozycjach — werset
z brakującą, nadmiarową albo pustą pozycją idzie do ponownego zapytania pojedynczo.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

SYSTEM_PROMPT = (
    "Jesteś tłumaczem przekładu interlinearnego Biblii na język polski (współczesna polszczyzna, "
    "styl dosłowny). Dla KAŻDEGO słowa oryginału podaj jego polski odpowiednik w tym konkretnym "
    "kontekście: 1–3 słowa, w formie gramatycznej wynikającej z morfologii i kontekstu (przypadek, "
    "liczba, rodzaj, osoba, czas, strona). Nie przestawiaj, nie łącz i nie pomijaj słów: dokładnie "
    "jedna glosa na jedno słowo, w tej samej kolejności. Rodzajnik grecki tłumacz jako „-”, chyba "
    "że w polszczyźnie potrzeba zaimka. Hebrajskie przedrostki (w-, i-, ha-, le-, be-, mi-) oddaj "
    "w glosie słowa (np. „na początku”, „i ziemia”). Partykułę אֵת tłumacz jako „-”. Tetragram "
    "יהוה oddawaj jako „JHWH”, אֲדֹנָי jako „Pan”. Imiona własne w formie przyjętej w polskich "
    "przekładach (Jezus, Dawid, Mojżesz, Izrael). Zwróć wyłącznie JSON (bez komentarza): "
    '{"<werset>": [{"i": <pozycja>, "g": "<glosa>"}, ...], ...} — po jednym obiekcie dla każdej '
    "pozycji i z wejścia."
)
USER_PREFIX = (
    "Słowa do przetłumaczenia (i = pozycja, w = słowo, lemma, morph = kod morfologiczny, "
    "gloss = znaczenie słownikowe EN):\n"
)
MAX_GLOSS = 120
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
ORIGINAL_SCRIPT = re.compile("[\u0370-\u03ff\u1f00-\u1fff\u0590-\u05ff]")  # grecki, hebrajski


@dataclass
class VerseItem:
    """Werset do glosowania: klucz (osis), id VerseText i słowa z danymi pomocniczymi."""

    osis: str
    verse_text_id: int
    words: list[dict] = field(
        default_factory=list
    )  # {"i", "w", "lemma", "morph", "gloss"}

    @property
    def positions(self) -> set[int]:
        return {w["i"] for w in self.words}


def build_user_message(verses: list[VerseItem]) -> str:
    payload = {v.osis: v.words for v in verses}
    return USER_PREFIX + json.dumps(payload, ensure_ascii=False)


def parse_response(text: str, verses: list[VerseItem]) -> dict[int, dict[int, str]]:
    """Glosy poprawnie wyrównanych wersetów: verse_text_id -> {pozycja: glosa}.
    Werset pominięty w wyniku = do ponownego zapytania."""
    data = extract_json(text)
    if not isinstance(data, dict):
        return {}
    out: dict[int, dict[int, str]] = {}
    for v in verses:
        items = data.get(v.osis)
        if not isinstance(items, list):
            continue
        glosses: dict[int, str] = {}
        ok = True
        for it in items:
            if not isinstance(it, dict):
                ok = False
                break
            try:
                pos = int(it.get("i"))
            except (TypeError, ValueError):
                ok = False
                break
            g = it.get("g")
            if not isinstance(g, str) or not g.strip() or pos in glosses:
                ok = False
                break
            g = " ".join(g.split())[:MAX_GLOSS]
            if ORIGINAL_SCRIPT.search(
                g
            ):  # model przepisał słowo zamiast je przetłumaczyć
                ok = False
                break
            glosses[pos] = g
        if ok and set(glosses) == v.positions:
            out[v.verse_text_id] = glosses
    return out


def chat(
    system: str,
    user: str,
    model: str,
    base_url: str,
    api_key: str = "",
    num_predict: int = 4000,
    timeout: int = 240,
    attempts: int = 5,
) -> str:
    """Treść odpowiedzi modelu (Ollama /api/chat, bez „thinking”). Ponawia przy 429/5xx
    i błędach sieci z rosnącą przerwą."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": num_predict},
        "keep_alive": "1h",
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(body).encode()
    delay = 5.0
    for attempt in range(attempts):
        req = urllib.request.Request(f"{base_url}/api/chat", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.load(resp)["message"]["content"]
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == attempts - 1:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 120)
    return ""


def request_glosses(
    verses: list[VerseItem], model: str, base_url: str, api_key: str = ""
) -> str:
    words = sum(len(v.words) for v in verses)
    return chat(
        SYSTEM_PROMPT,
        build_user_message(verses),
        model,
        base_url,
        api_key,
        num_predict=words * 40 + 400,
    )


def extract_json(text: str):
    """Pierwszy obiekt JSON z odpowiedzi modelu (model bywa gadatliwy) albo None."""
    m = _JSON_RE.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def gloss_verses(
    verses: list[VerseItem], model: str, base_url: str, api_key: str = ""
) -> tuple[dict[int, dict[int, str]], list[str]]:
    """Glosy dla paczki wersetów; nieudane wersety ponawiane pojedynczo (2 próby).
    Zwraca (glosy, osis wersetów bez wyniku). Bez dostępu do bazy — bezpieczne w wątku."""
    try:
        result = parse_response(
            request_glosses(verses, model, base_url, api_key), verses
        )
    except Exception:  # noqa: BLE001 — paczka przechodzi w tryb pojedynczy
        result = {}
    failed = []
    for v in verses:
        if v.verse_text_id in result:
            continue
        for _ in range(2):
            try:
                one = parse_response(
                    request_glosses([v], model, base_url, api_key), [v]
                )
            except Exception:  # noqa: BLE001
                one = {}
            if one:
                result.update(one)
                break
        else:
            failed.append(v.osis)
    return result, failed


def batches(
    verses: list[VerseItem], max_words: int = 320, max_verses: int = 30
) -> list[list[VerseItem]]:
    """Kolejne wersety jednej księgi w paczkach ograniczonych liczbą słów (kontekst dla modelu)."""
    out: list[list[VerseItem]] = []
    cur: list[VerseItem] = []
    words = 0
    for v in verses:
        n = len(v.words)
        if cur and (words + n > max_words or len(cur) >= max_verses):
            out.append(cur)
            cur, words = [], 0
        cur.append(v)
        words += n
    if cur:
        out.append(cur)
    return out
