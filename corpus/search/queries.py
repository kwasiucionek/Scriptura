"""Budowniczowie zapytań OpenSearch (czyste funkcje: LexicalQuery -> dict)."""

from dataclasses import dataclass, field
from html import escape

from corpus.normalize import normalize
from corpus.search.grammar import LexicalQuery
from corpus.search.mappings import STEM_FIELDS

TEXT_FIELDS = [*STEM_FIELDS, "text"]


def _match(text: str) -> dict:
    return {"multi_match": {"query": text, "fields": TEXT_FIELDS, "operator": "and"}}


def _phrase(text: str) -> dict:
    return {"multi_match": {"query": text, "fields": TEXT_FIELDS, "type": "phrase"}}


def _near(a: str, b: str, gap: int) -> dict:
    """a i b w obrębie `gap` słów, kolejność dowolna — intervals na każdym polu."""
    return {
        "bool": {
            "should": [
                {
                    "intervals": {
                        f: {
                            "all_of": {
                                "max_gaps": gap,
                                "ordered": False,
                                "intervals": [
                                    {"match": {"query": a}},
                                    {"match": {"query": b}},
                                ],
                            }
                        }
                    }
                }
                for f in TEXT_FIELDS
            ],
            "minimum_should_match": 1,
        }
    }


def _guess_language(text: str) -> str:
    import re

    if re.search(r"[\u0590-\u05ff]", text):
        return "hbo"
    if re.search(r"[\u0370-\u03ff\u1f00-\u1fff]", text):
        return "grc"
    return "pl"


def lexical_body(
    lq: LexicalQuery, work_codes: list[str], size: int = 200, from_: int = 0
) -> dict:
    must: list[dict] = []
    must += [_match(w) for w in lq.words]
    must += [_phrase(p) for p in lq.phrases]
    must += [_near(a, b, n) for a, b, n in lq.near]
    must += [{"term": {"strongs": s}} for s in lq.strongs]
    must += [
        {"term": {"lemmas": normalize(lem, _guess_language(lem))}} for lem in lq.lemmas
    ]
    must_not = [_match(x) for x in lq.excluded]

    return {
        "query": {
            "bool": {
                "must": must or [{"match_all": {}}],
                "must_not": must_not,
                "filter": [{"terms": {"work": work_codes}}],
            }
        },
        "sort": [{"ordinal": "asc"}, {"work": "asc"}],
        "highlight": {
            "pre_tags": ["<mark>"],
            "post_tags": ["</mark>"],
            "number_of_fragments": 0,
            "fields": {f: {} for f in TEXT_FIELDS},
        },
        "aggs": {
            "by_book": {
                "terms": {"field": "book", "size": 80, "order": {"min_order": "asc"}},
                "aggs": {"min_order": {"min": {"field": "book_order"}}},
            },
            "by_work": {"terms": {"field": "work", "size": 20}},
        },
        "size": size,
        "from": from_,
        "track_total_hits": True,
    }


@dataclass
class Hit:
    ref: str
    osis_id: str
    ordinal: int
    work: str
    language: str
    text: str
    html: str  # bezpieczny HTML z <mark>


@dataclass
class LexicalResult:
    total: int
    hits: list[Hit] = field(default_factory=list)
    by_book: list[dict] = field(default_factory=list)  # [{"abbr": "Mk", "n": 3}]
    by_work: list[dict] = field(default_factory=list)


def parse_response(resp: dict) -> LexicalResult:
    hits: list[Hit] = []
    for h in resp["hits"]["hits"]:
        src = h["_source"]
        hl = h.get("highlight", {})
        html = next((v[0] for v in hl.values() if v), None) or escape(src["text"])
        hits.append(
            Hit(
                ref=src["ref"],
                osis_id=src["osis_id"],
                ordinal=src["ordinal"],
                work=src["work"],
                language=src["language"],
                text=src["text"],
                html=html,
            )
        )
    aggs = resp.get("aggregations", {})
    by_book = [
        {"abbr": b["key"], "n": b["doc_count"]}
        for b in aggs.get("by_book", {}).get("buckets", [])
    ]
    by_work = [
        {"code": b["key"], "n": b["doc_count"]}
        for b in aggs.get("by_work", {}).get("buckets", [])
    ]
    total = resp["hits"]["total"]
    total = total["value"] if isinstance(total, dict) else total
    return LexicalResult(total=total, hits=hits, by_book=by_book, by_work=by_work)
