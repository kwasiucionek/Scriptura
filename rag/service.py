"""Serwis RAG: pytanie -> (chunki literatury + wersety) -> prompt -> strumień odpowiedzi.

Zdarzenia SSE (zgodne z GUI):
  sources  — kandydaci [n] z literatury + wersety znalezione po siglach w pytaniu
  delta    — fragment tekstu odpowiedzi
  done     — pełna odpowiedź, cytowane [n], weryfikacja sigli, czas, użyte tokeny
  error    — {"detail": ...}

Zasady w prompcie: tylko ze źródeł, cytuj [n], odróżniaj tekst / egzegezę /
interpretację, przypisuj tezy autorom, nie wymyślaj wersetów. Każde siglum
w odpowiedzi jest sprawdzane z korpusem — nieistniejące trafiają do `unverified`.
"""

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field

from django.conf import settings

from ane.service import ane_for_question
from corpus.models import Lexeme, Verse
from corpus.services import text as corpus_svc
from corpus.sigla import extract, format_ref, ordinal_range
from library import llm
from library.search import ChunkHit, diversify, expand_with_neighbors, retrieve
from patristics.service import patristics_for_question
from rag.quotes import check_citations, verify_quotes

log = logging.getLogger(__name__)

MODE_INSTRUCTIONS = {
    "scientific": (
        "TRYB NAUKOWY. Odbiorca to biblista lub student teologii. Używasz terminologii egzegetycznej, "
        "przytaczasz terminy hebrajskie i greckie w oryginale (z transliteracją przy pierwszym użyciu), "
        "wskazujesz rozbieżności między autorami i stan dyskusji, odróżniasz argumenty z krytyki tekstu, "
        "krytyki literackiej i historii tradycji. Cytujesz precyzyjnie, także drobne różnice między "
        "przekładami. 2–5 akapitów. Hierarchia źródeł: gdy tę samą tezę podaje publikacja recenzowana "
        "lub monografia i tekst popularyzatorski (blog, wykład), cytujesz publikację; źródła "
        "popularyzatorskie przywołujesz jako uzupełnienie, zaznaczając ich charakter („w tekście "
        "popularyzatorskim Majewski…”)."
    ),
    "popular": (
        "TRYB POPULARNONAUKOWY. Odbiorca to zainteresowana osoba bez wykształcenia teologicznego. Piszesz "
        "jasno i konkretnie, bez żargonu; termin fachowy tylko wtedy, gdy go wyjaśnisz w tym samym zdaniu. "
        "Słowa hebrajskie i greckie podajesz w transliteracji, oryginał tylko gdy pytanie o niego prosi. "
        "Nie referujesz sporów specjalistycznych, chyba że pytanie ich dotyczy — wtedy jednym zdaniem. "
        "Nadal opierasz się wyłącznie na źródłach i cytujesz [n]; wolno opierać się na źródłach naukowych, "
        "ale mówisz o nich prostym językiem. 1–3 akapity. Jeśli autor wyjaśnia rzecz przystępnie w tekście "
        "popularyzatorskim, sięgaj po ten tekst w pierwszej kolejności."
    ),
}

SYSTEM_PROMPT = """Jesteś asystentem teologii biblijnej. Odpowiadasz po polsku.

Zasady:
1. Opierasz się WYŁĄCZNIE na dostarczonych źródłach. Jeśli źródła nie odpowiadają na pytanie, powiedz to wprost i nie zgaduj. Wyjątek: znaczenie słowa hebrajskiego/greckiego możesz objaśnić na podstawie sekcji LEKSYKON i WERSETY (zaznaczając: „według leksykonu STEP i wystąpień w tekście”), nawet gdy literatura milczy — wtedy bez [n].
2. Każdą tezę zaczerpniętą z literatury oznaczasz numerem źródła w nawiasie kwadratowym, np. [2]. Tezy przypisujesz autorom po nazwisku („Rosik zauważa, że…").
3. Rozróżniasz trzy poziomy: co mówi tekst biblijny, co ustala egzegeza (autor X), co jest interpretacją teologiczną. Nie mieszasz ich.
4. Wersety cytujesz tylko z sekcji WERSETY, dokładnie w podanym brzmieniu, z siglum i skrótem przekładu. Nie cytujesz wersetów z pamięci.
5. Gdy autorzy się różnią, przedstawiasz obie pozycje z atrybucją; nie rozstrzygasz, która jest „prawdziwa".
   Źródło oznaczone jako RECENZJA to głos recenzenta o cudzej książce: tezy książki przypisuj jej autorowi, oceny — recenzentowi („Rosik w recenzji książki Niemasa zauważa…").
6. Nie używasz nagłówków Markdown ani list, chyba że pytanie wymaga wyliczenia.
7. Ojców Kościoła (sekcja TRADYCJA PATRYSTYCZNA) oznaczasz numerami [P1], [P2]…, a teksty starożytnego Bliskiego Wschodu (sekcja ANE) numerami [A1], [A2]… — zawsze z autorem i dziełem w zdaniu. Nigdy nie piszesz nazw sekcji w nawiasach.
8. Nie przepisujesz na końcu odpowiedzi sekcji WERSETY ani listy źródeł — czytelnik widzi je obok odpowiedzi."""


@dataclass
class VerseSource:
    ref: str
    work: str
    text: str


@dataclass
class AskResult:
    """Additive SSE result contract.

    verified_citations checks source existence, not support for every claim.
    quotes_checked = quotes_verified + len(quotes_altered) + len(quotes_unverified).
    quotes_tradition is a subset of checked quotes, not a verification exemption.
    """

    answer: str = ""
    citations: list[int] = field(default_factory=list)
    verified_refs: list[str] = field(default_factory=list)
    unverified_refs: list[str] = field(default_factory=list)
    quotes_verified: int = 0
    quotes_checked: int = 0
    quotes_altered: list[dict] = field(default_factory=list)
    quotes_unverified: list[dict] = field(default_factory=list)
    verified_citations: list[str] = field(default_factory=list)
    unverified_citations: list[str] = field(default_factory=list)
    citation_checks: list[dict] = field(default_factory=list)
    quotes_tradition: int = (
        0  # cytaty z odsyłaczem do Ojców / ANE; nie oznacza potwierdzenia
    )
    refused: bool = False
    latency_ms: int = 0
    timings: dict = field(
        default_factory=dict
    )  # retrieval_ms (embedding+OpenSearch+reranker), llm_ms
    usage: dict = field(default_factory=dict)
    context_budget: dict = field(
        default_factory=dict
    )  # szacunek tokenów promptu i co odrzucono, żeby zmieścić się w num_ctx
    model: str = ""
    mode: str = "scientific"


# --- retrieval ------------------------------------------------------------


def _work_access(access):
    return settings.RAG_ACCESS if access is None else access


def _accessible_works(works=None, access=None):
    """Use the levels already resolved by rag.views; an empty list denies all works."""
    codes = list(settings.RAG_VERSE_WORKS) if works is None else works
    if not codes:
        return []
    return corpus_svc.active_works(codes, access=_work_access(access))


def verses_for_question(
    question: str,
    works: list[str] | None = None,
    limit: int = 40,
    *,
    access: list[str] | None = None,
) -> list[VerseSource]:
    refs = [r for m in extract(question) for r in m.refs]
    if not refs:
        return []
    work_objs = _accessible_works(works, access)
    if not work_objs:
        return []
    rows = corpus_svc.parallel(refs, work_objs, access=_work_access(access))[:limit]
    out: list[VerseSource] = []
    for row in rows:
        for w in work_objs:
            vt = row.texts.get(w.code)
            if vt:
                out.append(VerseSource(ref=str(row.verse), work=w.code, text=vt.text))
    return out


def related_for_question(
    question: str,
    works: list[str] | None = None,
    *,
    access: list[str] | None = None,
) -> list[dict]:
    """Powiązane wersety (OpenBible) dla sigli z pytania — do promptu i panelu źródeł."""
    ranges = sigla_ranges(question)
    if not ranges or not settings.RAG_RELATED_VERSES:
        return []
    work_objs = _accessible_works(works, access)
    if not work_objs:
        # No fallback to other works when the requested selection is inaccessible.
        return []
    pl_first = sorted(work_objs, key=lambda w: (w.language != "pl", w.code))
    return [
        asdict(r)
        for r in corpus_svc.related_verses(
            ranges, pl_first, settings.RAG_RELATED_VERSES, access=_work_access(access)
        )
    ]


def sigla_ranges(question: str) -> list[tuple[int, int]]:
    return [ordinal_range(r) for m in extract(question) for r in m.refs]


# --- prompt ---------------------------------------------------------------


_TRANSLIT_STOP = {
    "jest",
    "jaki",
    "jaka",
    "jakie",
    "czym",
    "tego",
    "dlaczego",
    "kiedy",
    "gdzie",
    "hebrajskie",
    "greckie",
    "slovo",
    "slova",
    "znacy",
    "oznaca",
    "bibli",
    "biblia",
    "bog",
    "boga",
    "bogu",
    "pismo",
    "tekst",
    "tekscie",
    "ktory",
    "ktora",
    "ktore",
    "jak",
    "ale",
    "oraz",
    "moze",
    "przez",
    "wersecie",
    "rozdziale",
    "ksiega",
    "ksiegi",
    "ewangelia",
    "ewangeli",
    "list",
    "listu",
    "psalm",
    "psalmie",
    "prorok",
    "tora",
    "izrael",
    "izraela",
    "jezus",
    "jezusa",
    "chrystus",
    "mesjas",
    "pan",
    "pana",
    "kosciol",
    "kosciola",
}  # słowa polskie / nazwy, które nie są terminem do leksykonu (po canonical_translit)


def lexemes_from_transliteration(question: str, limit: int = 3) -> list[Lexeme]:
    """Transliterowane terminy w pytaniu („hesed”, „szalom”, „logos”) -> hasła leksykonu STEP."""
    from corpus.translit import canonical_translit

    words = re.findall(
        r"[A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻżĀāĒēĪīŌōŪūḤḥŠšṢṣṬṭ'’ʾʿ.-]{3,}", question
    )
    seen: set[str] = set()
    out: list[Lexeme] = []
    for w in words:
        key = canonical_translit(w)
        if len(key) < 3 or key in _TRANSLIT_STOP or key in seen:
            continue
        seen.add(key)
        for lx in Lexeme.objects.filter(translit_fold=key).order_by("strong")[:2]:
            out.append(lx)
            if len(out) >= limit:
                return out
    return out


def verses_for_lexeme(
    lx: Lexeme,
    works: list | None = None,
    limit: int = 6,
    *,
    access: list[str] | None = None,
) -> tuple[int, list["VerseSource"], str]:
    """Wystąpienia lematu w korpusie: (liczba, pierwsze `limit` wersetów, rozkład po księgach)."""
    lang = "hbo" if lx.language == "arc" else lx.language
    levels = _work_access(access)
    conc = corpus_svc.concordance(lemma=lx.lemma, language=lang, access=levels)
    if not conc.total and lx.strong:
        conc = corpus_svc.concordance(strong=lx.strong, access=levels)
    if not conc.total:
        return 0, [], ""
    by_book = ", ".join(
        f"{b['abbr']} {b['n']}" for b in sorted(conc.by_book, key=lambda b: -b["n"])[:6]
    )
    if limit <= 0:
        return conc.total, [], by_book
    verse_ids: list[int] = []
    for vid in conc.tokens.values_list("verse_text__verse_id", flat=True).iterator():
        if vid not in verse_ids:
            verse_ids.append(vid)
        if len(verse_ids) >= limit:
            break
    work_objs = (
        _accessible_works(access=access)
        if works is None
        else corpus_svc.visible_works(works, access=levels)
    )
    out: list[VerseSource] = []
    for v in (
        Verse.objects.filter(id__in=verse_ids)
        .select_related("book")
        .order_by("ordinal")
    ):
        # Work objects may become stale after selection; enforce ACL at the text read.
        for vt in v.texts.filter(
            work__in=work_objs,
            work__access__in=[a for a in levels if a != "personal"],
        ).select_related("work"):
            if vt.work.language in ("pl", lang):
                out.append(VerseSource(ref=str(v), work=vt.work.code, text=vt.text))
    return conc.total, out, by_book


def lexicon_lines(
    question: str,
    verses: list["VerseSource"],
    limit: int = 12,
    *,
    access: list[str] | None = None,
) -> list[str]:
    """Hasła leksykonu dla terminów z pytania: oryginał (lemat), Strong H…/G… albo transliteracja."""
    from library.search import original_terms

    grc, hbo = original_terms(question)
    strongs = re.findall(r"\b([HG]\d{1,5}[a-z]?)\b", question)
    qs = Lexeme.objects.none()
    if grc or hbo:
        qs = qs | Lexeme.objects.filter(lemma_norm__in=[*grc, *hbo])
    if strongs:
        qs = qs | Lexeme.objects.filter(strong__in=strongs)
    lexemes = list(qs.distinct()[:limit])
    ids = {lx.id for lx in lexemes}
    lexemes += [lx for lx in lexemes_from_transliteration(question) if lx.id not in ids]
    out = []
    for lx in lexemes[:limit]:
        total, _, by_book = verses_for_lexeme(lx, limit=0, access=access)
        out.append(
            f"{lx.strong} {lx.lemma} ({lx.transliteration}) — {lx.gloss}"
            + (f": {lx.meaning[:240]}" if lx.meaning else "")
            + (
                f" [w korpusie {total} wystąpień; najczęściej: {by_book}]"
                if total
                else ""
            )
        )
    return out


def lexeme_verses_for_question(
    question: str,
    works: list[str] | None = None,
    *,
    access: list[str] | None = None,
) -> list["VerseSource"]:
    """Przykładowe wersety z terminem transliterowanym w pytaniu (gdy pytanie nie ma sigli)."""
    work_objs = _accessible_works(works, access)
    if not work_objs:
        return []
    out: list[VerseSource] = []
    for lx in lexemes_from_transliteration(question, limit=2):
        _, vs, _ = verses_for_lexeme(lx, work_objs, limit=5, access=access)
        out += vs
    return out


def build_messages(
    question: str,
    chunks: list[ChunkHit],
    verses: list[VerseSource],
    mode: str = "scientific",
    related: list[dict] | None = None,
    ane: list | None = None,
    patristics: list | None = None,
    *,
    access: list[str] | None = None,
) -> list[dict]:
    src_lines = []
    for n, h in enumerate(chunks, start=1):
        kind = (
            " [RECENZJA KSIĄŻKI — tezy recenzowanego autora odróżniaj od ocen recenzenta]"
            if h.doc_type == "review"
            else ""
        )
        head = f"[{n}] {h.citation}{kind}" + (
            f" — sekcja: {h.section}" if h.section else ""
        )
        src_lines.append(f"{head}\n{h.text}")
    verse_lines = [f"{v.ref} ({v.work}): {v.text}" for v in verses]
    lex_lines = lexicon_lines(question, verses, access=access)
    user = (
        f"PYTANIE:\n{question}\n\n"
        f"ŹRÓDŁA (literatura):\n"
        + ("\n\n".join(src_lines) if src_lines else "(brak)")
        + "\n\n"
        "WERSETY (tekst biblijny z korpusu):\n"
        + ("\n".join(verse_lines) if verse_lines else "(brak)")
        + (
            "\n\nLEKSYKON (STEPBible, glosy angielskie — pomocniczo do terminów):\n"
            + "\n".join(lex_lines)
            if lex_lines
            else ""
        )
        + (
            "\n\nPOWIĄZANE WERSETY (OpenBible cross-references, wg głosów; tylko jako trop — cytuj wyłącznie z sekcji WERSETY):\n"
            + "\n".join(
                f"{r['ref']} ({r['work']}, {r['votes']}): {r['text']}"
                for r in related
                if r.get("text")
            )
            if related
            else ""
        )
        + (
            "\n\nTEKSTY PORÓWNAWCZE STAROŻYTNEGO BLISKIEGO WSCHODU (eBL; przekład angielski; NIE są tekstem "
            "biblijnym ani literaturą naukową — cytuj z oznaczeniem tekstu i linii, np. „Gilgamesz SB XI 8–19”):\n"
            + "\n\n".join(
                f"[A{i}] {h.ref}: {h.translation_en}"
                for i, h in enumerate(ane, start=1)
            )
            if ane
            else ""
        )
        + (
            "\n\nTRADYCJA PATRYSTYCZNA (Ojcowie Kościoła; przekłady angielskie ANF/NPNF, domena publiczna; "
            "NIE jest literaturą naukową — przytaczaj z autorem i dziełem, np. „Ireneusz, Adversus haereses III 21”; "
            "odróżniaj interpretację Ojców od egzegezy współczesnej):\n"
            + "\n\n".join(
                f"[P{i}] {h.ref}{' [odsyła do tego miejsca]' if h.via_verse else ''}: {h.text_en}"
                for i, h in enumerate(patristics, start=1)
            )
            if patristics
            else ""
        )
    )
    system = (
        SYSTEM_PROMPT
        + "\n\n"
        + MODE_INSTRUCTIONS.get(mode, MODE_INSTRUCTIONS["scientific"])
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# --- budżet kontekstu -----------------------------------------------------
# Różne modele mają różne tokenizery: ten sam prompt to np. 16 tys. tokenów w Gemmie
# i 21 tys. w PLLuM. Ollama albo zgłasza błąd, albo po cichu obcina prompt (tracimy
# źródła). Dlatego szacujemy długość promptu przed wysłaniem i przycinamy najmniej
# ważne elementy: powiązane wersety -> ANE -> Ojcowie -> najsłabsze fragmenty literatury.
# Szacunek jest znakowy (bez tokenizera modelu), więc celowo ostrożny.

_SECTION_OVERHEAD_CHARS = 1200  # nagłówki sekcji i instrukcje w treści użytkownika


def _chars_per_token() -> float:
    # polski tekst to ok. 3–4 znaki/token; hebrajski/grecki mniej — stąd ostrożne 2.8
    return float(getattr(settings, "RAG_CHARS_PER_TOKEN", 2.8))


def _est_tokens(chars: int) -> int:
    return int(chars / _chars_per_token()) + 1


def prompt_budget_tokens() -> int | None:
    """Ile tokenów może mieć prompt: num_ctx minus rezerwa na odpowiedź. None = bez limitu."""
    num_ctx = settings.RAG_NUM_CTX
    if not num_ctx:
        return None
    reserve = int(getattr(settings, "RAG_ANSWER_RESERVE_TOKENS", 2048))
    return max(num_ctx - reserve, 1024)


def fit_to_budget(
    question: str,
    chunks: list[ChunkHit],
    verses: list[VerseSource],
    mode: str,
    related: list[dict],
    ane: list,
    patristics: list,
    *,
    access: list[str] | None = None,
) -> tuple[list[ChunkHit], list[dict], list, list, dict]:
    """Przycina źródła do budżetu promptu. Zwraca listy po przycięciu i raport.

    Wersety i leksykon zostają w całości (są podstawą cytatów), fragmenty literatury
    zawsze co najmniej jeden. Musi działać PRZED zdarzeniem `sources`, żeby numery
    [n], [P1], [A1] w GUI zgadzały się z promptem.
    """
    budget = prompt_budget_tokens()
    chunks, related, ane, patristics = (
        list(chunks),
        list(related or []),
        list(ane or []),
        list(patristics or []),
    )
    # stała część: system, pytanie, wersety, leksykon (liczona raz — leksykon pyta bazę)
    base = (
        sum(
            len(m["content"])
            for m in build_messages(question, [], verses, mode, access=access)
        )
        + _SECTION_OVERHEAD_CHARS
    )
    sizes = {
        "chunks": [
            len(h.citation or "") + len(h.section or "") + len(h.text or "") + 60
            for h in chunks
        ],
        "related": [
            len(r.get("ref") or "") + len(r.get("text") or "") + 30 for r in related
        ],
        "ane": [len(h.ref or "") + len(h.translation_en or "") + 20 for h in ane],
        "patristics": [
            len(h.ref or "") + len(h.text_en or "") + 40 for h in patristics
        ],
    }

    def total_tokens() -> int:
        return _est_tokens(base + sum(sum(v) for v in sizes.values()))

    report = {
        "budget_tokens": budget,
        "estimated_tokens_before": total_tokens(),
        "dropped": {"related": 0, "ane": 0, "patristics": 0, "chunks": 0},
    }
    if budget is None or report["estimated_tokens_before"] <= budget:
        report["estimated_tokens_after"] = report["estimated_tokens_before"]
        return chunks, related, ane, patristics, report

    lists = {"related": related, "ane": ane, "patristics": patristics, "chunks": chunks}
    for name in ("related", "ane", "patristics", "chunks"):
        keep_min = 1 if name == "chunks" else 0
        while total_tokens() > budget and len(lists[name]) > keep_min:
            lists[name].pop()
            sizes[name].pop()
            report["dropped"][name] += 1

    report["estimated_tokens_after"] = total_tokens()
    log.info(
        "Budżet kontekstu %s tok.: szacunek %s -> %s, odrzucono %s",
        budget,
        report["estimated_tokens_before"],
        report["estimated_tokens_after"],
        report["dropped"],
    )
    if report["estimated_tokens_after"] > budget:
        log.warning(
            "Prompt nadal przekracza budżet (wersety/leksykon za długie) — zwiększ RAG_NUM_CTX"
        )
    return chunks, related, ane, patristics, report


# --- LLM ------------------------------------------------------------------
# Klient w library/llm.py (ollama | openai | echo); tu tylko parametry odpowiedzi RAG.


def llm_stream(messages: list[dict]) -> Iterator[llm.Delta]:
    return llm.chat_stream(
        messages,
        temperature=settings.RAG_TEMPERATURE,
        num_ctx=settings.RAG_NUM_CTX,
        think=settings.RAG_THINK,
    )


# --- weryfikacja ----------------------------------------------------------


def verify_refs(answer: str) -> tuple[list[str], list[str]]:
    """Sigla z odpowiedzi -> (istniejące w korpusie, nieistniejące)."""
    ok, bad = [], []
    for m in extract(answer):
        for ref in m.refs:
            label = format_ref(ref)
            s, e = ordinal_range(ref)
            if Verse.objects.filter(ordinal__range=(s, e)).exists():
                ok.append(label)
            else:
                bad.append(label)
    return sorted(set(ok)), sorted(set(bad))


# --- pełny przebieg -------------------------------------------------------


_TS_RE = re.compile(r"\[(?:(\d+):)?(\d{1,2}):(\d{2})\]")


def source_url(h: ChunkHit) -> str:
    """Dla nagrań (video) link do minuty: znacznik [mm:ss] akapitu (dokładniejszy) lub rozdziału -> &t=<s>."""
    if h.doc_type != "video" or not h.url:
        return h.url

    def secs_of(g) -> int:
        return int(g[0] or 0) * 3600 + int(g[1]) * 60 + int(g[2])

    sec = _TS_RE.search(h.section or "")
    sec_t = secs_of(sec.groups()) if sec else None
    stamps = [secs_of(g) for g in _TS_RE.findall(h.text or "")]
    t = next((st for st in stamps if st != sec_t), sec_t)
    if t is None:
        return h.url
    sep = "&" if "?" in h.url else "?"
    return f"{h.url}{sep}t={t}s"


def registers_for_mode(mode: str) -> list[str] | None:
    """Twardy filtr rejestru — tylko gdy RAG_REGISTER_MODE=hard (tryb naukowy: scientific+mixed).
    Domyślnie (soft) tryb zmienia poziom odpowiedzi i kolejność źródeł, nie ich zbiór."""
    if settings.RAG_REGISTER_MODE == "hard" and mode == "scientific":
        return ["scientific", "mixed"]
    return None


# premia/kara rankingowa wg rejestru źródła (mnożnik wyniku), gdy RAG_REGISTER_MODE=soft
REGISTER_WEIGHTS = {
    "scientific": {"scientific": 1.0, "mixed": 0.95, "popular": 0.8},
    "popular": {"popular": 1.0, "mixed": 1.0, "scientific": 0.9},
}


def apply_register_preference(
    hits: list[ChunkHit], mode: str, k: int
) -> list[ChunkHit]:
    """Miękka preferencja: przemnaża wynik przez wagę rejestru i przycina do k (stabilnie)."""
    if settings.RAG_REGISTER_MODE != "soft":
        return hits[:k]
    weights = REGISTER_WEIGHTS.get(mode, {})
    ranked = sorted(
        enumerate(hits),
        key=lambda ih: (-(ih[1].score or 0) * weights.get(ih[1].register, 1.0), ih[0]),
    )
    return [h for _, h in ranked][:k]


def ask(
    question: str,
    authors: list[str] | None = None,
    works: list[str] | None = None,
    mode: str | None = None,
    access: list[str] | None = None,
    user_id: int | None = None,
    personal_only: bool = False,
    include_ane: bool | None = None,
    include_patristics: bool | None = None,
) -> Iterator[tuple[str, dict]]:
    """Generator (event, data) do zaserwowania jako SSE.
    `access` — poziomy dostępu użytkownika (rag.access.access_for_user); None = RAG_ACCESS."""
    t0 = time.monotonic()
    mode = mode if mode in MODE_INSTRUCTIONS else settings.RAG_DEFAULT_MODE
    registers = registers_for_mode(mode)
    ranges = sigla_ranges(question)
    try:
        chunks = retrieve(
            question,
            k=settings.RAG_TOP_K + (4 if settings.RAG_REGISTER_MODE == "soft" else 0),
            access=access,
            authors=authors,
            sigla=ranges or None,
            registers=registers,
            user_id=user_id,
            personal_only=personal_only,
        )
        if (
            ranges and not chunks
        ):  # sigla w pytaniu, ale nikt o tym nie pisze -> bez filtra sigli
            chunks = retrieve(
                question,
                k=settings.RAG_TOP_K
                + (4 if settings.RAG_REGISTER_MODE == "soft" else 0),
                access=access,
                authors=authors,
                registers=registers,
                user_id=user_id,
                personal_only=personal_only,
            )
        chunks = apply_register_preference(chunks, mode, settings.RAG_TOP_K + 4)
        chunks = diversify(chunks, settings.RAG_MAX_PER_DOC, settings.RAG_TOP_K)
        chunks = expand_with_neighbors(chunks, settings.RAG_CONTEXT_NEIGHBORS)
        t_v = time.monotonic()
        verses = verses_for_question(question, works, access=access)
        if not verses:  # pytanie bez sigli, ale z terminem (hesed, szalom) -> przykładowe wystąpienia
            verses = lexeme_verses_for_question(question, works, access=access)
        related = related_for_question(question, works, access=access)
        ane = ane_for_question(question, include_ane)
        patristics = patristics_for_question(
            question, sigla_ranges(question), include_patristics
        )
        verses_ms = int((time.monotonic() - t_v) * 1000)
        # przycięcie do budżetu przed `sources`, żeby numeracja [n] się zgadzała
        chunks, related, ane, patristics, budget_report = fit_to_budget(
            question, chunks, verses, mode, related, ane, patristics, access=access
        )
        t_retrieval = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        log.exception("retrieval")
        yield "error", {"detail": f"Błąd wyszukiwania: {exc}"}
        return

    sources = [
        {
            "n": n,
            "document_id": h.document_id,
            "title": h.title,
            "authors": h.authors,
            "citation": h.citation,
            "section": h.section,
            "doc_type": h.doc_type,
            "year": h.year,
            "url": source_url(h),
            "sigla": h.sigla,
            "snippet": h.text[:600],
            "access": h.access,
            "register": h.register,
            "personal": h.access == "personal",
        }
        for n, h in enumerate(chunks, start=1)
    ]
    yield (
        "sources",
        {
            "chunks": sources,
            "verses": [asdict(v) for v in verses],
            "related": related,
            "ane": [asdict(h) for h in ane],
            "patristics": [asdict(h) for h in patristics],
        },
    )

    result = AskResult(model=llm.model_name(), mode=mode, context_budget=budget_report)
    from library.search import LAST_TIMINGS

    result.timings["retrieval_ms"] = t_retrieval
    result.timings.update(LAST_TIMINGS)
    result.timings["verses_ms"] = verses_ms
    t_llm = time.monotonic()
    if not chunks and not verses and not ane and not patristics:
        result.refused = True
        result.answer = "W dostępnych źródłach nie ma materiału, który pozwoliłby odpowiedzieć na to pytanie."
        yield "delta", {"text": result.answer}
    else:
        parts: list[str] = []
        try:
            for rec in llm_stream(
                build_messages(
                    question,
                    chunks,
                    verses,
                    mode,
                    related,
                    ane,
                    patristics,
                    access=access,
                )
            ):
                if rec.done:
                    result.usage = {
                        "prompt_tokens": rec.prompt_tokens,
                        "completion_tokens": rec.completion_tokens,
                    }
                    break
                if rec.content:
                    parts.append(rec.content)
                    yield "delta", {"text": rec.content}
        except Exception as exc:
            log.exception("llm")
            yield "error", {"detail": f"Błąd modelu: {exc}"}
            return
        result.answer = "".join(parts).strip()
        result.refused = bool(
            re.search(
                r"nie (?:zawiera\w*|ma|znajduj\w*|pozwala\w*)\b.{0,60}(?:informacj|źródł|materiał|odpowied)"
                r"|brak (?:informacji|źródeł|materiału)",
                result.answer[:400],
                re.I,
            )
        )

    result.timings["llm_ms"] = int((time.monotonic() - t_llm) * 1000)
    available = {
        *(f"[{n}]" for n in range(1, len(chunks) + 1)),
        *(f"[P{n}]" for n in range(1, len(patristics) + 1)),
        *(f"[A{n}]" for n in range(1, len(ane) + 1)),
    }
    citation_checks = check_citations(result.answer, available)
    cited = {
        ref
        for c in citation_checks
        if not c.reason or c.reason == "missing_source"
        for ref in c.refs
    }
    result.verified_citations = sorted(cited & available)
    result.unverified_citations = sorted(
        (cited - available)
        | {
            c.marker
            for c in citation_checks
            if c.reason and c.reason != "missing_source"
        }
    )
    result.citation_checks = [asdict(c) for c in citation_checks]
    result.citations = sorted(
        int(ref[1:-1]) for ref in cited & available if ref[1:-1].isdigit()
    )
    result.verified_refs, result.unverified_refs = verify_refs(result.answer)
    qr = verify_quotes(
        result.answer,
        [(f"{v.ref} ({v.work})", v.text) for v in verses],
        [(f"[{n}] {h.citation}", h.text) for n, h in enumerate(chunks, start=1)],
        tradition=[
            *[
                (f"[P{n}] {h.ref}", h.text_en)
                for n, h in enumerate(patristics, start=1)
            ],
            *[
                (f"[A{n}] {h.ref}", h.translation_en)
                for n, h in enumerate(ane, start=1)
            ],
        ],
    )
    result.quotes_verified, result.quotes_checked = qr.verified, qr.checked
    result.quotes_altered = [asdict(a) for a in qr.altered]
    result.quotes_unverified = [asdict(a) for a in qr.unverified]
    result.quotes_tradition = qr.tradition
    result.latency_ms = int((time.monotonic() - t0) * 1000)
    yield "done", asdict(result)
