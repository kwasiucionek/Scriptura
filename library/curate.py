"""Kurator manifestu: ocena pozycji przez model (tytuł, abstrakt, czasopismo) + reguły deterministyczne.

Model decyduje o wszystkim poza licencją i dostępem: czy praca należy do biblistyki/teologii
(a nie imiennika z innej dziedziny), typ (artykuł/recenzja/sprawozdanie/wstępniak/książka),
rejestr, język tekstu, poprawność nazwy czasopisma (metadane OpenAlex bywają błędne).
`access` wynika z licencji (normalize_license); kurator może ustawić `skip` (szum, nieistotne)
lub `review` (model sam zgłasza niepewność). Reguły: dublety po DOI/tytule, znane wzorce szumu.
Bez modelu (LLM_BACKEND != ollama) działają same reguły.
"""

import json
import logging
import re
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from django.conf import settings

from library.harvest.manifest import ManifestEntry

log = logging.getLogger(__name__)

NOISE_RE = re.compile(
    r"sprawozdan|report from|konferencj|conference|walne zebranie|recenzje i sprawozdania"
    r"|kościół w czasie pandemii|od redakcji|editorial|^wstęp$",
    re.I,
)
REVIEW_RE = re.compile(r"^(?:recenzja|review of|rec\.)|\(rec\.\)|\[review\]", re.I)

CURATE_PROMPT = """Jesteś kuratorem korpusu RAG z zakresu teologii biblijnej (autorzy: polscy bibliści, m.in. z PWT Wrocław i UPJPII).
Oceń każdą pozycję z listy i zwróć JSON: lista obiektów, po jednym na pozycję, w tej samej kolejności:
{{"i": <numer>, "relevant": true|false, "kind": "article|review|report|editorial|book|other",
  "register": "scientific|popular|mixed", "language": "pl|en|it|de|fr|la|other",
  "journal_ok": true|false, "journal_fix": "<poprawna nazwa czasopisma lub pusty string>",
  "confidence": 0.0-1.0, "reason": "<jedno zdanie po polsku>"}}

Zasady:
- relevant=false, gdy treść (tytuł, abstrakt) dotyczy innej dziedziny niż teologia/biblistyka/religioznawstwo/
  historia Kościoła/filologia biblijna — czyli to praca imiennika autora. Recenzje książek teologicznych są relevant.
- Metadane czasopisma bywają błędne (OpenAlex myli skróty, np. „Water Practice & Technology” zamiast „Wrocławski
  Przegląd Teologiczny”, oba WPT). Oceniaj po treści; jeśli czasopismo nie pasuje do treści, journal_ok=false
  i podaj journal_fix, gdy potrafisz je wskazać (polskie czasopisma teologiczne: Wrocławski Przegląd Teologiczny,
  Biblical Annals, Verbum Vitae, Collectanea Theologica, Ruch Biblijny i Liturgiczny, Scriptura Sacra, Studia Gdańskie…).
- kind: report = sprawozdanie/konferencja; editorial = wstępniak redakcyjny; review = recenzja książki.
- register: popular = teksty popularyzatorskie i duszpasterskie; scientific = artykuły naukowe i monografie;
  mixed = podręczniki, skrypty, teksty pośrednie.
- confidence < 0.5 tylko wtedy, gdy naprawdę nie da się rozstrzygnąć z podanych danych.
Zwróć TYLKO JSON.

AUTOR: {author}
POZYCJE:
{items}"""


@dataclass
class Decision:
    index: int
    access: str  # open | licensed | skip | review (nadpisuje tylko na skip/review)
    kind: str = "article"
    register: str = ""
    language: str = ""
    journal_fix: str = ""
    relevant: bool = True
    confidence: float = 1.0
    reason: str = ""
    duplicate_of: int | None = None


@dataclass
class CurationReport:
    decisions: list[Decision] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.decisions:
            out[d.access] = out.get(d.access, 0) + 1
        return out


def _title_key(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 .]+", " ", t).split(".")[0].strip()


def find_duplicates(
    entries: list[ManifestEntry], threshold: float = 0.9
) -> dict[int, int]:
    """indeks -> indeks wcześniejszej pozycji o tym samym DOI lub tytule.
    Ten sam tytuł u RÓŻNYCH autorów to różne teksty (np. dwie recenzje jednej książki) —
    dublet tytułowy tylko przy wspólnym autorze lub braku danych o autorach."""
    dup: dict[int, int] = {}
    seen_doi: dict[str, int] = {}
    keys: list[str] = []
    for i, e in enumerate(entries):
        if e.doi and e.doi in seen_doi:
            dup[i] = seen_doi[e.doi]
        elif e.doi:
            seen_doi[e.doi] = i
        k = _title_key(e.title)
        if i not in dup and k:
            mine = {a.lower() for a in e.authors}
            for j, kj in enumerate(keys):
                if (
                    not kj
                    or j in dup
                    or SequenceMatcher(None, k, kj).ratio() < threshold
                ):
                    continue
                theirs = {a.lower() for a in entries[j].authors}
                if not mine or not theirs or mine & theirs:
                    dup[i] = j
                    break
        keys.append(k)
    return dup


def rule_decisions(entries: list[ManifestEntry]) -> list[Decision]:
    dups = find_duplicates(entries)
    out = []
    for i, e in enumerate(entries):
        d = Decision(index=i, access=e.access, language=e.language)
        if e.access == "skip":
            d.reason = "już oznaczone skip"
        elif i in dups:
            d.access, d.duplicate_of, d.reason = (
                "skip",
                dups[i],
                f"dublet pozycji {dups[i] + 1}",
            )
        elif NOISE_RE.search(e.title):
            d.access, d.kind, d.reason = (
                "skip",
                "report",
                "sprawozdanie / wstępniak (reguła)",
            )
        elif REVIEW_RE.search(e.title):
            d.kind, d.reason = "review", "recenzja (reguła)"
        out.append(d)
    return out


def _extract_json(content: str):
    """JSON z odpowiedzi modelu: zdejmuje ogrodzenia ``` i szuka pierwszego [ lub {."""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"brak JSON w odpowiedzi modelu: {text[:200]!r}")


def chat_json(prompt: str, num_predict: int = 4000, attempts: int = 2) -> list[dict]:
    """Wywołanie modelu z oczekiwaną listą obiektów JSON.
    Retry: pusta treść -> bez format=json; błąd sieci/parsowania -> ponowienie (attempts)."""

    def _chat(force_json: bool) -> str:
        body = {
            "model": settings.CURATE_MODEL or settings.OLLAMA_CHAT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": 16384,
                "num_predict": num_predict,
            },
        }
        if force_json:
            body["format"] = "json"
        headers = {"Content-Type": "application/json"}
        if settings.OLLAMA_API_KEY:
            headers["Authorization"] = f"Bearer {settings.OLLAMA_API_KEY}"
        req = urllib.request.Request(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            data=json.dumps(body).encode(),
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
            return json.load(resp)["message"].get("content", "") or ""

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            content = _chat(attempt == 0)  # druga próba bez wymuszonego formatu
            if not content.strip():
                log.info("kurator: pusta odpowiedź, powtarzam bez format=json")
                content = _chat(False)
            data = _extract_json(content)
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [])
            return data
        except Exception as exc:  # noqa: BLE001 — sieć, timeout, JSON
            last = exc
            log.warning("kurator: próba %s nieudana: %s", attempt + 1, exc)
    raise RuntimeError(
        f"model nie odpowiedział poprawnie po {attempts} próbach: {last}"
    )


def chat_json_per_item(prompt_template: str, items: list[str]) -> list[dict]:
    """Awaryjnie: pozycja po pozycji (gdy partia pada), z zachowaniem numeracji `i`."""
    rows: list[dict] = []
    for idx, item in enumerate(items, start=1):
        try:
            r = chat_json(prompt_template.format(items=item), attempts=1)
            if r:
                r[0]["i"] = idx
                rows.append(r[0])
        except Exception as exc:  # noqa: BLE001
            log.warning("kurator: pozycja %s pominięta: %s", idx, exc)
    return rows


def _item_line(i: int, e: ManifestEntry) -> str:
    abstract = (getattr(e, "abstract", "") or "")[:600]
    line = (
        f"{i}. [{e.year or '----'}] {e.title}\n   czasopismo: {e.journal or '?'}; język wg metadanych: {e.language};"
        f" typ: {e.doc_type}"
    )
    if abstract:
        line += f"\n   abstrakt: {abstract}"
    return line


def llm_decisions(
    entries: list[ManifestEntry],
    author: str,
    decisions: list[Decision],
    batch: int = 10,
) -> None:
    """Uzupełnia decyzje modelem dla pozycji, których reguły nie rozstrzygnęły."""
    pending = [d for d in decisions if d.access != "skip"]
    for start in range(0, len(pending), batch):
        chunk = pending[start : start + batch]
        lines = [_item_line(n + 1, entries[d.index]) for n, d in enumerate(chunk)]
        template = CURATE_PROMPT.replace("{author}", author)
        try:
            rows = chat_json(template.format(items="\n".join(lines)))
        except Exception as exc:
            log.warning("kurator: partia padła (%s) — pozycja po pozycji", exc)
            rows = chat_json_per_item(template, lines)
        rows = [r for r in rows if isinstance(r, dict)]
        by_i = {}
        for r in rows:
            try:
                by_i[int(r.get("i", 0))] = r
            except (TypeError, ValueError):
                pass
        for n, d in enumerate(chunk):
            r = by_i.get(n + 1)
            if r is None and len(rows) == len(
                chunk
            ):  # numeracja niezgodna, ale komplet — po kolejności
                r = rows[n]
            if not r:
                continue
            d.relevant = bool(r.get("relevant", True))
            d.kind = r.get("kind", d.kind) or d.kind
            d.register = r.get("register", "") or ""
            d.language = r.get("language", d.language) or d.language
            d.confidence = float(r.get("confidence", 0.5) or 0.5)
            d.reason = r.get("reason", "") or d.reason
            if r.get("journal_ok") is False and r.get("journal_fix"):
                d.journal_fix = str(r["journal_fix"])[:200]
            if d.confidence < settings.CURATE_REVIEW_CONFIDENCE:
                d.access = "review"
            elif not d.relevant or d.kind in ("report", "editorial"):
                d.access = "skip"


def curate(
    entries: list[ManifestEntry], author: str, use_llm: bool = True
) -> CurationReport:
    decisions = rule_decisions(entries)
    if use_llm and settings.LLM_BACKEND == "ollama":
        llm_decisions(entries, author, decisions)
    return CurationReport(decisions)


def apply(entries: list[ManifestEntry], report: CurationReport) -> None:
    """Wpisuje decyzje do manifestu. Licencja/access 'open|licensed' zostaje; kurator może dać skip/review."""
    for d in report.decisions:
        e = entries[d.index]
        if d.access in ("skip", "review"):
            e.access = d.access
        if d.register and not getattr(e, "register", ""):
            e.register = d.register
        if d.kind in ("book", "review", "article") and d.kind != e.doc_type:
            e.doc_type = d.kind
        if d.journal_fix:
            e.note = f"czasopismo poprawione z „{e.journal}” | {e.note}"
            e.journal = d.journal_fix
        if d.language and d.language != "other" and d.language != e.language:
            e.note = (
                f"język wg kuratora: {d.language} (metadane: {e.language}) | {e.note}"
            )
            e.language = d.language
        if d.reason:
            e.note = f"kurator: {d.reason} | {e.note}"
