"""Budowa dokumentów indeksu `verses` z bazy i zapis do OpenSearch."""

import logging
from collections.abc import Iterator

from opensearchpy import helpers

from corpus.models import Token, VerseText, Work
from corpus.search.client import get_client, index_name
from corpus.search.mappings import LANGUAGE_FIELD, VERSES_INDEX

log = logging.getLogger(__name__)


def ensure_index(recreate: bool = False) -> str:
    client = get_client()
    name = index_name("verses")
    if recreate and client.indices.exists(index=name):
        client.indices.delete(index=name)
    if not client.indices.exists(index=name):
        client.indices.create(index=name, body=VERSES_INDEX)
        log.info("Utworzono indeks %s", name)
    return name


def _denormalized_tokens() -> dict[int, dict[str, set[str]]]:
    """verse_id -> {lemmas, strongs, forms} ze WSZYSTKICH dzieł (do zapytań międzykorpusowych)."""
    acc: dict[int, dict[str, set[str]]] = {}
    rows = Token.objects.values_list(
        "verse_text__verse_id", "lemma_norm", "strong", "surface_norm"
    ).iterator(chunk_size=20000)
    for verse_id, lemma, strong, form in rows:
        d = acc.setdefault(
            verse_id, {"lemmas": set(), "strongs": set(), "forms": set()}
        )
        if lemma:
            d["lemmas"].add(lemma)
        if strong:
            d["strongs"].add(strong)
        if form:
            d["forms"].add(form)
    return acc


def build_docs(works: list[Work] | None = None) -> Iterator[dict]:
    name = index_name("verses")
    denorm = _denormalized_tokens()
    qs = VerseText.objects.select_related("verse__book", "work").order_by(
        "verse__ordinal", "work__code"
    )
    if works:
        qs = qs.filter(work__in=works)
    for vt in qs.iterator(chunk_size=5000):
        v, w = vt.verse, vt.work
        tok = denorm.get(v.id, {})
        doc = {
            "osis_id": v.osis_id,
            "ordinal": v.ordinal,
            "book": v.book.abbr,
            "book_order": v.book.order,
            "chapter": v.chapter,
            "verse": v.verse,
            "ref": str(v),
            "work": w.code,
            "language": w.language,
            "access": w.access,
            "text": vt.text,
            "lemmas": sorted(tok.get("lemmas", ())),
            "strongs": sorted(tok.get("strongs", ())),
            "forms": sorted(tok.get("forms", ())),
        }
        field = LANGUAGE_FIELD.get(w.language)
        if field:
            doc[field] = vt.text
        yield {"_index": name, "_id": f"{v.osis_id}|{w.code}", "_source": doc}


def reindex(works: list[Work] | None = None, recreate: bool = False) -> int:
    ensure_index(recreate=recreate)
    client = get_client()
    ok, _ = helpers.bulk(
        client, build_docs(works), chunk_size=1000, request_timeout=120
    )
    client.indices.refresh(index=index_name("verses"))
    return ok
