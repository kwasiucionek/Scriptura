"""Sędziowie LLM: parsowanie, kontekst dla sędziów, walidacja komendy, odtworzenie w MLflow.

Bez modeli i sieci; natywny test MLflow tylko z RUN_MLFLOW_SMOKE=1 (tymczasowe SQLite)
i z fałszywym sędzią, który czyta kontekst ze spanu RETRIEVER.
"""

import json
import os
import subprocess
import sys
from dataclasses import asdict

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from library import judges, mlflow_eval
from library.evaluation import EvaluationCase, evaluate_cases
from rag import service


def test_parse_judges_default_and_dedupe():
    items = judges.parse_judges("default, RAGAS:Faithfulness ,ragas:ContextRelevance")
    assert items == [*judges.DEFAULT_JUDGES, ("ragas", "ContextRelevance")]
    assert judges.judge_name("ragas", "Faithfulness") == "ragas/Faithfulness"


@pytest.mark.parametrize(
    "spec", ["", " , ", "ragas", "ragas:AnswerRelevancy", "trulens:Groundedness"]
)
def test_parse_judges_rejects_unsupported(spec):
    # AnswerRelevancy w RAGAS wymaga embeddingów (domyślnie OpenAI) — celowo niedostępne
    with pytest.raises(ValueError):
        judges.parse_judges(spec)


@pytest.mark.parametrize("model", ["", "deepseek", "ollama:/", ":/model"])
def test_validate_model_rejects_malformed(model):
    with pytest.raises(ValueError):
        judges.validate_model(model)


def test_build_judges_reports_missing_packages(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlflow.genai.scorers.ragas", None)
    with pytest.raises(RuntimeError, match=r"\.\[eval,judges\]"):
        judges.build_judges([("ragas", "Faithfulness")], "ollama:/model:cloud")


def test_judges_module_import_is_lazy():
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'mlflow', 'ragas', 'deepeval', 'django'}:
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from library.judges import parse_judges
assert parse_judges('default')
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def sources():
    return {
        "chunks": [
            {
                "n": 1,
                "citation": "Rosik 2020, s. 5",
                "section": "Wstęp",
                "snippet": "skrót",
                "text": "Pełny tekst, zob. https://doi.org/10.1/x?token=abc oraz dalej.",
                "url": "https://example.org/secret",
            },
            {"n": 2, "title": "Bez cytatu", "snippet": "tylko skrót"},
        ],
        "verses": [{"ref": "Rdz 1,1", "work": "BG1632", "text": "Na początku"}],
        "related": [
            {"ref": "J 1,1", "work": "SBLGNT", "text": "Ἐν ἀρχῇ"},
            {"ref": "x"},
        ],
        "ane": [{"ref": "Enuma Elisz I 1", "translation_en": "When on high"}],
        "patristics": [{"ref": "Ireneusz, AH III 21", "text_en": "Text"}],
    }


def test_passages_mirror_prompt_and_redact_only_urls():
    passages = mlflow_eval._passages({"sources": sources()})
    assert passages == [
        "[1] Rosik 2020, s. 5 — sekcja: Wstęp\n"
        "Pełny tekst, zob. [redacted] oraz dalej.",
        "[2] Bez cytatu\ntylko skrót",
        "Rdz 1,1 (BG1632): Na początku",
        "J 1,1 (SBLGNT): Ἐν ἀρχῇ",
        "[A1] Enuma Elisz I 1: When on high",
        "[P1] Ireneusz, AH III 21: Text",
    ]
    assert mlflow_eval._passages({}) == [] and mlflow_eval._passages(None) == []


def test_judge_payload_skips_failed_and_empty_answers():
    results = [
        {
            "id": "ok",
            "question": "Pytanie?",
            "error": None,
            "output": {"result": {"answer": "Odpowiedź [1]."}, "sources": sources()},
        },
        {
            "id": "empty",
            "question": "Q",
            "error": None,
            "output": {"result": {"answer": " "}},
        },
        {"id": "failed", "question": "Q", "error": "evaluation_failed", "output": {}},
    ]
    payload = mlflow_eval._judge_payload(results)
    assert payload["ok"]["judge"] and payload["ok"]["answer"] == "Odpowiedź [1]."
    assert len(payload["ok"]["passages"]) == 6
    for key in ("empty", "failed"):
        assert payload[key] == {
            "question": "Q",
            "answer": "",
            "passages": [],
            "judge": False,
        }


def test_log_evaluation_requires_content_for_judges():
    with pytest.raises(ValueError, match="log_content"):
        mlflow_eval.log_evaluation(
            [],
            tracking_uri="sqlite:///eval.db",
            experiment="x",
            run_name=None,
            params={},
            tags={},
            judges=[object()],
        )


def test_rag_requests_full_chunk_text_only_with_content(monkeypatch):
    calls = []

    def ask(question, **kwargs):
        calls.append(kwargs)
        yield "sources", {"chunks": []}
        yield "done", asdict(service.AskResult(answer="Tak."))

    monkeypatch.setattr(service, "ask", ask)
    case = EvaluationCase(id="a", q="Pytanie?")
    evaluate_cases([case], task="rag")
    evaluate_cases([case], task="rag", include_content=True)
    assert "include_chunk_text" not in calls[0]
    assert calls[1]["include_chunk_text"] is True


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps({"id": "a", "q": "Pytanie?", "relevant": ["1:2"]}) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "args, message",
    [
        (["--task", "rag", "--judges", "default"], "--log-content"),
        (["--task", "retrieval", "--judges", "default", "--log-content"], "--task rag"),
        (
            ["--task", "rag", "--judges", "ragas:Nope", "--log-content"],
            "Nieobsługiwany",
        ),
        (
            [
                "--task",
                "rag",
                "--judges",
                "default",
                "--log-content",
                "--judge-model",
                "x",
            ],
            "dostawca",
        ),
        (
            [
                "--task",
                "rag",
                "--judges",
                "default",
                "--log-content",
                "--judge-workers",
                "0",
            ],
            "dodatnie",
        ),
        (
            [
                "--task",
                "rag",
                "--judges",
                "default",
                "--log-content",
                "--judge-timeout",
                "0",
            ],
            "dodatnie",
        ),
    ],
)
def test_command_validates_judges_before_models(monkeypatch, dataset, args, message):
    monkeypatch.setattr(service, "ask", pytest.fail)
    with pytest.raises(CommandError, match=message):
        call_command("eval_mlflow", str(dataset), "--dry-run", *args)


def test_gateway_judge_requires_http_tracking(monkeypatch, dataset, tmp_path):
    # sędzia przez bramkę MLflow nie zadziała z lokalnym SQLite — błąd przed modelami
    monkeypatch.setattr(service, "ask", pytest.fail)
    monkeypatch.delenv("MLFLOW_GATEWAY_URI", raising=False)
    with pytest.raises(CommandError, match="gateway"):
        call_command(
            "eval_mlflow",
            str(dataset),
            "--task",
            "rag",
            "--judges",
            "default",
            "--log-content",
            "--judge-model",
            "gateway:/scriptura-judge",
            "--tracking-uri",
            f"sqlite:///{tmp_path / 'eval.db'}",
        )
    assert "MLFLOW_GATEWAY_URI" not in os.environ


@pytest.mark.skipif(
    os.environ.get("RUN_MLFLOW_SMOKE") != "1",
    reason="Opt-in native MLflow smoke; RUN_MLFLOW_SMOKE=1 (temporary local SQLite only)",
)
def test_native_judged_replay(tmp_path):
    # Osobny proces izoluje globalny stan MLflow od reszty testów.
    script = """
import socket, sys
from pathlib import Path

def no_network(*args, **kwargs):
    raise AssertionError('Smoke must not connect anywhere')
socket.socket.connect = no_network
socket.create_connection = no_network

import mlflow
from mlflow import MlflowClient
from mlflow.entities import Feedback
from mlflow.genai.scorers import scorer
from mlflow.genai.utils.trace_utils import extract_retrieval_context_from_trace
from library.judges import _named
from library.mlflow_eval import log_evaluation

seen = []
def fake_inner(*, inputs, outputs, trace):
    contexts = extract_retrieval_context_from_trace(trace)
    texts = [c['content'] for values in contexts.values() for c in values]
    seen.append((inputs, outputs, texts))
    return Feedback(name='inner', value=float(len(texts)), rationale='fake')

judge = _named(scorer, fake_inner, 'fake/ContextCount')
root = Path(sys.argv[1])
uri = f'sqlite:///{root / "eval.db"}'
results = [
    {'id': 'ok', 'type': 'pl', 'question': 'Pytanie?', 'error': None,
     'metrics': {'success': 1.0, 'refused': 0.0},
     'output': {'result': {'answer': 'Odpowiedź [1].'},
                'sources': {'chunks': [{'n': 1, 'citation': 'A', 'text': 'Tekst A'}],
                            'verses': [{'ref': 'Rdz 1,1', 'work': 'BG1632', 'text': 'V'}]}}},
    {'id': 'bad', 'type': 'pl', 'question': 'Q', 'error': 'evaluation_failed',
     'metrics': {'success': 0.0}, 'output': {}},
]
run_id = log_evaluation(results, tracking_uri=uri, experiment='judged', run_name='j',
                        params={'p': 1}, tags={}, log_content=True, judges=[judge])
assert seen == [('Pytanie?', 'Odpowiedź [1].', ['[1] A\\nTekst A', 'Rdz 1,1 (BG1632): V'])], seen
client = MlflowClient(tracking_uri=uri)
metrics = client.get_run(run_id).data.metrics
assert metrics['judges.cases'] == 1.0 and metrics['cases.failed'] == 1.0, metrics
assert metrics['fake/ContextCount/mean'] == 2.0, metrics
mlflow.set_tracking_uri(uri)
experiment_id = client.get_run(run_id).info.experiment_id
traces = mlflow.search_traces(locations=[experiment_id], run_id=run_id, return_type='list')
assert len(traces) == 2, len(traces)
root = [s for s in traces[0].data.spans if s.parent_id is None]
assert len(root) == 1 and root[0].span_type == 'CHAIN', [(s.name, s.span_type) for s in root]
responses = sorted(str(t.data.response) for t in traces)
assert 'Odpowiedź [1].' in responses[1] or 'Odpowiedź [1].' in responses[0], responses
statuses = {a.value for t in traces for a in t.info.assessments if a.name == 'evaluation_status'}
assert statuses == {'ok', 'failed'}, statuses
judged = [t for t in traces if any(a.name == 'fake/ContextCount' for a in t.info.assessments)]
assert len(judged) == 1
print('OK')
"""
    env = {
        **os.environ,
        "MLFLOW_ENABLE_TELEMETRY": "false",
        "MLFLOW_DISABLE_AGENT_HINT": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    assert completed.stdout.strip().endswith("OK")
