"""Testy czystych funkcji: gramatyka zapytań i DSL OpenSearch (bez klastra)."""

from corpus.search.grammar import parse_query
from corpus.search.mappings import VERSES_INDEX
from corpus.search.queries import lexical_body, parse_response


def test_grammar():
    lq = parse_query('król NEAR/3 Dawid "na początku" -niebo H430 lemma:λόγος Jezus')
    assert lq.near == [("król", "Dawid", 3)]
    assert lq.phrases == ["na początku"]
    assert lq.excluded == ["niebo"]
    assert lq.strongs == ["H430"]
    assert lq.lemmas == ["λόγος"]
    assert lq.words == ["Jezus"]


def test_grammar_near_default_and_edges():
    assert parse_query("a NEAR b").near == [("a", "b", 2)]
    assert parse_query("NEAR b").words == [
        "NEAR",
        "b",
    ]  # NEAR bez lewego operandu = słowo
    assert parse_query("").is_empty


def test_lexical_body_shape():
    body = lexical_body(
        parse_query("Bóg NEAR/2 niebo -ziemia H430 lemma:ἀρχή"), ["BG1632", "WLC"]
    )
    must = body["query"]["bool"]["must"]
    kinds = [next(iter(m)) for m in must]
    assert kinds == ["bool", "term", "term"]  # near, strong, lemma
    assert must[1] == {"term": {"strongs": "H430"}}
    assert must[2] == {
        "term": {"lemmas": "αρχη"}
    }  # lemat znormalizowany jak w indeksie
    near = must[0]["bool"]["should"][0]["intervals"]
    field, spec = next(iter(near.items()))
    assert (
        field == "text_pl"
        and spec["all_of"]["max_gaps"] == 2
        and spec["all_of"]["ordered"] is False
    )
    assert body["query"]["bool"]["must_not"][0]["multi_match"]["query"] == "ziemia"
    assert body["query"]["bool"]["filter"] == [{"terms": {"work": ["BG1632", "WLC"]}}]
    assert body["highlight"]["number_of_fragments"] == 0
    assert "by_book" in body["aggs"]


def test_parse_response():
    resp = {
        "hits": {
            "total": {"value": 1},
            "hits": [
                {
                    "_source": {
                        "ref": "Rdz 1,1",
                        "osis_id": "Gen.1.1",
                        "ordinal": 1001001,
                        "work": "BG1632",
                        "language": "pl",
                        "text": "Na początku stworzył Bóg niebo i ziemię.",
                    },
                    "highlight": {
                        "text_pl": [
                            "Na początku stworzył <mark>Bóg</mark> niebo i ziemię."
                        ]
                    },
                }
            ],
        },
        "aggregations": {
            "by_book": {"buckets": [{"key": "Rdz", "doc_count": 1}]},
            "by_work": {"buckets": [{"key": "BG1632", "doc_count": 1}]},
        },
    }
    r = parse_response(resp)
    assert r.total == 1 and r.hits[0].html.count("<mark>") == 1
    assert r.by_book == [{"abbr": "Rdz", "n": 1}]


def test_mapping_is_strict_and_has_language_fields():
    props = VERSES_INDEX["mappings"]["properties"]
    assert VERSES_INDEX["mappings"]["dynamic"] == "strict"
    for f in (
        "text",
        "text_pl",
        "text_grc",
        "text_hbo",
        "lemmas",
        "strongs",
        "forms",
        "access",
    ):
        assert f in props
    analyzers = VERSES_INDEX["settings"]["analysis"]["analyzer"]
    assert "polish_stem" in analyzers["pl_stem"]["filter"]
    assert "icu_folding" in analyzers["grc_fold"]["filter"]
