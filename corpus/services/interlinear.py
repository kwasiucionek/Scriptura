"""Widok interlinearny: słowo oryginału z numerem Stronga, transliteracją, polską glosą
w kontekście (Token.gloss_pl) i morfologią po polsku; pod wersetem przekład polski — SNPD
(Przekład dosłowny EIB, z przypisami), gdy użytkownik ma do niego dostęp, inaczej BG 1632;
parametr `pl` wybiera przekład.

Tekst bazowy: SBLGNT dla NT, WLC dla protokanonicznego ST, LXX dla ksiąg deuterokanonicznych;
parametr `tekst` pozwala wybrać inny oryginał (np. LXX dla Rdz). Słowo bez glosy kontekstowej
(np. przed ukończeniem przebiegu gloss_tokens albo w LXX) dostaje pierwszy polski odpowiednik
z hasła słownika, oznaczony jako glosa słownikowa.
"""

from dataclasses import dataclass, field

from corpus.books import ParsedRef
from corpus.models import Book, Lexeme, Verse, VerseText, Work
from corpus.morph_pl import describe
from corpus.services import text as svc
from corpus.sigla import ordinal_range
from corpus.translit import greek_translit

ORIGINALS = ("SBLGNT", "WLC", "LXX")
TRANSLATIONS = ("SNPD", "BG1632")  # kolejność = pierwszeństwo
MAX_VERSES = 200


@dataclass
class Word:
    surface: str
    strong: str
    translit: str
    gloss: str
    dictionary_gloss: bool  # glosa z hasła słownika, nie z kontekstu
    morph: str
    morph_full: str
    lemma: str


@dataclass
class InterlinearVerse:
    verse: Verse
    words: list[Word] = field(default_factory=list)
    translation: str = ""
    translation_vt: VerseText | None = None  # z markup i przypisami (render_tokens)


@dataclass
class Interlinear:
    work: Work | None
    verses: list[InterlinearVerse]
    truncated: bool = False
    prev_ref: str = ""
    next_ref: str = ""
    # inne oryginały dla tych wersetów
    alternatives: list[str] = field(default_factory=list)
    # dzieła widoczne na stronie (nota o prawach)
    sources: list[Work] = field(default_factory=list)
    translation_code: str = ""
    translations: list[str] = field(default_factory=list)  # dostępne przekłady polskie


def default_work(book: Book) -> str:
    if book.testament == "NT":
        return "SBLGNT"
    return "LXX" if book.deuterocanonical else "WLC"


def _lexemes(tokens) -> tuple[dict[str, Lexeme], dict[str, Lexeme]]:
    strongs = {t.strong for t in tokens if t.strong}
    strongs |= {s.rstrip("abcdefghijklmnopqrstuvwxyz") for s in strongs}
    by_strong = {x.strong: x for x in Lexeme.objects.filter(strong__in=strongs)}
    lemmas = {t.lemma_norm for t in tokens if not t.strong and t.lemma_norm}
    by_lemma: dict[str, Lexeme] = {}
    for x in Lexeme.objects.filter(language="grc", lemma_norm__in=lemmas).order_by(
        "strong"
    ):
        by_lemma.setdefault(x.lemma_norm, x)
    return by_strong, by_lemma


def _word(t, language: str, by_strong, by_lemma) -> Word:
    lex = None
    if t.strong:
        lex = by_strong.get(t.strong) or by_strong.get(
            t.strong.rstrip("abcdefghijklmnopqrstuvwxyz")
        )
    elif t.lemma_norm:
        lex = by_lemma.get(t.lemma_norm)
    gloss, from_dict = t.gloss_pl, False
    if not gloss and lex is not None:
        gloss = (lex.gloss_pl or "").split(",")[0].strip()
        from_dict = bool(gloss)
    short, full = describe(language, t.pos, t.morph)
    return Word(
        surface=t.surface,
        strong=lex.strong if lex else t.strong,
        translit=greek_translit(t.surface) if language == "grc" else "",
        gloss=gloss,
        dictionary_gloss=from_dict,
        morph=short or t.morph,
        morph_full=full,
        lemma=t.lemma or (lex.lemma if lex else ""),
    )


def neighbours(refs: list[ParsedRef]) -> tuple[str, str]:
    """Siglum poprzedniego i następnego rozdziału (względem pierwszego i ostatniego sigli)."""
    first, last = refs[0], refs[-1]
    prev_ref = next_ref = ""
    start_ch = first.chapter
    end_ch = last.chapter_end or last.chapter
    prev_v = (
        Verse.objects.filter(book__order=first.book.order, chapter__lt=start_ch)
        .order_by("-ordinal")
        .select_related("book")
        .first()
        or Verse.objects.filter(book__order__lt=first.book.order)
        .order_by("-ordinal")
        .select_related("book")
        .first()
    )
    next_v = (
        Verse.objects.filter(book__order=last.book.order, chapter__gt=end_ch)
        .order_by("ordinal")
        .select_related("book")
        .first()
        or Verse.objects.filter(book__order__gt=last.book.order)
        .order_by("ordinal")
        .select_related("book")
        .first()
    )
    if prev_v:
        prev_ref = f"{prev_v.book.abbr} {prev_v.chapter}"
    if next_v:
        next_ref = f"{next_v.book.abbr} {next_v.chapter}"
    return prev_ref, next_ref


def build(
    refs: list[ParsedRef],
    work_code: str = "",
    *,
    access: list[str] | None = None,
    translation: str = "",
) -> Interlinear:
    first_book = Book.objects.get(osis=refs[0].book.osis)
    code = (
        work_code.upper()
        if work_code.upper() in ORIGINALS
        else default_work(first_book)
    )
    works = {
        w.code: w
        for w in svc.visible_works(
            list(Work.objects.filter(code__in=(*ORIGINALS, *TRANSLATIONS))),
            access=access,
        )
    }
    work = works.get(code)
    prev_ref, next_ref = neighbours(refs)
    if work is None:
        return Interlinear(None, [], prev_ref=prev_ref, next_ref=next_ref)
    translations = [c for c in TRANSLATIONS if c in works]
    pl = translation.upper()
    pl = pl if pl in translations else (translations[0] if translations else "")
    rows = svc.parallel(
        refs, [w for c, w in works.items() if c in (code, pl)], access=access
    )
    rows = [r for r in rows if code in r.texts]
    truncated = len(rows) > MAX_VERSES
    rows = rows[:MAX_VERSES]
    tokens = [t for r in rows for t in r.texts[code].tokens.all()]
    by_strong, by_lemma = _lexemes(tokens)
    verses = []
    for r in rows:
        vt = r.texts[code]
        words = [_word(t, work.language, by_strong, by_lemma) for t in vt.tokens.all()]
        tr = r.texts.get(pl)
        verses.append(InterlinearVerse(r.verse, words, tr.text if tr else "", tr))
    start, end = ordinal_range(refs[0])
    alternatives = sorted(
        set(
            VerseText.objects.filter(
                work__code__in=[c for c in ORIGINALS if c in works and c != code],
                verse__ordinal__range=(start, end),
            ).values_list("work__code", flat=True)
        )
    )
    sources = [work] + (
        [works[pl]] if pl and any(v.translation for v in verses) else []
    )
    return Interlinear(
        work,
        verses,
        truncated,
        prev_ref,
        next_ref,
        alternatives,
        sources,
        pl,
        translations,
    )
