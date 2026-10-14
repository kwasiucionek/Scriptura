"""Iteracja po dużych querysetach bez otwartego kursora w czasie zapisów.

SQLite w trybie WAL: `qs.iterator()` trzyma otwarty odczyt (migawkę bazy). Jeśli w tym czasie inny
proces coś zapisze (strona: rozmowa, sesja; inna komenda), zapis z tej samej pętli dostaje od razu
„database is locked” (SQLITE_BUSY_SNAPSHOT) — `timeout` w OPTIONS nie pomaga, bo SQLite nie czeka.
Dlatego najpierw lista kluczy, potem partie wczytywane w całości; zapisy idą przy zamkniętym odczycie.
"""

from collections.abc import Iterator

from django.db.models import Model, QuerySet


def in_batches(qs: QuerySet, size: int = 50, limit: int = 0) -> Iterator[Model]:
    """Obiekty z `qs` w jego kolejności (z select_related), partiami po `size`; `limit` > 0 ucina listę."""
    ids = list(qs.values_list("pk", flat=True))
    if limit:
        ids = ids[:limit]
    for start in range(0, len(ids), size):
        part = ids[start : start + size]
        objs = {o.pk: o for o in qs.filter(pk__in=part)}  # partia wczytana w całości
        for pk in part:
            if pk in objs:  # usunięte w międzyczasie — pomijamy
                yield objs[pk]
