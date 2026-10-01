"""Offline unit tests; opt in to the native SQLite smoke with RUN_MLFLOW_SMOKE=1."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from library import mlflow_eval


@pytest.fixture
def cases():
    return [
        {
            "id": "retrieval-1",
            "type": "retrieval",
            "question": "PRIVATE QUESTION",
            "metrics": {"recall_at_k": 0.5, "mrr": 0.0},
            "output": {"snippets": ["PRIVATE SNIPPET"]},
            "error": None,
        },
        {
            "id": "rag-1",
            "type": "rag",
            "question": "PRIVATE QUESTION 2",
            "metrics": {"citation_validity": 0.75},
            "output": {"answer": "PRIVATE ANSWER"},
            "error": None,
        },
        {
            "id": "rag-failed",
            "type": "rag",
            "question": "PRIVATE QUESTION 3",
            "metrics": {},
            "output": {},
            "error": "PRIVATE EXCEPTION https://backend/request?token=PRIVATE TOKEN",
        },
    ]


@pytest.fixture
def legacy_cases(cases):
    for case, label in zip(cases, ("pl", "pl+orig", "pl->en"), strict=True):
        case["type"] = label
    for index, label in enumerate(("żółć 日本語", "foo bar", "foo_bar")):
        cases.append(
            {
                "id": f"extra-{index}",
                "type": label,
                "question": "PRIVATE EXTRA QUESTION",
                "metrics": {"type_score": (index + 1) / 4},
                "output": {"answer": "PRIVATE EXTRA ANSWER"},
                "error": None,
            }
        )
    return cases


@pytest.fixture
def fake_mlflow(monkeypatch):
    mlflow = ModuleType("mlflow")
    mlflow.__version__ = "3.16.1"
    mlflow.active_run = Mock(return_value=None)
    mlflow.get_tracking_uri = Mock(return_value="sqlite:///previous.db")
    mlflow.set_tracking_uri = Mock()
    mlflow.get_experiment_by_name = Mock(return_value=None)
    mlflow.create_experiment = Mock(return_value="experiment-id")
    mlflow.log_params = Mock()
    mlflow.set_tags = Mock()
    mlflow.log_metrics = Mock()
    mlflow.log_dict = Mock()
    run = SimpleNamespace(info=SimpleNamespace(run_id="run-id"))
    context = Mock()
    context.__enter__ = Mock(return_value=run)
    context.__exit__ = Mock(return_value=False)
    mlflow.start_run = Mock(return_value=context)
    entities = ModuleType("mlflow.entities")
    entities.Feedback = lambda **kwargs: SimpleNamespace(**kwargs)
    scorers = ModuleType("mlflow.genai.scorers")
    scorers.scorer = lambda fn: fn
    genai = ModuleType("mlflow.genai")

    def evaluate(*, data, scorers):
        mlflow.feedback = [scorers[0](outputs=row["outputs"]) for row in data]
        return SimpleNamespace(run_id="run-id")

    genai.evaluate = Mock(side_effect=evaluate)
    mlflow.genai = genai
    for name, module in (
        ("mlflow", mlflow),
        ("mlflow.entities", entities),
        ("mlflow.genai", genai),
        ("mlflow.genai.scorers", scorers),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return mlflow


def _log(cases, **kwargs):
    arguments = {
        "tracking_uri": "https://tracking.example.invalid",
        "experiment": "offline-eval",
        "run_name": "baseline",
        "params": {"k": 5},
        "tags": {"suite": "offline"},
    }
    arguments.update(kwargs)
    return mlflow_eval.log_evaluation(cases, **arguments)


def test_native_evaluate_and_default_privacy(fake_mlflow, cases):
    original = json.dumps(cases)
    assert _log(cases) == "run-id"
    fake_mlflow.start_run.assert_called_once_with(
        experiment_id="experiment-id", run_name="baseline"
    )
    fake_mlflow.log_params.assert_called_once_with({"k": 5})
    fake_mlflow.set_tags.assert_called_once_with({"suite": "offline"})
    fake_mlflow.create_experiment.assert_called_once_with(
        "offline-eval", artifact_location=None
    )
    kwargs = fake_mlflow.genai.evaluate.call_args.kwargs
    assert set(kwargs) == {"data", "scorers"}  # no predictor or judge
    assert kwargs["data"][0] == {
        "inputs": {"case_id": "retrieval-1"},
        "outputs": {
            "type": "retrieval",
            "metrics": {"recall_at_k": 0.5, "mrr": 0.0},
            "error": None,
        },
    }
    artifact, filename = fake_mlflow.log_dict.call_args.args
    assert filename == "cases.json"
    assert artifact[2]["error"] == "evaluation_failed"
    serialized = json.dumps([artifact, kwargs["data"]])
    assert "PRIVATE" not in serialized
    assert "question" not in serialized and "answer" not in serialized
    assert "snippets" not in serialized and "https://backend" not in serialized
    assert json.dumps(cases) == original
    assert fake_mlflow.set_tracking_uri.call_args_list[-1].args == (
        "sqlite:///previous.db",
    )


def test_applicable_metrics_and_visible_failures(fake_mlflow, cases):
    cases[0]["metrics"].update(
        ignored=True, absent=None, textual="1", nan=float("nan"), inf=float("inf")
    )
    _log(cases)
    feedback = [{item.name: item.value for item in row} for row in fake_mlflow.feedback]
    assert feedback[0] == {
        "recall_at_k": 0.5,
        "mrr": 0.0,
        "evaluation_status": "ok",
    }
    assert feedback[2] == {"evaluation_status": "failed"}
    assert "citation_validity" not in feedback[0]
    assert isinstance(feedback[0]["mrr"], float)
    metrics = fake_mlflow.log_metrics.call_args.args[0]
    assert metrics["cases.count"] == 3
    assert metrics["cases.failed"] == 1
    assert metrics["types.retrieval.count"] == 1
    assert metrics["types.rag.count"] == 2
    assert metrics["types.rag.failed"] == 1
    assert metrics["metrics.citation_validity.mean"] == 0.75
    assert metrics["metrics.citation_validity.count"] == 1
    assert metrics["types.rag.metrics.citation_validity.count"] == 1
    assert metrics["metrics.mrr.mean"] == 0.0
    assert "metrics.absent.mean" not in metrics


def test_means_use_only_applicable_cases(fake_mlflow, cases):
    cases[1]["metrics"]["recall_at_k"] = 1.0
    _log(cases)
    metrics = fake_mlflow.log_metrics.call_args.args[0]
    assert metrics["metrics.recall_at_k.mean"] == 0.75
    assert metrics["metrics.recall_at_k.count"] == 2
    assert metrics["types.retrieval.metrics.recall_at_k.mean"] == 0.5
    assert metrics["types.rag.metrics.recall_at_k.mean"] == 1.0


def test_content_opt_in_still_redacts_exceptions_and_credentials(fake_mlflow, cases):
    cases[0]["output"].update(
        error="PRIVATE RAW ERROR",
        nested={"api_key": "PRIVATE KEY", "request_url": "https://backend/private"},
    )
    _log(
        cases,
        log_content=True,
        params={"k": 5, "OPENAI_API_KEY": "PRIVATE KEY", "base_url": "https://backend"},
        tags={"suite": "offline", "token": "PRIVATE TOKEN"},
    )
    rows = fake_mlflow.genai.evaluate.call_args.kwargs["data"]
    assert rows[0]["inputs"]["question"] == "PRIVATE QUESTION"
    assert rows[0]["outputs"]["output"]["snippets"] == ["PRIVATE SNIPPET"]
    assert rows[1]["outputs"]["output"]["answer"] == "PRIVATE ANSWER"
    assert rows[2]["outputs"]["error"] == "evaluation_failed"
    assert rows[0]["outputs"]["output"]["error"] == "[redacted]"
    assert rows[0]["outputs"]["output"]["nested"] == {
        "api_key": "[redacted]",
        "request_url": "[redacted]",
    }
    assert fake_mlflow.log_params.call_args.args[0] == {
        "k": 5,
        "OPENAI_API_KEY": "[redacted]",
        "base_url": "[redacted]",
    }
    assert fake_mlflow.set_tags.call_args.args[0]["token"] == "[redacted]"
    serialized = json.dumps([rows, fake_mlflow.log_dict.call_args.args[0]])
    assert (
        "PRIVATE EXCEPTION" not in serialized and "PRIVATE RAW ERROR" not in serialized
    )
    assert "PRIVATE KEY" not in serialized and "https://backend" not in serialized


def test_sqlite_artifacts_isolated_beside_database(fake_mlflow, tmp_path, cases):
    database = tmp_path / "tracking" / "eval.db"
    _log(cases, tracking_uri=f"sqlite:///{database}")
    artifacts = database.parent / "eval-artifacts"
    assert artifacts.is_dir()
    fake_mlflow.create_experiment.assert_called_once_with(
        "offline-eval", artifact_location=artifacts.as_uri()
    )
    assert not (tmp_path / "mlruns").exists()


def test_existing_experiment_is_reused(fake_mlflow, cases):
    fake_mlflow.get_experiment_by_name.return_value = SimpleNamespace(
        experiment_id="42"
    )
    _log(cases, run_name=None)
    fake_mlflow.create_experiment.assert_not_called()
    fake_mlflow.start_run.assert_called_once_with(experiment_id="42", run_name=None)


def test_reject_active_run_before_changing_tracking(fake_mlflow, cases):
    fake_mlflow.active_run.return_value = object()
    with pytest.raises(RuntimeError, match="active MLflow run"):
        _log(cases)
    fake_mlflow.set_tracking_uri.assert_not_called()
    fake_mlflow.start_run.assert_not_called()
    fake_mlflow.genai.evaluate.assert_not_called()


@pytest.mark.parametrize("operation", ["create_experiment", "log_dict", "evaluate"])
def test_failures_propagate_and_restore_tracking(fake_mlflow, cases, operation):
    target = fake_mlflow.genai if operation == "evaluate" else fake_mlflow
    getattr(target, operation).side_effect = RuntimeError("export failed")
    with pytest.raises(RuntimeError, match="export failed"):
        _log(cases)
    assert fake_mlflow.set_tracking_uri.call_args_list[-1].args == (
        "sqlite:///previous.db",
    )
    if operation != "create_experiment":
        assert (
            fake_mlflow.start_run.return_value.__exit__.call_args.args[0]
            is RuntimeError
        )


def test_wrong_native_run_is_not_reported_as_success(fake_mlflow, cases):
    fake_mlflow.genai.evaluate.side_effect = None
    fake_mlflow.genai.evaluate.return_value = SimpleNamespace(run_id="different")
    with pytest.raises(RuntimeError, match="different run"):
        _log(cases)


def test_empty_results(fake_mlflow):
    assert _log([]) == "run-id"
    fake_mlflow.log_metrics.assert_called_once_with(
        {"cases.count": 0.0, "cases.failed": 0.0}
    )
    fake_mlflow.log_dict.assert_called_once_with([], "cases.json")
    fake_mlflow.genai.evaluate.assert_not_called()


@pytest.mark.parametrize("version", ["3.15.9", "4.0.0", "4.0.1"])
def test_incompatible_mlflow_version(fake_mlflow, cases, version):
    fake_mlflow.__version__ = version
    with pytest.raises(RuntimeError, match="mlflow>=3.16,<4"):
        _log(cases)
    fake_mlflow.start_run.assert_not_called()


def test_telemetry_disabled_before_lazy_import(fake_mlflow, monkeypatch):
    monkeypatch.delenv("MLFLOW_ENABLE_TELEMETRY", raising=False)
    assert mlflow_eval.require_mlflow() is fake_mlflow
    assert os.environ["MLFLOW_ENABLE_TELEMETRY"] == "false"
    monkeypatch.setenv("MLFLOW_ENABLE_TELEMETRY", "true")
    mlflow_eval.require_mlflow()
    assert os.environ["MLFLOW_ENABLE_TELEMETRY"] == "true"


def test_optional_dependency_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlflow", None)
    with pytest.raises(RuntimeError, match="mlflow>=3.16,<4"):
        _log([])


def test_import_has_no_mlflow_django_or_environment_side_effects():
    script = """
import builtins
import os
before = dict(os.environ)
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'mlflow', 'django'}:
        raise AssertionError('Unexpected optional/application import')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import library.mlflow_eval
assert dict(os.environ) == before
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "uri", ["", "mlruns", "file:///tmp/mlruns", "sqlite:///:memory:"]
)
def test_no_implicit_or_obsolete_file_store(fake_mlflow, uri):
    with pytest.raises(ValueError):
        _log([], tracking_uri=uri)
    fake_mlflow.start_run.assert_not_called()


@pytest.mark.parametrize("name", ["invalid?metric", "evaluation_status"])
def test_invalid_or_reserved_metric_names(fake_mlflow, cases, name):
    cases[0]["metrics"][name] = 1
    with pytest.raises(ValueError, match="metric name"):
        _log(cases)
    fake_mlflow.start_run.assert_not_called()


def test_legacy_unicode_types_and_raw_labels(fake_mlflow, legacy_cases):
    assert _log(legacy_cases) == "run-id"
    expected_types = [case["type"] for case in legacy_cases]
    artifact = fake_mlflow.log_dict.call_args.args[0]
    assert [case["type"] for case in artifact] == expected_types
    rows = fake_mlflow.genai.evaluate.call_args.kwargs["data"]
    assert [row["outputs"]["type"] for row in rows] == expected_types
    metrics = fake_mlflow.log_metrics.call_args.args[0]
    for label in expected_types:
        encoded = mlflow_eval._encode_type(label)
        assert all(
            char.isascii() and (char.isalnum() or char in "_-") for char in encoded
        )
        assert metrics[f"types.{encoded}.count"] == 1
    assert metrics["types.pl_2b_orig.metrics.citation_validity.mean"] == 0.75
    assert metrics["types.pl-_3e_en.failed"] == 1
    assert metrics["types.foo_20_bar.metrics.type_score.mean"] == 0.5
    assert metrics["types.foo_5f_bar.metrics.type_score.mean"] == 0.75


def test_type_encoding_has_no_escape_or_path_collisions(fake_mlflow):
    labels = (
        "foo bar",
        "foo_bar",
        "foo_20_bar",
        "foo",
        "foo.metrics.bar",
        "pl+orig",
        "pl_2b_orig",
        "pl->en",
        "ż",
        "_c5__bc_",
        "a/b",
        "a.b",
    )
    rows = [
        {"id": str(index), "type": label, "metrics": {"bar": index}}
        for index, label in enumerate(labels)
    ]
    _log(rows)
    encoded = [mlflow_eval._encode_type(label) for label in labels]
    assert len(set(encoded)) == len(labels)
    metrics = fake_mlflow.log_metrics.call_args.args[0]
    for index, component in enumerate(encoded):
        assert metrics[f"types.{component}.count"] == 1
        assert metrics[f"types.{component}.metrics.bar.mean"] == index
    assert mlflow_eval._encode_type("foo.metrics.bar") == "foo_2e_metrics_2e_bar"


def test_preflight_validates_without_tracking_or_filesystem_writes(
    fake_mlflow, tmp_path
):
    database = tmp_path / "not-created" / "eval.db"
    records = [{"id": "1", "type": "pl+orig"}, {"id": "2", "type": "żółć"}]
    assert (
        mlflow_eval.preflight(f"sqlite:///{database}", "early-check", cases=records)
        is None
    )
    assert not database.parent.exists()
    fake_mlflow.active_run.assert_called_once_with()
    fake_mlflow.set_tracking_uri.assert_not_called()
    fake_mlflow.create_experiment.assert_not_called()
    fake_mlflow.start_run.assert_not_called()
    fake_mlflow.genai.evaluate.assert_not_called()


@pytest.mark.parametrize("label", ["", "   ", "all", "ALL", " all ", None, 3])
def test_preflight_and_export_reject_invalid_types(fake_mlflow, label):
    records = [{"id": "1", "type": label}]
    with pytest.raises(ValueError, match="case type"):
        mlflow_eval.preflight("sqlite:///eval.db", "early-check", cases=records)
    with pytest.raises(ValueError, match="case type"):
        _log(records)
    fake_mlflow.active_run.assert_not_called()
    fake_mlflow.start_run.assert_not_called()


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"id": ""},
        {"id": "", "type": "pl"},
        {"id": True, "type": "pl"},
        {"id": "1", "type": "pl", "metrics": []},
    ],
)
def test_preflight_rejects_invalid_case_records(fake_mlflow, record):
    with pytest.raises(ValueError):
        mlflow_eval.preflight("sqlite:///eval.db", "early-check", cases=[record])
    fake_mlflow.active_run.assert_not_called()


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "mlruns",
        "file:///tmp/mlruns",
        "sqlite:///:memory:",
        "sqlite://",
        "https:///missing-host",
        "unsupported://store",
    ],
)
def test_preflight_rejects_invalid_tracking_uris(fake_mlflow, tmp_path, uri):
    with pytest.raises(ValueError):
        mlflow_eval.preflight(uri, "early-check")
    fake_mlflow.active_run.assert_not_called()
    fake_mlflow.set_tracking_uri.assert_not_called()


@pytest.mark.parametrize("experiment", ["", "  ", None, 3])
def test_preflight_rejects_empty_experiment_names(fake_mlflow, experiment):
    with pytest.raises(ValueError, match="experiment name"):
        mlflow_eval.preflight("sqlite:///eval.db", experiment)
    fake_mlflow.active_run.assert_not_called()


def test_preflight_rejects_active_run(fake_mlflow):
    fake_mlflow.active_run.return_value = object()
    with pytest.raises(RuntimeError, match="active MLflow run"):
        mlflow_eval.preflight("sqlite:///eval.db", "early-check")
    fake_mlflow.set_tracking_uri.assert_not_called()
    fake_mlflow.start_run.assert_not_called()


@pytest.mark.parametrize("version", ["3.15.9", "4.0.0"])
def test_preflight_rejects_incompatible_version(fake_mlflow, version):
    fake_mlflow.__version__ = version
    with pytest.raises(RuntimeError, match="mlflow>=3.16,<4"):
        mlflow_eval.preflight("sqlite:///eval.db", "early-check")
    fake_mlflow.active_run.assert_not_called()


def test_preflight_rejects_missing_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlflow", None)
    with pytest.raises(RuntimeError, match="mlflow>=3.16,<4"):
        mlflow_eval.preflight("sqlite:///eval.db", "early-check")


@pytest.mark.skipif(
    os.environ.get("RUN_MLFLOW_SMOKE") != "1",
    reason="Opt-in native MLflow smoke; RUN_MLFLOW_SMOKE=1 (temporary local SQLite only)",
)
def test_native_sqlite_smoke(tmp_path, monkeypatch, legacy_cases):
    monkeypatch.setenv("MLFLOW_ENABLE_TELEMETRY", "false")
    mlflow = pytest.importorskip("mlflow", minversion="3.16")
    from packaging.version import Version

    if Version(mlflow.__version__) >= Version("4"):
        pytest.skip("Native export supports MLflow >=3.16,<4")
    # A clean subprocess isolates MLflow's global state/autologging from the suite.
    script = """
import json
import socket
import sys
from pathlib import Path
from library.mlflow_eval import log_evaluation, preflight

def no_network(*args, **kwargs):
    raise AssertionError('Native SQLite smoke must not connect to external services')
socket.socket.connect = no_network
socket.create_connection = no_network
import mlflow
from mlflow import MlflowClient

root = Path(sys.argv[1])
uri = f'sqlite:///{root / "eval.db"}'
rows = json.loads(sys.argv[2])
previous = mlflow.get_tracking_uri()
preflight(uri, 'native-smoke', cases=rows)
assert not (root / 'eval.db').exists()
assert not (root / 'eval-artifacts').exists()
assert mlflow.get_tracking_uri() == previous
run_id = log_evaluation(rows, tracking_uri=uri, experiment='native-smoke',
                        run_name='redacted', params={'k': 5}, tags={'suite': 'smoke'})
assert mlflow.active_run() is None
assert mlflow.get_tracking_uri() == previous
client = MlflowClient(tracking_uri=uri)
run = client.get_run(run_id)
assert run.info.status == 'FINISHED'
assert run.data.tags['mlflow.runType'] == 'genai_evaluate'
assert run.data.metrics['cases.failed'] == 1
expected_types = ('pl', 'pl+orig', 'pl->en', 'żółć 日本語', 'foo bar', 'foo_bar')
assert tuple(row['type'] for row in rows) == expected_types
encoded_types = [
    ''.join(chr(b) if chr(b) in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-'
            else f'_{b:02x}_' for b in label.encode('utf-8'))
    for label in expected_types
]
assert len(set(encoded_types)) == len(expected_types)
for encoded in encoded_types:
    assert run.data.metrics[f'types.{encoded}.count'] == 1
assert run.data.metrics['types.pl_2b_orig.metrics.citation_validity.mean'] == 0.75
assert run.data.metrics['types.pl-_3e_en.failed'] == 1
assert run.data.metrics['types.foo_20_bar.metrics.type_score.mean'] == 0.5
assert run.data.metrics['types.foo_5f_bar.metrics.type_score.mean'] == 0.75
assert run.data.metrics['metrics.citation_validity.mean'] == 0.75
assert run.data.metrics['citation_validity/mean'] == 0.75
assert run.data.metrics['mrr/mean'] == 0.0
assert run.data.params['k'] == '5'
assert run.data.tags['suite'] == 'smoke'
experiment = client.get_experiment(run.info.experiment_id)
assert experiment.artifact_location == (root / 'eval-artifacts').as_uri()
artifacts = client.list_artifacts(run_id)
assert any(item.path == 'cases.json' for item in artifacts)
artifact = json.loads(Path(client.download_artifacts(run_id, 'cases.json')).read_text())
assert artifact[2]['error'] == 'evaluation_failed'
assert tuple(row['type'] for row in artifact) == expected_types
traces = client.search_traces(locations=[run.info.experiment_id],
                              filter_string=f'trace.run_id = "{run_id}"')
assert len(traces) == len(rows), traces
seen = {}
for trace in traces:
    request = json.loads(trace.info.request_preview)
    case_id = request['case_id']
    seen[case_id] = {a.name: a.feedback.value for a in trace.info.assessments}
    response = json.loads(trace.info.response_preview)
    assert response['type'] == next(row['type'] for row in rows if row['id'] == case_id)
assert seen['retrieval-1']['mrr'] == 0.0
assert seen['rag-1']['citation_validity'] == 0.75
assert 'citation_validity' not in seen['rag-failed']
assert seen['rag-failed']['evaluation_status'] == 'failed'
for file in (root / 'eval-artifacts').rglob('*'):
    if file.is_file():
        assert b'PRIVATE' not in file.read_bytes(), file
serialized = json.dumps([trace.to_dict() for trace in traces])
assert 'PRIVATE' not in serialized
print(json.dumps({'run_id': run_id, 'traces': len(traces),
                  'artifacts': [item.path for item in artifacts]}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), json.dumps(legacy_cases)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
