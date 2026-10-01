"""Opt-in command -> evaluator -> native MLflow smoke, isolated and network-free."""

import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
METADATA_KEYS = {
    "corpus.open_documents",
    "corpus.open_chunks",
    "corpus.metadata_sha256",
    "evaluation.task",
    "evaluation.access",
    "evaluation.k",
    "evaluation.mode",
    "evaluation.log_content",
    "dataset.case_count",
    "dataset.sha256",
    "prompt.sha256",
}
RESULT_PREFIX = "NATIVE_MLFLOW_RESULT="


@pytest.mark.skipif(
    os.environ.get("RUN_MLFLOW_SMOKE") != "1",
    reason="Opt-in native command smoke: RUN_MLFLOW_SMOKE=1; temporary SQLite only",
)
def test_native_eval_mlflow_command_sqlite_end_to_end(tmp_path):
    try:
        version = importlib.metadata.version("mlflow")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("Optional MLflow dependency is not installed")
    from packaging.version import Version

    if not Version("3.16") <= Version(version) < Version("4"):
        pytest.skip("Native export supports MLflow >=3.16,<4")
    # Keep MLflow globals, native evaluator threads and Django's test DB in a child.
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'corpus.sqlite3'}",
        "MLFLOW_ENABLE_TELEMETRY": "false",
        "MLFLOW_ENABLE_ASYNC_LOGGING": "false",
        "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'unused-tracking.db'}",
        "MLFLOW_TRACKING_TOKEN": "PRIVATE TRACKING TOKEN",
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summaries = [
        json.loads(line.removeprefix(RESULT_PREFIX))
        for line in result.stdout.splitlines()
        if line.startswith(RESULT_PREFIX)
    ]
    assert len(summaries) == 1, result.stdout + result.stderr
    summary = summaries[0]
    assert summary["mlflow_version"] == version
    assert summary["network_attempts"] == 0
    assert [run["name"] for run in summary["runs"]] == [
        "retrieval",
        "rag",
        "rag-failed",
    ]
    assert [run["traces"] for run in summary["runs"]] == [1, 2, 3]
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _native_smoke(root):
    import sqlite3
    from contextlib import ExitStack
    from io import StringIO
    from types import SimpleNamespace
    from unittest.mock import Mock, call, patch

    network_attempts = []

    def no_network(*args, **kwargs):
        network_attempts.append((args, kwargs))
        raise AssertionError("Native command smoke must not connect to any service")

    with ExitStack() as stack:
        for target in (
            "socket.socket.connect",
            "socket.socket.connect_ex",
            "socket.create_connection",
        ):
            stack.enter_context(patch(target, side_effect=no_network))
        import django
        import mlflow
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.db import connection
        from django.test.utils import (
            override_settings,
            setup_databases,
            teardown_databases,
        )
        from mlflow import MlflowClient

        django.setup()
        from library import embeddings, llm, rerank, search
        from library.management.commands import eval_mlflow as command
        from library.models import Chunk, Document
        from rag import service

        assert connection.vendor == "sqlite"
        stack.enter_context(
            override_settings(
                BASE_DIR=root,
                SEARCH_BACKEND="db",
                LLM_BACKEND="echo",
                RERANKER_BACKEND="none",
                OLLAMA_CHAT_MODEL="offline-chat-model",
                RAG_TOP_K=7,
                SECRET_KEY="PRIVATE DJANGO SECRET",
                OPENAI_API_KEY="PRIVATE OPENAI TOKEN",
                OLLAMA_API_KEY="PRIVATE OLLAMA TOKEN",
                TEI_TOKEN="PRIVATE TEI TOKEN",
                DEMO_TOKEN="PRIVATE DEMO TOKEN",
                OPENAI_BASE_URL="https://user:PRIVATE@openai.example.invalid",
                OLLAMA_BASE_URL="https://user:PRIVATE@ollama.example.invalid",
                TEI_EMBED_URL="https://user:PRIVATE@tei.example.invalid",
                OPENSEARCH_URL="https://user:PRIVATE@search.example.invalid",
            )
        )
        database_config = setup_databases(verbosity=0, interactive=False)
        stack.callback(teardown_databases, database_config, verbosity=0)
        document = Document.objects.create(
            title="PRIVATE DOCUMENT TITLE",
            access="open",
            chunk_count=4,
            content_hash="a" * 32,
            note="PRIVATE DOCUMENT NOTE",
        )
        chunks = [
            Chunk.objects.create(
                document=document, order=order, text=f"PRIVATE SOURCE {order}"
            )
            for order in range(4)
        ]
        closed = Document.objects.create(
            title="PRIVATE CLOSED DOCUMENT", access="private"
        )
        Chunk.objects.create(document=closed, order=0, text="PRIVATE CLOSED SOURCE")
        stack.enter_context(
            patch.object(
                command,
                "_git_metadata",
                return_value={"scriptura.git_commit": "a" * 40},
            )
        )
        model_guards = []
        for module, name in (
            (llm, "chat"),
            (llm, "chat_stream"),
            (embeddings, "embed"),
            (rerank, "rerank"),
        ):
            guard = Mock(
                side_effect=AssertionError("Unexpected external model invocation")
            )
            stack.enter_context(patch.object(module, name, guard))
            model_guards.append(guard)
        retrieve = stack.enter_context(patch.object(search, "retrieve"))
        ask = stack.enter_context(patch.object(service, "ask"))
        uri = f"sqlite:///{root / 'tracking.db'}"
        client = MlflowClient(tracking_uri=uri)
        previous_uri = mlflow.get_tracking_uri()
        reports = []

        def execute(name, task, rows, *, fails=False):
            path = root / f"{name}.jsonl"
            path.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            stdout, stderr = StringIO(), StringIO()
            arguments = [
                str(path),
                "--task",
                task,
                "--k",
                "3" if task == "retrieval" else "99",
                "--tracking-uri",
                uri,
                "--experiment",
                "native-command-smoke",
                "--run-name",
                name,
            ]
            if fails:
                with pytest.raises(CommandError, match="Ewaluacja zapisana") as error:
                    call_command(
                        "eval_mlflow", *arguments, stdout=stdout, stderr=stderr
                    )
                assert error.value.returncode == 1
                assert "PRIVATE" not in str(error.value)
            else:
                call_command("eval_mlflow", *arguments, stdout=stdout, stderr=stderr)
            assert stderr.getvalue() == ""
            assert "PRIVATE" not in stdout.getvalue()
            run_lines = [
                line
                for line in stdout.getvalue().splitlines()
                if line.startswith("MLflow run_id=")
            ]
            assert len(run_lines) == 1, stdout.getvalue()
            run_id = run_lines[0].partition("=")[2]
            assert mlflow.active_run() is None
            assert mlflow.get_tracking_uri() == previous_uri
            run = client.get_run(run_id)
            assert run.info.status == "FINISHED"
            assert run.data.tags["mlflow.runType"] == "genai_evaluate"
            assert run.data.tags["mlflow.runName"] == name
            assert run.data.tags["scriptura.evaluation_task"] == task
            params = run.data.params
            assert set(params) == SAFE_CONFIG_KEYS | METADATA_KEYS
            assert params["search_backend"] == "db"
            assert params["llm_backend"] == "echo"
            assert params["ollama_chat_model"] == "offline-chat-model"
            assert params["rag_top_k"] == "7"
            assert params["evaluation.task"] == task
            assert params["evaluation.access"] == "open"
            assert params["evaluation.log_content"] == "False"
            assert params["evaluation.k"] == (
                "3" if task == "retrieval" else "configured_RAG_TOP_K"
            )
            assert params["dataset.case_count"] == str(len(rows))
            assert params["corpus.open_documents"] == "1"
            assert params["corpus.open_chunks"] == "4"
            for key in ("dataset.sha256", "prompt.sha256", "corpus.metadata_sha256"):
                assert len(params[key]) == 64
                int(params[key], 16)
            metadata = json.dumps([params, run.data.tags])
            assert "PRIVATE" not in metadata
            # MLflow adds its own public git.repoURL tag; the command must not export URLs.
            command_metadata = json.dumps(
                [
                    params,
                    {
                        key: value
                        for key, value in run.data.tags.items()
                        if key.startswith("scriptura.")
                    },
                ]
            )
            assert "https://" not in command_metadata
            assert run.data.metrics["cases.count"] == len(rows)
            assert run.data.metrics["cases.failed"] == int(fails)
            experiment = client.get_experiment(run.info.experiment_id)
            assert (
                experiment.artifact_location == (root / "tracking-artifacts").as_uri()
            )
            artifacts = client.list_artifacts(run_id)
            assert "cases.json" in [artifact.path for artifact in artifacts]
            artifact_path = Path(client.download_artifacts(run_id, "cases.json"))
            cases = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert [case["id"] for case in cases] == [row["id"] for row in rows]
            assert all(
                set(case) == {"id", "type", "metrics", "error"} for case in cases
            )
            traces = client.search_traces(
                locations=[run.info.experiment_id],
                filter_string=f'trace.run_id = "{run_id}"',
            )
            assert len(traces) == len(rows)
            assessments = {}
            cases_by_id = {case["id"]: case for case in cases}
            for trace in traces:
                request = json.loads(trace.info.request_preview)
                assert set(request) == {"case_id"}
                case_id = request["case_id"]
                case = cases_by_id[case_id]
                response = json.loads(trace.info.response_preview)
                assert response == {
                    key: case[key] for key in ("type", "metrics", "error")
                }
                feedback = {
                    assessment.name: assessment.feedback.value
                    for assessment in trace.info.assessments
                }
                assert feedback == {
                    **case["metrics"],
                    "evaluation_status": "failed" if case["error"] else "ok",
                }
                assessments[case_id] = feedback
            assert "PRIVATE" not in json.dumps([trace.to_dict() for trace in traces])
            reports.append(
                {
                    "name": name,
                    "run_id": run_id,
                    "traces": len(traces),
                    "cases_failed": run.data.metrics["cases.failed"],
                    "command_error": fails,
                }
            )
            return run, cases_by_id, assessments

        def assert_metric(run, name, value, count):
            assert run.data.metrics[f"metrics.{name}.mean"] == pytest.approx(value)
            assert run.data.metrics[f"metrics.{name}.count"] == count
            assert run.data.metrics[f"{name}/mean"] == pytest.approx(value)

        retrieval_rows = [
            {
                "id": "retrieval-one",
                "q": "PRIVATE RETRIEVAL QUESTION",
                "type": "pl",
                "relevant": [f"{document.pk}:0", f"{document.pk}:2"],
                "note": "PRIVATE DATASET NOTE",
            }
        ]
        retrieve.return_value = [
            SimpleNamespace(
                document_id=chunk.document_id, order=chunk.order, text=chunk.text
            )
            for chunk in chunks[1:]
        ]
        run, cases, assessments = execute("retrieval", "retrieval", retrieval_rows)
        retrieve.assert_called_once_with(
            retrieval_rows[0]["q"],
            k=3,
            access=["open"],
            authors=None,
            sigla=None,
            user_id=None,
            personal_only=False,
        )
        ask.assert_not_called()
        for metric, value in {
            "recall_at_k": 0.5,
            "mrr_at_k": 0.5,
            "hit_at_1": 0.0,
            "near_at_k": 1.0,
        }.items():
            assert_metric(run, metric, value, 1)
            assert assessments["retrieval-one"][metric] == value
            assert cases["retrieval-one"]["metrics"][metric] == value
        reports[-1].update(recall_at_k=0.5, mrr_at_k=0.5)

        rag_rows = [
            {
                "id": "rag-scored",
                "q": "PRIVATE SCORED QUESTION",
                "expected_refused": True,
                "required_terms": ["ḥesed", "absent-term"],
            },
            {
                "id": "rag-unchecked",
                "q": "PRIVATE UNCHECKED QUESTION",
                "expected_refused": True,
            },
        ]
        failed_row = {"id": "rag-error", "q": "PRIVATE FAILING QUESTION"}
        events = []
        active = []

        def ask_stream(question, **kwargs):
            assert not active, "RAG streams must be consumed sequentially"
            active.append(question)
            events.append(question)
            try:
                yield (
                    "sources",
                    {
                        "chunks": [
                            {"document_id": document.pk, "text": "PRIVATE SOURCE"}
                        ]
                    },
                )
                yield "delta", {"text": "PRIVATE DELTA"}
                if question == failed_row["q"]:
                    yield (
                        "error",
                        {
                            "detail": "PRIVATE EXCEPTION https://user:PRIVATE@backend.invalid"
                        },
                    )
                    return
                if question == rag_rows[0]["q"]:
                    payload = {
                        "answer": "PRIVATE ANSWER ḥesed",
                        "refused": True,
                        "quotes_checked": 4,
                        "quotes_verified": 3,
                        "quotes_altered": ["PRIVATE ALTERED QUOTE"],
                        "quotes_unverified": [],
                        "citation_checks": [
                            {"status": "ok", "refs": [1, 2]},
                            {
                                "status": "invalid",
                                "reason": "missing_source",
                                "refs": [3, 4],
                                "missing": [4],
                            },
                        ],
                    }
                else:
                    payload = {"answer": "PRIVATE UNCHECKED ANSWER", "refused": False}
                yield "done", payload
            finally:
                active.pop()

        ask.side_effect = ask_stream
        for name, rows, fails in (
            ("rag", rag_rows, False),
            ("rag-failed", [rag_rows[0], failed_row, rag_rows[1]], True),
        ):
            ask.reset_mock()
            events.clear()
            run, cases, assessments = execute(name, "rag", rows, fails=fails)
            assert events == [row["q"] for row in rows]
            assert active == []
            assert ask.call_args_list == [
                call(
                    row["q"],
                    authors=None,
                    works=None,
                    mode="scientific",
                    access=["open"],
                    user_id=None,
                    personal_only=False,
                    include_ane=None,
                    include_patristics=None,
                )
                for row in rows
            ]
            assert retrieve.call_count == 1
            expected = {
                "quote_verification_rate": (0.75, 1),
                "citation_validity_rate": (0.75, 1),
                "citation_checked_count": (4.0, 1),
                "citation_valid_count": (3.0, 1),
                "citation_invalid_count": (1.0, 1),
                "refusal_accuracy": (0.5, 2),
                "required_terms_coverage": (0.5, 1),
            }
            for metric, (value, count) in expected.items():
                assert_metric(run, metric, value, count)
            assert assessments["rag-scored"]["refusal_accuracy"] == 1.0
            assert assessments["rag-unchecked"]["refusal_accuracy"] == 0.0
            for metric in (
                "quote_verification_rate",
                "citation_validity_rate",
                "citation_checked_count",
            ):
                assert metric not in cases["rag-unchecked"]["metrics"]
                assert metric not in assessments["rag-unchecked"]
            assert cases["rag-unchecked"]["metrics"]["quotes_checked"] == 0.0
            if fails:
                failed = cases["rag-error"]
                assert failed["error"] == "evaluation_failed"
                assert set(failed["metrics"]) == {"success", "latency_ms"}
                assert failed["metrics"]["success"] == 0.0
                assert assessments["rag-error"]["evaluation_status"] == "failed"
                assert "quote_verification_rate" not in assessments["rag-error"]
                assert_metric(run, "success", 2 / 3, 3)
            else:
                assert_metric(run, "success", 1.0, 2)
            reports[-1].update(
                quote_verification_rate=0.75,
                citation_validity_rate=0.75,
                refusal_accuracy=0.5,
            )

        for guard in model_guards:
            guard.assert_not_called()
        for artifact in (root / "tracking-artifacts").rglob("*"):
            if artifact.is_file():
                assert b"PRIVATE" not in artifact.read_bytes(), artifact
        with sqlite3.connect(root / "tracking.db") as tracking:
            assert "PRIVATE" not in "\n".join(tracking.iterdump())
        assert not (root / "unused-tracking.db").exists()
        assert network_attempts == []
        return {
            "mlflow_version": mlflow.__version__,
            "network_attempts": len(network_attempts),
            "runs": reports,
        }


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    print(RESULT_PREFIX + json.dumps(_native_smoke(Path(sys.argv[1])), sort_keys=True))
