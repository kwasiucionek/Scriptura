from unittest import mock

import pytest

from library.models import Chunk
from library.rerank import rerank
from library.search import ChunkHit, bm25_body, original_terms


def _hit(i, text="tekst"):
    return ChunkHit(
        chunk_id=i,
        document_id=1,
        order=i,
        title="T",
        authors=[],
        citation="c",
        doc_type="article",
        year=None,
        url="",
        section="",
        text=text,
        sigla=[],
        score=1.0 / (i + 1),
        access="open",
    )


def test_original_terms():
    grc, hbo = original_terms("Słowo λόγος i ἀρχή, hebr. חֶסֶד oraz בְּרֵאשִׁית; a NEAR/2 b")
    assert grc == ["αρχη", "λογοσ"] and hbo == ["בראשית", "חסד"]


def test_bm25_body_boosts_original_terms():
    body = bm25_body("znaczenie ἀγάπη u Pawła", 10, [{"terms": {"access": ["open"]}}])
    should = body["query"]["bool"]["should"]
    assert should == [{"term": {"terms_grc": {"value": "αγαπη", "boost": 3.0}}}]


def test_rerank_none_backend_keeps_order(settings):
    settings.RERANKER_BACKEND = "none"
    hits = [_hit(0), _hit(1), _hit(2)]
    assert [h.order for h in rerank("q", hits, 2)] == [0, 1]


def test_rerank_tei_reorders(settings):
    settings.RERANKER_BACKEND = "tei"
    settings.RERANK_TOP_N = 3
    hits = [_hit(0), _hit(1), _hit(2), _hit(3)]
    with mock.patch("library.rerank._tei_rerank", return_value=[0.5, 0.1, 0.9]):
        out = rerank("q", hits, 2)
    assert [h.order for h in out] == [2, 0]
    assert out[0].score == pytest.approx(0.9)


def test_rerank_tei_failure_falls_back(settings):
    settings.RERANKER_BACKEND = "tei"
    hits = [_hit(0), _hit(1)]
    with mock.patch("library.rerank._tei_rerank", side_effect=OSError("down")):
        assert [h.order for h in rerank("q", hits, 2)] == [0, 1]


def test_chunks_mapping_has_fold_field():
    from library.search import CHUNKS_INDEX

    assert CHUNKS_INDEX["mappings"]["properties"]["text_fold"]["analyzer"] == "fold"
    an = CHUNKS_INDEX["settings"]["analysis"]
    assert (
        "translit" in an["char_filter"]
        and "š=>sz" in an["char_filter"]["translit"]["mappings"]
    )
    assert an["analyzer"]["fold"]["char_filter"] == ["translit"]


def test_bm25_body_searches_fold_field():
    body = bm25_body("miszkan", 5, [])
    clause = body["query"]["bool"]["must"][0]["bool"]["should"][0]
    assert "text_fold" in clause["multi_match"]["fields"]


def test_bm25_body_with_english_query():
    body = bm25_body("powtórzenia w poezji", 10, [], query_en="repetition in poetry")
    must = body["query"]["bool"]["must"][0]["bool"]
    assert must["minimum_should_match"] == 1
    assert must["should"][1] == {
        "match": {"text_en": {"query": "repetition in poetry", "boost": 1.5}}
    }
    assert "text_en" not in str(
        bm25_body("powtórzenia", 10, [])["query"]["bool"]["must"]
    )


def test_translate_query_skips_when_disabled(settings):
    from library.translate import looks_polish, translate_query

    settings.RAG_TRANSLATE_QUERY = False
    translate_query.cache_clear()
    assert translate_query("Jakie powtórzenia występują w poezji?") == ""
    assert looks_polish("Jakie powtórzenia?") and not looks_polish(
        "What repetitions occur in poetry?"
    )


def test_translated_chunk_is_indexed_in_polish_but_kept_in_english(db):
    from library.models import Document
    from library.search import build_docs

    d = Document.objects.create(title="Repetition", language="en")
    Chunk.objects.create(
        document=d,
        order=0,
        text="Repetition is the determinant of poetry.",
        text_pl="Powtórzenie jest wyznacznikiem poezji.",
        sigla=[],
    )
    (doc,) = build_docs(d, None, index="x")
    src = doc["_source"]
    assert src["text"].startswith("Powtórzenie")
    assert (
        src["text_fold"].startswith("Repetition") and "Powtórzenie" in src["text_fold"]
    )
    assert src["text_en"].startswith("Repetition") and src["text_exact"].startswith(
        "Repetition"
    )


def test_translate_prompt_formats():
    from library.management.commands.translate_chunks import build_prompt

    p = build_prompt("Hello", "en", "translategemma:27b")
    assert p.endswith("\n\n\nHello") and "English (en) to Polish (pl)" in p
    assert "Keep unchanged" not in p
    g = build_prompt("Hello", "de", "gemma4:31b-cloud")
    assert (
        "German (de) to Polish (pl)" in g
        and "Keep unchanged" in g
        and g.endswith("\n\nHello")
    )
