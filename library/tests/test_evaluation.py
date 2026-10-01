"""Evaluation contract tests: no model, network or corpus/database evaluation."""

import json
import subprocess
import sys
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from library import evaluation, search
from library.evaluation import (
    EvaluationCase,
    aggregate_results,
    evaluate_cases,
    load_dataset,
)
from rag import service
from rag.quotes import check_citations


@pytest.fixture(autouse=True)
def backends(monkeypatch):
    retrieve = Mock(side_effect=AssertionError("Unexpected retrieval"))
    ask = Mock(side_effect=AssertionError("Unexpected RAG"))
    monkeypatch.setattr(search, "retrieve", retrieve)
    monkeypatch.setattr(service, "ask", ask)
    search.LAST_TIMINGS.clear()
    yield retrieve, ask
    search.LAST_TIMINGS.clear()


def dataset(tmp_path, rows):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n" + "\n\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def case(case_id="one", **kwargs):
    return EvaluationCase(id=case_id, q="pytanie", relevant=["1:2"], **kwargs)


def hit(document_id, order):
    return SimpleNamespace(document_id=document_id, order=order, text="source text")


def done(**kwargs):
    return asdict(service.AskResult(**kwargs))


def mock_ask(backends, payload, sources=None):
    _, ask = backends
    ask.side_effect = lambda *a, **kw: iter(
        [
            ("sources", sources or {"chunks": []}),
            ("delta", {"text": "ignored delta"}),
            ("done", payload),
        ]
    )
    return ask


def test_import_needs_neither_mlflow_nor_django():
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'mlflow', 'django', 'rag', 'corpus'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from library.evaluation import load_dataset, evaluate_cases, aggregate_results
assert aggregate_results([])['all']['count'] == 0
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_load_legacy_and_optional_fields(tmp_path, backends):
    path = dataset(
        tmp_path,
        [
            {
                "q": "  Co oznacza ḥesed?  ",
                "relevant": ["012:03", "12:3"],
                "authors": [" Jan Kowalski "],
                "works": ["BHS"],
                "expected_refused": False,
                "required_terms": ["ḥesed"],
                "include_ane": False,
                "include_patristics": True,
                "mode": "popular",
                "note": "prywatna adnotacja",
            }
        ],
    )
    loaded = load_dataset(path)
    assert len(loaded) == 1
    row = loaded[0]
    assert row.id.startswith("case-") and len(row.id) == 69
    assert row.q == "Co oznacza ḥesed?" and row.type == "pl"
    assert row.relevant == ["12:3"] and row.authors == ["Jan Kowalski"]
    assert row.works == ["BHS"] and row.expected_refused is False
    assert row.required_terms == ["ḥesed"]
    assert row.include_ane is False and row.include_patristics is True
    assert row.mode == "popular" and row.note == "prywatna adnotacja"
    for backend in backends:
        backend.assert_not_called()


def test_generated_id_stable_across_paths_positions_key_order_and_note(tmp_path):
    row = {"q": "pytanie", "relevant": ["1:2"], "note": "first"}
    first = load_dataset(dataset(tmp_path, [row]))[0].id
    other = tmp_path / "other.jsonl"
    other.write_text(
        '\n\n{"note":"changed","relevant":["1:2"],"q":"pytanie","type":"pl"}\n',
        encoding="utf-8",
    )
    assert load_dataset(other)[0].id == first


@pytest.mark.parametrize("annotation", [{}, {"relevant": []}, {"relevant": ["1:0"]}])
def test_rag_relevance_can_be_unlabeled(tmp_path, annotation):
    loaded = load_dataset(dataset(tmp_path, [{"q": "test", **annotation}]), task="rag")
    assert loaded[0].relevant == annotation.get("relevant", [])
    assert loaded[0].include_ane is None and loaded[0].include_patristics is None


@pytest.mark.parametrize(
    "update",
    [
        {"q": " "},
        {"q": None},
        {"q": 2},
        {"type": "all"},
        {"type": " ALL "},
        {"type": []},
        {"type": ""},
        {"relevant": []},
        {"relevant": None},
        {"relevant": "1:2"},
        {"relevant": [1]},
        {"relevant": ["x:2"]},
        {"relevant": ["0:2"]},
        {"relevant": ["1:-1"]},
        {"relevant": ["1:2:3"]},
        {"authors": [1]},
        {"authors": "Name"},
        {"authors": [" "]},
        {"works": [False]},
        {"works": "BHS"},
        {"required_terms": [2]},
        {"required_terms": [""]},
        {"required_terms": None},
        {"expected_refused": 0},
        {"expected_refused": "false"},
        {"include_ane": 1},
        {"include_patristics": "true"},
        {"mode": "unknown"},
        {"mode": []},
        {"note": {}},
        {"id": 1},
        {"id": " "},
        {"id": None},
    ],
)
def test_invalid_fields(tmp_path, update, backends):
    with pytest.raises(ValueError, match="line 2:"):
        load_dataset(dataset(tmp_path, [{"q": "test", "relevant": ["1:2"], **update}]))
    for backend in backends:
        backend.assert_not_called()


@pytest.mark.parametrize("row", [[], "text", 42, None])
def test_rows_must_be_objects(tmp_path, row):
    with pytest.raises(ValueError, match="JSON object"):
        load_dataset(dataset(tmp_path, [row]))


def test_missing_question_and_invalid_json_are_safe(tmp_path):
    with pytest.raises(ValueError, match="q must"):
        load_dataset(dataset(tmp_path, [{"relevant": ["1:2"]}]))
    path = tmp_path / "bad.jsonl"
    path.write_text('{"private-secret": invalid}', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1: invalid JSON") as exc:
        load_dataset(path)
    assert "private-secret" not in str(exc.value)


@pytest.mark.parametrize("explicit", [True, False])
def test_duplicate_ids_even_beyond_limit(tmp_path, explicit):
    row = {"q": "pytanie", "relevant": ["1:2"]}
    if explicit:
        row["id"] = "same"
    second = {**row, "note": "different note"}
    if explicit:
        second["q"] = "inne pytanie"
    with pytest.raises(ValueError, match="duplicate case id"):
        load_dataset(dataset(tmp_path, [row, second]), limit=1)


def test_limit_only_after_full_validation(tmp_path):
    path = dataset(
        tmp_path,
        [
            {"q": "one", "relevant": ["1:2"]},
            {"q": "two", "relevant": ["2:3"]},
        ],
    )
    assert len(load_dataset(path, limit=1)) == 1
    assert len(load_dataset(path)) == 2
    with pytest.raises(ValueError, match="retrieval requires"):
        load_dataset(
            dataset(
                tmp_path,
                [
                    {"q": "one", "relevant": ["1:2"]},
                    {"q": "bad"},
                ],
            ),
            limit=1,
        )


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "1"])
def test_invalid_limit(tmp_path, limit):
    with pytest.raises(ValueError, match="limit"):
        load_dataset(dataset(tmp_path, []), limit=limit)


def test_empty_dataset_and_missing_file(tmp_path):
    assert load_dataset(dataset(tmp_path, [])) == []
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path / "missing.jsonl")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"task": "unknown"},
        {"k": 0},
        {"k": True},
        {"k": 1.5},
        {"mode": "bad"},
        {"include_content": 1},
    ],
)
def test_invalid_evaluate_arguments(kwargs, backends):
    with pytest.raises(ValueError):
        evaluate_cases([case()], **{"task": "retrieval", **kwargs})
    for backend in backends:
        backend.assert_not_called()


@pytest.mark.parametrize("task", ["rag", "retrieval"])
@pytest.mark.parametrize(
    "bad",
    [
        EvaluationCase(id="bad", q=" ", relevant=["1:2"]),
        EvaluationCase(id="one", q="duplicate", relevant=["1:2"]),
        EvaluationCase(id="bad", q="test", type="all", relevant=["1:2"]),
        {"id": "not-a-dataclass"},
    ],
)
def test_validate_all_cases_before_backend_calls(task, bad, backends):
    with pytest.raises(ValueError):
        evaluate_cases(iter([case(), bad]), task)
    for backend in backends:
        backend.assert_not_called()


def test_retrieval_matches_script_and_explicit_acl(backends, monkeypatch, settings):
    from corpus.sigla import extract, ordinal_range

    retrieve, ask = backends
    settings.RAG_ACCESS = ["private", "licensed", "personal"]
    monkeypatch.setattr(
        service, "diversify", Mock(side_effect=AssertionError("No diversify"))
    )

    def fake_retrieve(*args, **kwargs):
        search.LAST_TIMINGS.update(embed_ms=2, search_ms=3, rerank_ms=4)
        return [hit(9, 9), hit(1, 2), hit(1, 2), hit(2, 4)]

    retrieve.side_effect = fake_retrieve
    row = EvaluationCase(
        id="sigla",
        q="Mk 16,9-20",
        type="pl+orig",
        relevant=["1:2", "2:3"],
        authors=["Jan Kowalski"],
        works=["BHS"],
        note="not in result",
    )
    result = evaluate_cases([row], "retrieval", k=4)[0]
    metrics = result["metrics"]
    assert metrics["recall_at_k"] == 0.5  # duplicate exact hit counts once
    assert metrics["near_at_k"] == 1.0
    assert metrics["mrr_at_k"] == 0.5 and metrics["hit_at_1"] == 0.0
    assert metrics["success"] == 1.0 and metrics["latency_ms"] >= 0
    assert metrics["embed_ms"] == 2.0 and metrics["rerank_ms"] == 4.0
    assert all(type(value) is float for value in metrics.values())
    assert result["output"] == {"retrieved_ids": ["9:9", "1:2", "1:2", "2:4"]}
    assert result["id"] == "sigla" and result["type"] == "pl+orig"
    assert result["question"] == row.q and result["error"] is None
    assert "not in result" not in json.dumps(result)
    sigla = [ordinal_range(ref) for match in extract(row.q) for ref in match.refs]
    retrieve.assert_called_once_with(
        row.q,
        k=4,
        access=["open"],
        authors=row.authors,
        sigla=sigla,
        user_id=None,
        personal_only=False,
    )
    ask.assert_not_called()


@pytest.mark.parametrize(
    "hits, expected",
    [
        ([], (0.0, 0.0, 0.0, 0.0)),
        ([hit(2, 2)], (0.0, 0.0, 0.0, 0.0)),  # other document is not near
        ([hit(1, 3)], (0.0, 1.0, 0.0, 0.0)),  # neighbor is near, not recall
        ([hit(1, 2)], (1.0, 1.0, 1.0, 1.0)),
    ],
)
def test_retrieval_metric_edges(backends, hits, expected):
    retrieve, _ = backends
    retrieve.side_effect = lambda *a, **kw: hits
    metrics = evaluate_cases([case()], "retrieval")[0]["metrics"]
    assert (
        tuple(
            metrics[name]
            for name in (
                "recall_at_k",
                "near_at_k",
                "mrr_at_k",
                "hit_at_1",
            )
        )
        == expected
    )
    assert metrics["success"] == 1.0
    assert retrieve.call_args.kwargs["sigla"] is None


def test_content_opt_in_and_no_stale_stage_times(backends):
    retrieve, _ = backends
    retrieve.side_effect = lambda *a, **kw: [hit(1, 2)]
    search.LAST_TIMINGS["embed_ms"] = 999
    result = evaluate_cases([case()], "retrieval", include_content=True)[0]
    assert result["output"]["content"] == ["source text"]
    assert "embed_ms" not in result["metrics"]


def test_latency_is_measured_not_taken_from_backend(backends, monkeypatch):
    mock_ask(backends, done(answer="ok", latency_ms=9999, timings={"latency_ms": 9999}))
    times = iter([100.0, 100.025])
    monkeypatch.setattr(evaluation.time, "monotonic", lambda: next(times))
    assert evaluate_cases([case()], "rag")[0]["metrics"]["latency_ms"] == pytest.approx(
        25
    )


def test_rag_sources_order_metrics_and_filter_forwarding(backends, settings):
    settings.RAG_ACCESS = ["personal", "private"]
    answer = "ḤESED i STRASSE [1,2–3] [1] [P1–P2] [A9] [3–1] [1, P1–A2]"
    checks = [
        asdict(check) for check in check_citations(answer, {"[1]", "[2]", "[P1]"})
    ]
    payload = done(
        answer=answer,
        refused=False,
        quotes_checked=4,
        quotes_verified=2,
        quotes_altered=[{"quote": "altered"}],
        quotes_unverified=[{"quote": "missing"}],
        citation_checks=checks,
        usage={"prompt_tokens": 20, "completion_tokens": 10},
        timings={"llm_ms": 8, "retrieval_ms": 4, "embed_ms": 1},
    )
    sources = {
        "chunks": [
            {"n": 1, "document_id": 9, "snippet": "first"},
            {"n": 2, "document_id": 1, "snippet": "second"},
        ]
    }
    ask = mock_ask(backends, payload, sources)
    row = case(
        authors=["Author Name"],
        works=[],
        expected_refused=False,
        required_terms=["ḥesed", "Straße", "absent"],
        include_ane=False,
        include_patristics=True,
        mode="popular",
    )
    result = evaluate_cases([row], "rag", k=1)[0]
    metrics = result["metrics"]
    assert result["output"] == {"result": payload, "sources": sources}
    assert metrics["answer_nonempty"] == metrics["success"] == 1.0
    assert metrics["quotes_checked"] == 4 and metrics["quotes_verified"] == 2
    assert metrics["quotes_altered"] == metrics["quotes_unverified"] == 1
    assert metrics["quote_verification_rate"] == 0.5
    assert metrics["citation_checked_count"] == 9
    assert (
        metrics["citation_valid_count"] == 4 and metrics["citation_invalid_count"] == 5
    )
    assert metrics["citation_validity_rate"] == pytest.approx(4 / 9)
    assert metrics["refused"] == 0.0 and metrics["refusal_accuracy"] == 1.0
    assert metrics["required_terms_coverage"] == pytest.approx(2 / 3)
    assert metrics["prompt_tokens"] == 20.0 and metrics["completion_tokens"] == 10.0
    assert metrics["llm_ms"] == 8.0 and metrics["retrieval_ms"] == 4.0
    for name in ("recall_at_k", "near_at_k", "mrr_at_k", "hit_at_1"):
        assert name not in metrics  # even labeled RAG cannot infer chunk IDs
    ask.assert_called_once_with(
        row.q,
        authors=row.authors,
        works=[],
        mode="popular",
        access=["open"],
        user_id=None,
        personal_only=False,
        include_ane=False,
        include_patristics=True,
    )
    backends[0].assert_not_called()


@pytest.mark.parametrize("relevant", [[], ["1:2"]])
def test_rag_applicability_and_no_fabricated_rates(backends, relevant):
    ask = mock_ask(backends, done(answer=" "))
    row = EvaluationCase(id="one", q="test", relevant=relevant)
    metrics = evaluate_cases([row], "rag")[0]["metrics"]
    assert metrics["success"] == 1.0 and metrics["answer_nonempty"] == 0.0
    assert metrics["quotes_checked"] == metrics["quotes_verified"] == 0.0
    assert metrics["quotes_altered"] == metrics["quotes_unverified"] == 0.0
    for name in (
        "quote_verification_rate",
        "citation_validity_rate",
        "citation_checked_count",
        "refusal_accuracy",
        "required_terms_coverage",
        "prompt_tokens",
        "recall_at_k",
    ):
        assert name not in metrics
    assert ask.call_args.kwargs["works"] is None
    assert ask.call_args.kwargs["mode"] == "scientific"
    assert ask.call_args.kwargs["include_ane"] is None
    assert ask.call_args.kwargs["include_patristics"] is None


@pytest.mark.parametrize(
    "refused, expected, accuracy",
    [
        (True, True, 1.0),
        (False, False, 1.0),
        (True, False, 0.0),
        (False, True, 0.0),
    ],
)
def test_refusal_accuracy_only_for_labeled_cases(backends, refused, expected, accuracy):
    mock_ask(backends, done(answer="Brak źródeł", refused=refused))
    metrics = evaluate_cases([case(expected_refused=expected)], "rag", mode="popular")[
        0
    ]["metrics"]
    assert metrics["refusal_accuracy"] == accuracy
    assert metrics["refused"] == float(refused)
    assert backends[1].call_args.kwargs["mode"] == "popular"


def test_legacy_citation_lists_are_not_substitutes_for_checks(backends):
    mock_ask(
        backends,
        {
            "answer": "[1]",
            "verified_citations": ["[1]"],
            "unverified_citations": ["[9]"],
            "usage": {"prompt_tokens": 0, "completion_tokens": None},
            "timings": {
                "llm_ms": 0,
                "bad_ms": float("nan"),
                "negative_ms": -1,
                "other": 9,
            },
        },
    )
    metrics = evaluate_cases([case()], "rag")[0]["metrics"]
    assert "citation_validity_rate" not in metrics
    assert metrics["prompt_tokens"] == 0.0 and "completion_tokens" not in metrics
    assert metrics["llm_ms"] == 0.0
    assert not {"bad_ms", "negative_ms", "other"} & metrics.keys()


def test_retrieval_failure_safe_and_continues(backends):
    retrieve, _ = backends
    retrieve.side_effect = [RuntimeError("private endpoint/token"), [hit(1, 2)]]
    results = evaluate_cases([case("bad"), case("good")], "retrieval")
    assert [row["id"] for row in results] == ["bad", "good"]
    assert results[0]["error"] == "evaluation_failed" and results[0]["output"] == {}
    assert set(results[0]["metrics"]) == {"success", "latency_ms"}
    assert results[0]["metrics"]["success"] == 0.0
    assert "private endpoint/token" not in json.dumps(results)
    assert results[1]["metrics"]["success"] == 1.0


@pytest.mark.parametrize(
    "failure", ["error", "exception", "missing_done", "after_done", "duplicate_done"]
)
def test_rag_failures_discard_partial_output_and_continue(backends, failure):
    _, ask = backends

    def broken():
        yield "sources", {"chunks": [{"snippet": "private partial source"}]}
        yield "delta", {"text": "private partial answer"}
        if failure == "error":
            yield "error", {"detail": "private error"}
        elif failure == "exception":
            raise RuntimeError("private exception")
        elif failure in ("after_done", "duplicate_done"):
            yield "done", done(answer="private partial answer")
            if failure == "after_done":
                yield "error", {"detail": "private error"}
            else:
                yield "done", done(answer="duplicate")

    ask.side_effect = [broken(), iter([("done", done(answer="ok"))])]
    results = evaluate_cases([case("bad"), case("good")], "rag")
    assert results[0]["output"] == {} and results[0]["error"] == "evaluation_failed"
    assert results[0]["metrics"]["success"] == 0.0
    assert set(results[0]["metrics"]) == {"success", "latency_ms"}
    assert "private" not in json.dumps(results)
    assert results[1]["metrics"]["success"] == 1.0
    assert ask.call_count == 2


def test_aggregate_applicable_macro_means_counts_and_raw_types(backends):
    _, ask = backends
    ask.side_effect = [
        iter([("done", done(answer="yes", quotes_checked=2, quotes_verified=1))]),
        iter([("done", done(answer="yes", quotes_checked=4, quotes_verified=4))]),
        iter([("done", done(answer="no quotes"))]),
        iter([("error", {"detail": "hidden"})]),
    ]
    results = evaluate_cases(
        [
            case("a", type="pl"),
            case("b", type="pl"),
            case("c", type="pl->en / custom"),
            case("d", type="pl"),
        ],
        "rag",
    )
    aggregate = aggregate_results(iter(results))
    overall = aggregate["all"]
    assert overall["count"] == 4 and overall["success_count"] == 3
    assert overall["error_count"] == 1 and overall["metrics"]["success"] == 0.75
    assert overall["metrics"]["quote_verification_rate"] == 0.75  # macro, not 5/6
    assert overall["metric_counts"]["quote_verification_rate"] == 2
    assert overall["metric_counts"]["answer_nonempty"] == 3
    assert (
        overall["metric_counts"]["success"]
        == overall["metric_counts"]["latency_ms"]
        == 4
    )
    groups = aggregate["by_type"]
    assert set(groups) == {"pl", "pl->en / custom"}
    assert sum(group["count"] for group in groups.values()) == overall["count"]
    assert groups["pl"]["success_count"] == 2 and groups["pl"]["error_count"] == 1
    assert "quote_verification_rate" not in groups["pl->en / custom"]["metrics"]


def test_empty_results():
    assert evaluate_cases([], "retrieval") == []
    assert aggregate_results([]) == {
        "all": {
            "count": 0,
            "success_count": 0,
            "error_count": 0,
            "metrics": {},
            "metric_counts": {},
        },
        "by_type": {},
    }
