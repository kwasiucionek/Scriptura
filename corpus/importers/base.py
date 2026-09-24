"""Wspólny mechanizm importu: rekordy -> Verse (kanon) -> VerseText -> Token.

Importery formatów (oshb, morphgnt, json_bible) tylko produkują `VerseRecord`;
cała logika zapisu i deduplikacji jest tutaj.
"""

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from django.db import transaction

from corpus.books import BY_OSIS
from corpus.models import Book, Token, Verse, VerseText, Work
from corpus.normalize import normalize

log = logging.getLogger(__name__)

BATCH = 2000


@dataclass
class TokenRecord:
    surface: str
    lemma: str = ""
    strong: str = ""
    morph: str = ""
    pos: str = ""
    prefixes: str = ""


@dataclass
class VerseRecord:
    osis: str
    chapter: int
    verse: int
    text: str
    tokens: list[TokenRecord] = field(default_factory=list)


class VerseIndex:
    """Mapa (osis, chapter, verse) -> Verse.id; dotwarza brakujące wersety."""

    def __init__(self) -> None:
        self._ids: dict[tuple[str, int, int], int] = {
            (b, c, v): pk
            for pk, b, c, v in Verse.objects.values_list(
                "id", "book_id", "chapter", "verse"
            )
        }

    def ensure(self, records: Iterable[VerseRecord]) -> None:
        missing: list[Verse] = []
        seen: set[tuple[str, int, int]] = set()
        for r in records:
            key = (r.osis, r.chapter, r.verse)
            if key in self._ids or key in seen:
                continue
            seen.add(key)
            spec = BY_OSIS[r.osis]
            missing.append(
                Verse(
                    book_id=r.osis,
                    chapter=r.chapter,
                    verse=r.verse,
                    ordinal=Verse.make_ordinal(spec.order, r.chapter, r.verse),
                    osis_id=f"{r.osis}.{r.chapter}.{r.verse}",
                )
            )
        if missing:
            Verse.objects.bulk_create(missing, batch_size=BATCH)
            for v in Verse.objects.filter(
                osis_id__in=[m.osis_id for m in missing]
            ).values_list("id", "book_id", "chapter", "verse"):
                self._ids[(v[1], v[2], v[3])] = v[0]

    def get(self, r: VerseRecord) -> int:
        return self._ids[(r.osis, r.chapter, r.verse)]


def _chunks(it: Iterator[VerseRecord], size: int) -> Iterator[list[VerseRecord]]:
    buf: list[VerseRecord] = []
    for item in it:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def seed_books() -> int:
    """Załaduj tabelę Book z corpus.books.BOOKS (idempotentnie)."""
    from corpus.books import BOOKS

    created = 0
    for spec in BOOKS:
        _, was_created = Book.objects.update_or_create(
            osis=spec.osis,
            defaults={
                "order": spec.order,
                "testament": spec.testament,
                "abbr": spec.abbr,
                "name_pl": spec.name_pl,
                "deuterocanonical": spec.deuterocanonical,
            },
        )
        created += int(was_created)
    return created


@transaction.atomic
def import_records(
    work: Work, records: Iterator[VerseRecord], replace: bool = True
) -> dict:
    """Zapisz rekordy dla danego dzieła. `replace=True` usuwa poprzedni import."""
    seed_books()
    if replace:
        deleted, _ = VerseText.objects.filter(work=work).delete()
        if deleted:
            log.info("Usunięto %s poprzednich rekordów dla %s", deleted, work.code)

    index = VerseIndex()
    stats = {"verses": 0, "tokens": 0}

    for chunk in _chunks(records, BATCH):
        index.ensure(chunk)
        texts = [
            VerseText(
                verse_id=index.get(r),
                work=work,
                text=r.text,
                text_norm=normalize(r.text, work.language),
            )
            for r in chunk
        ]
        texts = VerseText.objects.bulk_create(texts, batch_size=BATCH)
        stats["verses"] += len(texts)

        tokens: list[Token] = []
        for vt, r in zip(texts, chunk, strict=True):
            for pos, t in enumerate(r.tokens, start=1):
                tokens.append(
                    Token(
                        verse_text=vt,
                        position=pos,
                        surface=t.surface[:64],
                        surface_norm=normalize(t.surface, work.language)[:64],
                        lemma=t.lemma[:64],
                        lemma_norm=normalize(t.lemma, work.language)[:64]
                        if t.lemma
                        else "",
                        strong=t.strong[:12],
                        morph=t.morph[:32],
                        pos=t.pos[:8],
                        prefixes=t.prefixes[:16],
                    )
                )
        if tokens:
            Token.objects.bulk_create(tokens, batch_size=BATCH)
            stats["tokens"] += len(tokens)

    log.info("%s: %s wersetów, %s tokenów", work.code, stats["verses"], stats["tokens"])
    return stats
