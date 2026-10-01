"""Explicit offline evaluation; no MLflow hooks in the web request path."""

import hashlib
import json
import logging
import os
import subprocess
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from library.evaluation import aggregate_results, evaluate_cases, load_dataset
from library.mlflow_eval import log_evaluation, preflight

_CONFIG_KEYS = (
    "SEARCH_BACKEND",
    "OPENSEARCH_INDEX_PREFIX",
    "EMBEDDINGS_BACKEND",
    "OLLAMA_EMBED_MODEL",
    "EMBEDDING_DIM",
    "EMBEDDING_QUERY_PREFIX",
    "RERANKER_BACKEND",
    "RERANK_TOP_N",
    "RERANK_MAX_CHARS",
    "RERANK_LLM_MIN_SCORE",
    "RERANK_LLM_MAX_CHARS",
    "LLM_BACKEND",
    "OLLAMA_CHAT_MODEL",
    "OPENAI_CHAT_MODEL",
    "OPENAI_THINKING_KWARGS",
    "RAG_TOP_K",
    "RAG_MAX_PER_DOC",
    "RAG_CONTEXT_NEIGHBORS",
    "RAG_REGISTER_MODE",
    "RAG_TRANSLATE_QUERY",
    "RAG_TRANSLATE_MODEL",
    "RAG_VERSE_WORKS",
    "RAG_RELATED_VERSES",
    "RAG_ANE_PASSAGES",
    "RAG_PATRISTIC_PASSAGES",
    "RAG_TEMPERATURE",
    "RAG_NUM_CTX",
    "RAG_THINK",
    "LLM_REASONING_BUDGET",
)


@contextmanager
def _quiet_backend_logs():
    # This standalone command is sequential. Provider exception logs can echo
    # prompts or credentials before the evaluator receives its sanitized error.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous)


def _git_metadata() -> dict:
    tags = {}
    for key, args in (
        ("scriptura.git_commit", ["rev-parse", "HEAD"]),
        ("scriptura.git_dirty", ["--no-optional-locks", "status", "--porcelain"]),
    ):
        try:
            result = subprocess.run(
                ["git", "--no-pager", *args],
                cwd=settings.BASE_DIR,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            tags[key] = (
                str(bool(result.stdout.strip())).lower()
                if key.endswith("dirty")
                else result.stdout.strip()
            )
    return tags


def _fingerprint(cases) -> str:
    rows = []
    for case in cases:
        row = asdict(case)
        row.pop("note", None)
        rows.append(row)
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _validate_relevant(cases) -> None:
    from library.models import Chunk

    relevant = {value for case in cases for value in case.relevant}
    documents = {int(value.split(":")[0]) for value in relevant}
    existing = {
        f"{document_id}:{order}"
        for document_id, order in Chunk.objects.filter(
            document__access="open", document_id__in=documents
        ).values_list("document_id", "order")
    }
    missing = relevant - existing
    if missing:
        raise CommandError(
            f"{len(missing)} etykiet relevant nie istnieje w otwartym korpusie. "
            "Sprawdź bazę, wersję zbioru i identyfikatory przed ewaluacją."
        )


def _corpus_metadata() -> dict:
    from library.models import Chunk, Document

    digest = hashlib.sha256()
    docs = Document.objects.filter(access="open").order_by("pk")
    for row in docs.values_list(
        "pk", "modified", "content_hash", "chunk_count", "language", "register"
    ).iterator():
        digest.update(json.dumps(row, default=str, ensure_ascii=False).encode())
        digest.update(b"\n")
    return {
        "corpus.open_documents": docs.count(),
        "corpus.open_chunks": Chunk.objects.filter(document__access="open").count(),
        # This is metadata identity, not a hash of all texts or OpenSearch vectors.
        "corpus.metadata_sha256": digest.hexdigest(),
    }


class Command(BaseCommand):
    help = (
        "Ewaluacja retrieval/RAG w MLflow GenAI (domyślnie lokalnie, tylko źródła open)"
    )

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path, help="Przejrzany zbiór JSONL")
        parser.add_argument("--task", choices=["retrieval", "rag"], default="retrieval")
        parser.add_argument(
            "--k", type=int, default=5, help="Top-k tylko dla retrieval"
        )
        parser.add_argument(
            "--mode", choices=["scientific", "popular"], default="scientific"
        )
        parser.add_argument("--no-rerank", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument(
            "--tracking-uri",
            help="MLflow URI; domyślnie MLFLOW_TRACKING_URI lub .mlflow/mlflow.db",
        )
        parser.add_argument("--experiment", default="scriptura-evaluation")
        parser.add_argument("--run-name")
        parser.add_argument(
            "--dataset-version", help="Własna etykieta wersji benchmarku"
        )
        parser.add_argument(
            "--corpus-version", help="Własna etykieta wersji korpusu/indeksu"
        )
        parser.add_argument(
            "--log-content",
            action="store_true",
            help="Jawnie zapisz pytania, odpowiedzi i fragmenty w MLflow",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Walidacja zbioru i etykiet bez modeli i zapisu MLflow",
        )

    def handle(self, *args, **options):
        task = options["task"]
        if options["k"] < 1:
            raise CommandError("--k musi być dodatnie")
        try:
            cases = load_dataset(options["file"], task=task, limit=options["limit"])
        except (OSError, ValueError, UnicodeError) as exc:
            raise CommandError(f"Nieprawidłowy zbiór ewaluacyjny: {exc}") from None
        if not cases:
            raise CommandError("Zbiór ewaluacyjny jest pusty")
        if task == "retrieval":
            _validate_relevant(cases)
        if options["dry_run"]:
            self.stdout.write(
                f"Zbiór poprawny: {len(cases)} przypadków; task={task}; access=open"
            )
            return
        uri = (
            options["tracking_uri"]
            or os.environ.get("MLFLOW_TRACKING_URI")
            or (f"sqlite:///{Path(settings.BASE_DIR) / '.mlflow' / 'mlflow.db'}")
        )
        try:
            preflight(uri, options["experiment"])
        except (RuntimeError, ValueError):
            raise CommandError(
                "Nieprawidłowa konfiguracja MLflow lub brak zgodnej biblioteki. "
                "Sprawdź URI, nazwę eksperymentu i aktywny run. "
                "Zależności: pip install -e '.[eval]'."
            ) from None
        if options["log_content"]:
            self.stderr.write(
                "UWAGA: pytania, odpowiedzi i fragmenty trafią do skonfigurowanego MLflow."
            )
        self.stdout.write(
            f"Ewaluacja {task}: {len(cases)} przypadków, tylko źródła open."
        )
        overrides = {"RERANKER_BACKEND": "none"} if options["no_rerank"] else {}
        with override_settings(**overrides):
            from rag.service import MODE_INSTRUCTIONS, SYSTEM_PROMPT

            params = {key.lower(): getattr(settings, key) for key in _CONFIG_KEYS}
            params.update(_corpus_metadata())
            params.update(
                {
                    "evaluation.task": task,
                    "evaluation.access": "open",
                    "evaluation.k": options["k"]
                    if task == "retrieval"
                    else "configured_RAG_TOP_K",
                    "evaluation.mode": options["mode"],
                    "evaluation.log_content": options["log_content"],
                    "dataset.case_count": len(cases),
                    "dataset.sha256": _fingerprint(cases),
                    "prompt.sha256": hashlib.sha256(
                        json.dumps(
                            [SYSTEM_PROMPT, MODE_INSTRUCTIONS],
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),
                }
            )
            tags = _git_metadata()
            tags["scriptura.evaluation_task"] = task
            for name in ("dataset_version", "corpus_version"):
                if options[name]:
                    tags[f"scriptura.{name}"] = options[name]
            with _quiet_backend_logs():
                results = evaluate_cases(
                    cases,
                    task=task,
                    k=options["k"],
                    mode=options["mode"],
                    include_content=options["log_content"],
                )
            try:
                run_id = log_evaluation(
                    results,
                    tracking_uri=uri,
                    experiment=options["experiment"],
                    run_name=options["run_name"] or f"{task}-baseline",
                    params=params,
                    tags=tags,
                    log_content=options["log_content"],
                )
            except Exception:
                # Tracking exceptions can contain URLs, tokens and uploaded content.
                raise CommandError(
                    "Nie udało się zapisać ewaluacji w MLflow. Sprawdź backend tracking, "
                    "uprawnienia i instalację. Wyniki nie zostały potwierdzone."
                ) from None
        summary = aggregate_results(results)["all"]
        self.stdout.write(f"MLflow run_id={run_id}")
        self.stdout.write(
            f"Sukces: {summary['success_count']}/{summary['count']}; błędy: {summary['error_count']}"
        )
        for name, value in sorted(summary["metrics"].items()):
            self.stdout.write(
                f"  {name}: {value:.4f} (n={summary['metric_counts'][name]})"
            )
        if summary["error_count"]:
            raise CommandError(
                "Ewaluacja zapisana, ale część przypadków zakończyła się błędem; sprawdź cases.json."
            )
