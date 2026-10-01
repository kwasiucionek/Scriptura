"""Offline CLI contracts: SQL fixtures and mocks, never models or MLflow stores."""

import builtins
import hashlib
import json
import logging
import sys
from dataclasses import asdict
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError

from library import evaluation, search
from library.management.commands import eval_mlflow as command
from library.models import Chunk, Document
from rag import service

SAFE_CONFIG_KEYS = {
    "search_backend",
    "opensearch_index_prefix",
    "embeddings_backend",
    "ollama_embed_model",
    "embedding_dim",
    "embedding_query_prefix",
    "reranker_backend",
    "rerank_top_n",
    "rerank_max_chars",
    "rerank_llm_min_score",
    "rerank_llm_max_chars",
    "llm_backend",
    "ollama_chat_model",
    "openai_chat_model",
    "openai_thinking_kwargs",
    "rag_top_k",
    "rag_max_per_doc",
    "rag_context_neighbors",
    "rag_register_mode",
    "rag_translate_query",
    "rag_translate_model",
    "rag_verse_works",
    "rag_related_verses",
    "rag_ane_passages",
    "rag_patristic_passages",
    "rag_temperature",
    "rag_num_ctx",
    "rag_think",
    "llm_reasoning_budget",
}


def test_backend_failure_logs_cannot_echo_content(
    corpus, dataset, cli, offline, monkeypatch, caplog
):
    path = dataset([{"id": "safe-id", "q": "PRIVATE QUESTION"}])
    monkeypatch.setattr(command, "evaluate_cases", evaluation.evaluate_cases)

    def fail_with_sensitive_log(*args, **kwargs):
        logging.getLogger("rag.service").error(
            "PRIVATE QUESTION https://provider.invalid/?token=PRIVATE_TOKEN",
            exc_info=True,
        )
        yield "error", {"detail": "PRIVATE PROVIDER ERROR"}

    monkeypatch.setattr(service, "ask", fail_with_sensitive_log)
    previous = logging.root.manager.disable
    with caplog.at_level(logging.ERROR):
        with pytest.raises(CommandError, match="część przypadków"):
            cli.run(path, "--task", "rag")
    assert logging.root.manager.disable == previous
    assert "PRIVATE" not in caplog.text
    assert "PRIVATE" not in cli.stdout.getvalue() + cli.stderr.getvalue()
    result = offline.log.call_args.args[0][0]
    assert result["error"] == "evaluation_failed" and result["output"] == {}


def test_backend_log_suppression_restores_level_after_unexpected_error():
    previous = logging.root.manager.disable
    with pytest.raises(RuntimeError):
        with command._quiet_backend_logs():
            assert logging.root.manager.disable == logging.CRITICAL
            raise RuntimeError("unexpected")
    assert logging.root.manager.disable == previous


def successful_results(cases, **kwargs):
    return [
        {
            "id": case.id,
            "type": case.type,
            "question": case.q,
            "metrics": {"success": 1.0, "latency_ms": 1.0},
            "output": {},
            "error": None,
        }
        for case in cases
    ]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    dependencies = SimpleNamespace(
        preflight=Mock(),
        evaluate=Mock(side_effect=successful_results),
        log=Mock(return_value="offline-run"),
        git=Mock(
            return_value={
                "scriptura.git_commit": "a" * 40,
                "scriptura.git_dirty": "false",
            }
        ),
        retrieve=Mock(side_effect=AssertionError("Unexpected retrieval")),
        ask=Mock(side_effect=AssertionError("Unexpected RAG")),
    )
    monkeypatch.setattr(command, "preflight", dependencies.preflight)
    monkeypatch.setattr(command, "evaluate_cases", dependencies.evaluate)
    monkeypatch.setattr(command, "log_evaluation", dependencies.log)
    monkeypatch.setattr(command, "_git_metadata", dependencies.git)
    monkeypatch.setattr(search, "retrieve", dependencies.retrieve)
    monkeypatch.setattr(service, "ask", dependencies.ask)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    return dependencies


@pytest.fixture
def cli():
    stdout, stderr = StringIO(), StringIO()

    def run(path, *arguments):
        return call_command(
            "eval_mlflow", str(path), *arguments, stdout=stdout, stderr=stderr
        )

    return SimpleNamespace(run=run, stdout=stdout, stderr=stderr)


@pytest.fixture
def dataset(tmp_path):
    def write(rows, name="cases.jsonl"):
        path = tmp_path / name
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        return path

    return write


@pytest.fixture
def corpus(db):
    chunks = {}
    for access in ("open", "licensed", "private", "personal"):
        document = Document.objects.create(
            title=f"PRIVATE TITLE {access}",
            access=access,
            chunk_count=1,
            content_hash="a" * 32,
            note="PRIVATE DOCUMENT NOTE",
            url="https://private.example.invalid/source?token=PRIVATE",
        )
        chunks[access] = Chunk.objects.create(
            document=document, order=0, text=f"PRIVATE CHUNK {access}"
        )
    return SimpleNamespace(
        **chunks,
        empty=Document.objects.create(title="Open without a chunk", access="open"),
    )


@pytest.fixture
def retrieval_file(dataset, corpus):
    return dataset(
        [
            {
                "id": "retrieval-one",
                "q": "PRIVATE RETRIEVAL QUESTION",
                "relevant": [f"{corpus.open.document_id}:0"],
            }
        ]
    )


def assert_no_services(offline):
    for dependency in (
        offline.preflight,
        offline.evaluate,
        offline.log,
        offline.git,
        offline.retrieve,
        offline.ask,
    ):
        dependency.assert_not_called()


def assert_private_output(cli, *private):
    output = cli.stdout.getvalue() + cli.stderr.getvalue()
    for value in private:
        assert value not in output


@pytest.mark.parametrize(
    "contents",
    [
        b'{"PRIVATE MALFORMED QUESTION": invalid}',
        b"[]\n",
        b'{"q": "PRIVATE QUESTION"}\n',
        b'{"q": "PRIVATE QUESTION", "relevant": ["0:0"]}\n',
        b'{"q": "PRIVATE QUESTION", "relevant": ["1:-1"]}\n',
        b'{"q": "PRIVATE QUESTION", "relevant": ["1:0"], "authors": 1}\n',
        b"\xff\xfe\n",
        b"\n \n",
    ],
    ids=[
        "json",
        "non-object",
        "no-labels",
        "zero-id",
        "negative-order",
        "authors",
        "utf8",
        "empty",
    ],
)
def test_invalid_dataset_stops_before_sql_and_services(
    tmp_path, contents, cli, offline
):
    path = tmp_path / "invalid.jsonl"
    path.write_bytes(contents)
    with pytest.raises(CommandError) as exc:
        cli.run(path)
    assert exc.value.returncode == 1
    assert "PRIVATE" not in str(exc.value)
    assert_no_services(offline)


def test_missing_file_is_command_error(tmp_path, cli, offline):
    with pytest.raises(CommandError, match="Nieprawidłowy zbiór"):
        cli.run(tmp_path / "missing.jsonl")
    assert_no_services(offline)


@pytest.mark.parametrize(
    "tail", ["malformed", "invalid", "duplicate-id", "duplicate-generated-id"]
)
def test_limit_does_not_hide_invalid_later_rows(dataset, cli, offline, tail):
    first = {"q": "PRIVATE FIRST", "relevant": ["1:0"]}
    if tail == "duplicate-id":
        first["id"] = "same"
        second = {"id": "same", "q": "PRIVATE SECOND", "relevant": ["1:0"]}
    elif tail == "duplicate-generated-id":
        second = {**first, "note": "different PRIVATE NOTE"}
    else:
        second = {"q": "PRIVATE SECOND"}
    path = dataset([first, second])
    if tail == "malformed":
        path.write_text(
            json.dumps(first) + '\n{"PRIVATE": invalid}\n', encoding="utf-8"
        )
    with pytest.raises(CommandError, match="line 2:") as exc:
        cli.run(path, "--limit", "1")
    assert "PRIVATE" not in str(exc.value)
    assert_no_services(offline)


@pytest.mark.parametrize("limit", ["0", "-1", "not-an-int"])
def test_invalid_limit_stops_before_services(dataset, cli, offline, limit):
    with pytest.raises(CommandError):
        cli.run(dataset([{"q": "PRIVATE QUESTION"}]), "--task", "rag", "--limit", limit)
    assert_no_services(offline)


@pytest.mark.parametrize("task", ["retrieval", "rag"])
@pytest.mark.parametrize("k", ["0", "-1", "not-an-int"])
def test_invalid_k_stops_before_loading_or_services(tmp_path, cli, offline, task, k):
    with pytest.raises(CommandError, match="k"):
        cli.run(tmp_path / "does-not-exist.jsonl", "--task", task, "--k", k)
    assert_no_services(offline)


@pytest.mark.parametrize("task", ["retrieval", "rag"])
def test_dry_run_without_mlflow_dependency(
    monkeypatch, dataset, retrieval_file, cli, offline, task
):
    original_import = builtins.__import__
    imports = []

    def no_mlflow(name, *args, **kwargs):
        if name.split(".")[0] == "mlflow":
            imports.append(name)
            raise ModuleNotFoundError("MLflow is deliberately unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setitem(sys.modules, "mlflow", None)
    monkeypatch.setattr(builtins, "__import__", no_mlflow)
    path = (
        retrieval_file
        if task == "retrieval"
        else dataset([{"q": "PRIVATE RAG QUESTION"}])
    )
    cli.run(path, "--task", task, "--dry-run")
    assert imports == []
    assert f"1 przypadków; task={task}; access=open" in cli.stdout.getvalue()
    assert cli.stderr.getvalue() == ""
    assert_private_output(cli, "PRIVATE")
    assert_no_services(offline)


@pytest.mark.parametrize(
    "label",
    [
        "licensed",
        "private",
        "personal",
        "missing-document",
        "missing-order",
        "no-chunks",
        "deleted-chunk",
        "revoked-access",
    ],
)
def test_relevant_must_exist_in_open_sql_corpus(dataset, corpus, cli, offline, label):
    if label in ("licensed", "private", "personal"):
        relevant = f"{getattr(corpus, label).document_id}:0"
    elif label == "missing-document":
        relevant = f"{corpus.empty.pk + 100}:0"
    elif label == "missing-order":
        relevant = f"{corpus.open.document_id}:999"
    elif label == "no-chunks":
        relevant = f"{corpus.empty.pk}:0"
    else:
        relevant = f"{corpus.open.document_id}:0"
        if label == "deleted-chunk":
            corpus.open.delete()
        else:
            Document.objects.filter(pk=corpus.open.document_id).update(access="private")
    path = dataset([{"q": "PRIVATE QUESTION", "relevant": [relevant]}])
    with pytest.raises(CommandError, match="otwartym korpusie"):
        cli.run(path, "--dry-run")
    assert_no_services(offline)


def test_all_selected_relevant_labels_are_checked(dataset, corpus, cli, offline):
    path = dataset(
        [
            {"q": "first", "relevant": [f"{corpus.open.document_id}:0"]},
            {"q": "second", "relevant": [f"{corpus.private.document_id}:0"]},
        ]
    )
    with pytest.raises(CommandError, match="otwartym korpusie"):
        cli.run(path)
    assert_no_services(offline)


def test_valid_limit_only_evaluates_selected_cases(dataset, corpus, cli, offline):
    path = dataset(
        [
            {"id": "one", "q": "first", "relevant": [f"{corpus.open.document_id}:0"]},
            {"id": "two", "q": "second", "relevant": [f"{corpus.open.document_id}:0"]},
        ]
    )
    cli.run(path, "--limit", "1")
    cases = offline.evaluate.call_args.args[0]
    assert [case.id for case in cases] == ["one"]
    assert offline.log.call_args.kwargs["params"]["dataset.case_count"] == 1


def test_preflight_happens_before_models_and_logging(retrieval_file, cli, offline):
    order = Mock()
    order.attach_mock(offline.preflight, "preflight")
    order.attach_mock(offline.evaluate, "evaluate")
    order.attach_mock(offline.log, "log")
    cli.run(retrieval_file)
    assert [entry[0] for entry in order.mock_calls] == ["preflight", "evaluate", "log"]
    offline.retrieve.assert_not_called()
    offline.ask.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [RuntimeError("Missing optional dependency"), ValueError("Invalid tracking URI")],
)
def test_failed_preflight_never_evaluates(retrieval_file, cli, offline, error):
    offline.preflight.side_effect = error
    with pytest.raises(CommandError, match="MLflow"):
        cli.run(retrieval_file)
    offline.preflight.assert_called_once()
    offline.evaluate.assert_not_called()
    offline.log.assert_not_called()
    offline.git.assert_not_called()
    offline.retrieve.assert_not_called()
    offline.ask.assert_not_called()


@pytest.mark.parametrize("failure", [None, "evaluation", "tracking"])
def test_no_rerank_is_scoped_and_restored(
    settings, retrieval_file, cli, offline, failure
):
    settings.RERANKER_BACKEND = "tei"

    def evaluate(cases, **kwargs):
        assert django_settings.RERANKER_BACKEND == "none"
        if failure == "evaluation":
            raise RuntimeError("Synthetic evaluation failure")
        return successful_results(cases, **kwargs)

    def log(results, **kwargs):
        assert django_settings.RERANKER_BACKEND == "none"
        assert kwargs["params"]["reranker_backend"] == "none"
        if failure == "tracking":
            raise RuntimeError("Synthetic tracking failure")
        return "offline-run"

    offline.evaluate.side_effect = evaluate
    offline.log.side_effect = log
    if failure:
        exception = RuntimeError if failure == "evaluation" else CommandError
        with pytest.raises(exception):
            cli.run(retrieval_file, "--no-rerank")
    else:
        cli.run(retrieval_file, "--no-rerank")
    assert django_settings.RERANKER_BACKEND == "tei"
    if failure == "evaluation":
        offline.log.assert_not_called()


def test_config_is_an_explicit_safe_whitelist(settings, retrieval_file, cli, offline):
    sensitive = {
        "SECRET_KEY": "PRIVATE SECRET",
        "OPENAI_API_KEY": "PRIVATE OPENAI TOKEN",
        "OLLAMA_API_KEY": "PRIVATE OLLAMA TOKEN",
        "TEI_TOKEN": "PRIVATE TEI TOKEN",
        "DEMO_TOKEN": "PRIVATE DEMO TOKEN",
        "OPENAI_BASE_URL": "https://user:PRIVATE@openai.example.invalid",
        "OLLAMA_BASE_URL": "https://user:PRIVATE@ollama.example.invalid",
        "TEI_EMBED_URL": "https://user:PRIVATE@tei.example.invalid",
        "OPENSEARCH_URL": "https://user:PRIVATE@search.example.invalid",
        "UNRELATED_SETTING": "PRIVATE UNKNOWN CONFIG",
    }
    for name, value in sensitive.items():
        setattr(settings, name, value)
    settings.OLLAMA_CHAT_MODEL = "offline-chat-model"
    settings.RAG_TOP_K = 7
    cli.run(retrieval_file)
    kwargs = offline.log.call_args.kwargs
    params = kwargs["params"]
    config_keys = {
        key
        for key in params
        if not key.startswith(("corpus.", "dataset.", "evaluation.", "prompt."))
    }
    assert config_keys == SAFE_CONFIG_KEYS
    assert params["ollama_chat_model"] == "offline-chat-model"
    assert params["rag_top_k"] == 7
    assert params["evaluation.access"] == "open"
    assert not (config_keys & {name.lower() for name in sensitive})
    metadata = json.dumps([params, kwargs["tags"]], default=str)
    assert "PRIVATE" not in metadata
    assert "https://" not in metadata
    assert_private_output(cli, "PRIVATE", "https://")


def test_metadata_hashes_and_case_ids_are_stable_without_notes(
    dataset, corpus, cli, offline
):
    first_row = {
        "q": "  Co oznacza ḥesed?  ",
        "relevant": [f"{corpus.open.document_id:03d}:00"],
        "note": "PRIVATE FIRST NOTE",
    }
    first = dataset([first_row], "first.jsonl")
    second = dataset(
        [
            {
                "note": "PRIVATE SECOND NOTE",
                "relevant": [f"{corpus.open.document_id}:0"],
                "q": "Co oznacza ḥesed?",
                "type": "pl",
            }
        ],
        "second.jsonl",
    )
    second.write_text("\n\n" + second.read_text(encoding="utf-8"), encoding="utf-8")
    cli.run(first)
    first_cases = offline.evaluate.call_args.args[0]
    first_metadata = offline.log.call_args.kwargs
    cli.run(second)
    second_cases = offline.evaluate.call_args.args[0]
    second_metadata = offline.log.call_args.kwargs
    assert first_cases[0].id == second_cases[0].id
    assert first_cases[0].id.startswith("case-")
    canonical = asdict(first_cases[0])
    del canonical["note"]
    expected_dataset = hashlib.sha256(
        json.dumps([canonical], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert first_metadata["params"]["dataset.sha256"] == expected_dataset
    assert second_metadata["params"]["dataset.sha256"] == expected_dataset
    expected_prompt = hashlib.sha256(
        json.dumps(
            [service.SYSTEM_PROMPT, service.MODE_INSTRUCTIONS],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert first_metadata["params"]["prompt.sha256"] == expected_prompt
    assert first_metadata["params"]["corpus.open_documents"] == 2
    assert first_metadata["params"]["corpus.open_chunks"] == 1
    digest = hashlib.sha256()
    for document in (corpus.open.document, corpus.empty):
        row = (
            document.pk,
            document.modified,
            document.content_hash,
            document.chunk_count,
            document.language,
            document.register,
        )
        digest.update(json.dumps(row, default=str, ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    assert first_metadata["params"]["corpus.metadata_sha256"] == digest.hexdigest()
    assert first_metadata["params"] == second_metadata["params"]
    assert "PRIVATE" not in json.dumps(first_metadata, default=str)
    changed = dataset(
        [{**first_row, "q": "An actually different question"}], "changed.jsonl"
    )
    cli.run(changed)
    assert offline.evaluate.call_args.args[0][0].id != first_cases[0].id
    assert offline.log.call_args.kwargs["params"]["dataset.sha256"] != expected_dataset


def test_failed_cases_are_retained_and_logged_before_nonzero_error(
    retrieval_file, cli, offline
):
    failed = {
        "id": "failed-case",
        "type": "pl",
        "question": "PRIVATE QUESTION",
        "metrics": {"success": 0.0, "latency_ms": 2.0},
        "output": {},
        "error": "evaluation_failed",
    }
    results = [*successful_results(evaluation.load_dataset(retrieval_file)), failed]
    offline.evaluate.side_effect = None
    offline.evaluate.return_value = results
    with pytest.raises(CommandError, match="Ewaluacja zapisana") as exc:
        cli.run(retrieval_file)
    assert exc.value.returncode == 1
    offline.log.assert_called_once()
    assert offline.log.call_args.args[0] is results
    assert offline.log.call_args.args[0][-1] == failed
    assert offline.log.call_args.kwargs["log_content"] is False
    assert "MLflow run_id=offline-run" in cli.stdout.getvalue()
    assert "Sukces: 1/2; błędy: 1" in cli.stdout.getvalue()
    assert "(n=2)" in cli.stdout.getvalue()
    assert_private_output(cli, "PRIVATE")


def test_cli_entrypoint_exits_nonzero_after_logging(db, monkeypatch, dataset, offline):
    path = dataset([{"q": "PRIVATE QUESTION"}])
    offline.evaluate.side_effect = None
    offline.evaluate.return_value = [
        {
            "id": "failed",
            "type": "pl",
            "question": "PRIVATE QUESTION",
            "metrics": {"success": 0.0},
            "output": {},
            "error": "evaluation_failed",
        }
    ]
    close_connections = Mock()
    monkeypatch.setattr(
        "django.core.management.base.connections.close_all", close_connections
    )
    stdout, stderr = StringIO(), StringIO()
    cmd = command.Command(stdout=stdout, stderr=stderr)
    with pytest.raises(SystemExit) as exc:
        cmd.run_from_argv(["manage.py", "eval_mlflow", str(path), "--task", "rag"])
    assert exc.value.code == 1
    offline.log.assert_called_once()
    assert "MLflow run_id=offline-run" in stdout.getvalue()
    assert "CommandError:" in stderr.getvalue()
    assert "PRIVATE" not in stdout.getvalue() + stderr.getvalue()
    close_connections.assert_called_once()


@pytest.mark.parametrize("log_content", [False, True])
def test_content_logging_requires_explicit_flag(
    retrieval_file, cli, offline, log_content
):
    arguments = ["--log-content"] if log_content else []
    cli.run(retrieval_file, *arguments)
    assert offline.evaluate.call_args.kwargs == {
        "task": "retrieval",
        "k": 5,
        "mode": "scientific",
        "include_content": log_content,
    }
    assert offline.log.call_args.kwargs["log_content"] is log_content
    assert (
        offline.log.call_args.kwargs["params"]["evaluation.log_content"] is log_content
    )
    assert ("UWAGA:" in cli.stderr.getvalue()) is log_content
    assert_private_output(cli, "PRIVATE RETRIEVAL QUESTION")


def test_rag_uses_one_sequential_service_call_per_case_without_public_content(
    db, monkeypatch, dataset, cli, offline, caplog
):
    rows = [
        {
            "id": "one",
            "q": "PRIVATE QUESTION ONE",
            "authors": ["Author"],
            "works": [],
            "include_ane": False,
            "include_patristics": True,
        },
        {"id": "two", "q": "PRIVATE QUESTION TWO", "mode": "scientific"},
    ]
    path = dataset(rows)
    events = []
    active = []

    def ask(question, **kwargs):
        assert not active, "A second stream started before the first completed"
        active.append(question)
        events.append(("start", question))
        yield "sources", {"chunks": [{"text": "PRIVATE SOURCE"}]}
        yield "delta", {"text": "PRIVATE DELTA"}
        yield "done", {"answer": "PRIVATE ANSWER", "refused": False}
        events.append(("end", question))
        active.pop()

    offline.ask.side_effect = ask
    monkeypatch.setattr(command, "evaluate_cases", evaluation.evaluate_cases)
    with caplog.at_level("DEBUG"):
        cli.run(path, "--task", "rag", "--mode", "popular", "--k", "99")
    assert events == [(event, row["q"]) for row in rows for event in ("start", "end")]
    assert active == []
    assert offline.ask.call_args_list == [
        call(
            rows[0]["q"],
            authors=["Author"],
            works=[],
            mode="popular",
            access=["open"],
            user_id=None,
            personal_only=False,
            include_ane=False,
            include_patristics=True,
        ),
        call(
            rows[1]["q"],
            authors=None,
            works=None,
            mode="scientific",
            access=["open"],
            user_id=None,
            personal_only=False,
            include_ane=None,
            include_patristics=None,
        ),
    ]
    offline.preflight.assert_called_once()
    offline.log.assert_called_once()
    offline.retrieve.assert_not_called()
    results = offline.log.call_args.args[0]
    assert [result["id"] for result in results] == ["one", "two"]
    assert all(result["metrics"]["success"] == 1.0 for result in results)
    assert offline.log.call_args.kwargs["log_content"] is False
    assert (
        offline.log.call_args.kwargs["params"]["evaluation.k"] == "configured_RAG_TOP_K"
    )
    assert_private_output(cli, "PRIVATE")
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("stage", ["preflight", "tracking"])
@pytest.mark.parametrize("exception", [RuntimeError, ValueError])
def test_tracking_failures_do_not_expose_credentials_or_content(
    retrieval_file, cli, offline, stage, exception
):
    message = "PRIVATE QUESTION https://username:PRIVATE_PASSWORD@tracking.example.invalid?token=PRIVATE_TOKEN"
    target = offline.preflight if stage == "preflight" else offline.log
    target.side_effect = exception(message)
    with pytest.raises(CommandError) as exc:
        cli.run(retrieval_file)
    public_error = str(exc.value) + cli.stdout.getvalue() + cli.stderr.getvalue()
    for secret in ("PRIVATE", "username", "tracking.example.invalid", "https://"):
        assert secret not in public_error
    assert exc.value.returncode == 1
    assert exc.value.__suppress_context__ is True
    if stage == "preflight":
        offline.evaluate.assert_not_called()
        offline.log.assert_not_called()
    else:
        offline.evaluate.assert_called_once()
        offline.log.assert_called_once()
    assert "MLflow run_id=" not in cli.stdout.getvalue()


@pytest.mark.parametrize("source", ["default", "environment", "explicit"])
def test_tracking_options_uri_precedence_and_no_credential_metadata(
    monkeypatch, settings, tmp_path, retrieval_file, cli, offline, source
):
    settings.BASE_DIR = tmp_path
    environment_uri = "https://env-user:PRIVATE_ENV_PASSWORD@env.example.invalid?token=PRIVATE_ENV_TOKEN"
    explicit_uri = "https://cli-user:PRIVATE_CLI_PASSWORD@cli.example.invalid?token=PRIVATE_CLI_TOKEN"
    if source != "default":
        monkeypatch.setenv("MLFLOW_TRACKING_URI", environment_uri)
    arguments = [
        "--experiment",
        "offline-experiment",
        "--run-name",
        "offline-name",
        "--dataset-version",
        "dataset-v1",
        "--corpus-version",
        "corpus-v1",
    ]
    if source == "explicit":
        arguments.extend(["--tracking-uri", explicit_uri])
    cli.run(retrieval_file, *arguments)
    expected_uri = {
        "default": f"sqlite:///{tmp_path / '.mlflow' / 'mlflow.db'}",
        "environment": environment_uri,
        "explicit": explicit_uri,
    }[source]
    offline.preflight.assert_called_once_with(expected_uri, "offline-experiment")
    kwargs = offline.log.call_args.kwargs
    assert kwargs["tracking_uri"] == expected_uri
    assert kwargs["experiment"] == "offline-experiment"
    assert kwargs["run_name"] == "offline-name"
    assert kwargs["tags"] == {
        "scriptura.git_commit": "a" * 40,
        "scriptura.git_dirty": "false",
        "scriptura.evaluation_task": "retrieval",
        "scriptura.dataset_version": "dataset-v1",
        "scriptura.corpus_version": "corpus-v1",
    }
    metadata = json.dumps([kwargs["params"], kwargs["tags"]], default=str)
    assert "PRIVATE" not in metadata
    assert "https://" not in metadata
    assert "tracking_uri" not in kwargs["params"]
    assert not (tmp_path / ".mlflow").exists()
    assert_private_output(cli, "PRIVATE", "https://", "env-user", "cli-user")
    if source != "default":
        assert command.os.environ["MLFLOW_TRACKING_URI"] == environment_uri
