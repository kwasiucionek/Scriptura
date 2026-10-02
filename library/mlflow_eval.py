"""Optional, offline export of precomputed evaluations to MLflow GenAI.

Importing this module does not import MLflow or Django. Only explicit params/tags
are exported; callers should supply non-secret experiment metadata, not settings.
Case content is excluded by default, including from native traces and datasets.
"""

import math
import os
import re
from collections import defaultdict
from numbers import Real
from pathlib import Path
from statistics import fmean
from urllib.parse import urlsplit

_ERROR = "evaluation_failed"
_REDACTED = "[redacted]"
_NAME = re.compile(r"^[\w .:/-]+$", re.ASCII)
_SENSITIVE = re.compile(
    r"token|secret|password|credential|api[_-]?key|authorization|cookie|"
    r"headers|settings|(?:^|[_-])(?:url|uri|error|exception|traceback)(?:$|[_-])",
    re.IGNORECASE,
)
_URL = re.compile(r"\b(?:https?|postgres(?:ql)?|sqlite|mysql)://", re.IGNORECASE)
_URL_SPAN = re.compile(
    r"\b(?:https?|postgres(?:ql)?|sqlite|mysql)://\S+", re.IGNORECASE
)


def _safe_content(value):
    """Even opt-in content never exports structured credentials or exceptions."""
    if isinstance(value, dict):
        return {
            key: _REDACTED if _SENSITIVE.search(str(key)) else _safe_content(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_content(item) for item in value]
    if isinstance(value, str):
        return _REDACTED if _URL.search(value) else value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    # Never stringify arbitrary objects: exception reprs can contain credentials.
    return _REDACTED


def _safe_metadata(values: dict) -> dict:
    return {
        key: _REDACTED if _SENSITIVE.search(str(key)) else _safe_content(value)
        for key, value in values.items()
    }


def _numeric_metrics(metrics: dict) -> dict[str, float]:
    numeric = {}
    for name, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, Real):
            continue
        value = float(value)
        if not math.isfinite(value):
            continue
        if (
            not isinstance(name, str)
            or not _NAME.fullmatch(name)
            or name == "evaluation_status"
        ):
            raise ValueError("Invalid or reserved evaluation metric name")
        numeric[name] = value
    return numeric


def _prepare_cases(results: list[dict], log_content: bool) -> list[dict]:
    cases = []
    for result in results:
        if not isinstance(result, dict) or "id" not in result or "type" not in result:
            raise ValueError("Evaluation cases must be records with id and type")
        case_id, case_type = result["id"], result["type"]
        if (
            not isinstance(case_id, (str, int))
            or isinstance(case_id, bool)
            or (isinstance(case_id, str) and not case_id.strip())
        ):
            raise ValueError("Evaluation case id must be a nonempty string or integer")
        if not isinstance(case_type, str) or not case_type.strip():
            raise ValueError("Evaluation case type must be a nonempty string")
        if case_type.strip().casefold() == "all":
            raise ValueError("Evaluation case type all is reserved")
        if not isinstance(result.get("metrics", {}), dict):
            raise ValueError("Evaluation case metrics must be a dictionary")
        case = {
            "id": case_id,
            "type": case_type,
            "metrics": _numeric_metrics(result.get("metrics", {})),
            "error": _ERROR if result.get("error") is not None else None,
        }
        if log_content:
            case["question"] = _safe_content(result.get("question"))
            case["output"] = _safe_content(result.get("output", {}))
        cases.append(case)
    return cases


def _encode_type(label: str) -> str:
    """Injective UTF-8 byte escaping; dots and underscores cannot alias paths.

    Only ASCII letters, digits and hyphens pass through. Every other byte is
    encoded as ``_xx_``, including underscores (the escape delimiter) and dots
    (the aggregate path separator). Raw labels remain in cases.json/traces.
    """
    return "".join(
        chr(byte)
        if 65 <= byte <= 90 or 97 <= byte <= 122 or 48 <= byte <= 57 or byte == 45
        else f"_{byte:02x}_"
        for byte in label.encode("utf-8")
    )


def _aggregate(cases: list[dict]) -> dict[str, float]:
    """Means use only applicable finite values, including real zeroes."""
    metrics = {"cases.count": float(len(cases)), "cases.failed": 0.0}
    samples = defaultdict(list)
    for case in cases:
        prefix = f"types.{_encode_type(case['type'])}"
        metrics[prefix + ".count"] = metrics.get(prefix + ".count", 0.0) + 1
        metrics.setdefault(prefix + ".failed", 0.0)
        if case["error"] is not None:
            metrics["cases.failed"] += 1
            metrics[prefix + ".failed"] += 1
        for name, value in case["metrics"].items():
            samples[f"metrics.{name}"].append(value)
            samples[f"{prefix}.metrics.{name}"].append(value)
    for name, values in samples.items():
        metrics[name + ".mean"] = fmean(values)
        metrics[name + ".count"] = float(len(values))
    return metrics


def _sqlite_artifacts(tracking_uri: str, *, create: bool = True) -> str | None:
    scheme = urlsplit(tracking_uri).scheme
    if scheme.split("+", 1)[0] != "sqlite":
        return None
    if ":///" not in tracking_uri:
        raise ValueError("Use a persistent SQLite tracking URI")
    database = tracking_uri.split(":///", 1)[1].partition("?")[0]
    if not database or database == ":memory:":
        raise ValueError("Use a persistent SQLite tracking URI")
    path = Path(database).resolve()
    artifacts = path.parent / f"{path.stem}-artifacts"
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(exist_ok=True)
    return artifacts.as_uri()


def require_mlflow():
    """Lazily import and return MLflow >=3.16,<4, disabling telemetry by default.

    Commands may call this before generation to fail early on missing or
    incompatible dependencies. No tracking store is opened or configured.
    """
    os.environ.setdefault("MLFLOW_ENABLE_TELEMETRY", "false")
    try:
        import mlflow
        from packaging.version import Version
    except ImportError:
        raise RuntimeError("Evaluation export requires mlflow>=3.16,<4") from None
    if not Version("3.16") <= Version(mlflow.__version__) < Version("4"):
        raise RuntimeError("Evaluation export requires mlflow>=3.16,<4")
    return mlflow


def preflight(
    tracking_uri: str,
    experiment: str,
    *,
    cases: list[dict] | None = None,
) -> None:
    """Validate export prerequisites before expensive application generation.

    Checks the URI shape, experiment name, dependency/version and active run.
    Optional case records need only ``id``/``type``; if metrics are present their
    names are checked too. Does not read questions/outputs, create directories,
    change tracking configuration, start runs or contact a tracking server.
    Server availability/permissions and SQLite writability are not probed.
    """
    if not isinstance(tracking_uri, str) or not tracking_uri:
        raise ValueError("An explicit database or server tracking URI is required")
    destination = urlsplit(tracking_uri)
    scheme = destination.scheme.split("+", 1)[0]
    if scheme == "file":
        raise ValueError("The obsolete MLflow file tracking store is not supported")
    if scheme not in {
        "sqlite",
        "http",
        "https",
        "postgresql",
        "mysql",
        "mssql",
        "databricks",
    }:
        raise ValueError(
            "An explicit supported database or server tracking URI is required"
        )
    if scheme in {"http", "https"} and not destination.netloc:
        raise ValueError("A tracking server URI must include a host")
    _sqlite_artifacts(tracking_uri, create=False)
    if not isinstance(experiment, str) or not experiment.strip():
        raise ValueError("An experiment name is required")
    if cases is not None:
        _prepare_cases(cases, log_content=False)
    mlflow = require_mlflow()
    if mlflow.active_run() is not None:
        raise RuntimeError("Finish the active MLflow run before exporting evaluation")


def _redact_urls(text: str) -> str:
    """Treść dla sędziów: wycina same adresy zamiast redagować cały tekst.

    ``_safe_content`` zastępuje cały napis z adresem, co w artykułach naukowych
    (DOI, linki) zabrałoby sędziemu cały fragment. Tu znika tylko adres —
    poświadczenia w URI (np. ``postgres://user:pass@host``) i tak w nim są."""
    return _URL_SPAN.sub(_REDACTED, text)


def _passages(output: dict) -> list[str]:
    """Fragmenty kontekstu w tej postaci, w jakiej widział je model odpowiedzi."""
    sources = output.get("sources") if isinstance(output, dict) else None
    if not isinstance(sources, dict):
        return []

    def items(key):
        return [item for item in sources.get(key) or [] if isinstance(item, dict)]

    passages = []
    for chunk in items("chunks"):
        head = f"[{chunk.get('n')}] {chunk.get('citation') or chunk.get('title') or ''}"
        if chunk.get("section"):
            head += f" — sekcja: {chunk['section']}"
        passages.append(f"{head}\n{chunk.get('text') or chunk.get('snippet') or ''}")
    for verse in items("verses"):
        passages.append(
            f"{verse.get('ref')} ({verse.get('work')}): {verse.get('text')}"
        )
    for verse in items("related"):
        if verse.get("text"):
            passages.append(
                f"{verse.get('ref')} ({verse.get('work')}): {verse['text']}"
            )
    for n, hit in enumerate(items("ane"), start=1):
        passages.append(f"[A{n}] {hit.get('ref')}: {hit.get('translation_en')}")
    for n, hit in enumerate(items("patristics"), start=1):
        passages.append(f"[P{n}] {hit.get('ref')}: {hit.get('text_en')}")
    return [_redact_urls(str(p)) for p in passages if str(p).strip()]


def _judge_payload(results: list[dict]) -> dict:
    """Pytanie, odpowiedź i fragmenty dla przypadków, które sędziowie mogą ocenić."""
    payload = {}
    for result in results:
        output = result.get("output") if isinstance(result, dict) else None
        output = output if isinstance(output, dict) else {}
        answer = (output.get("result") or {}).get("answer")
        judgeable = (
            result.get("error") is None
            and isinstance(answer, str)
            and bool(answer.strip())
        )
        payload[result["id"]] = {
            "question": _redact_urls(str(result.get("question") or "")),
            "answer": _redact_urls(answer) if judgeable else "",
            "passages": _passages(output) if judgeable else [],
            "judge": judgeable,
        }
    return payload


def log_evaluation(
    results: list[dict],
    *,
    tracking_uri: str,
    experiment: str,
    run_name: str | None,
    params: dict,
    tags: dict,
    log_content: bool = False,
    judges: list | None = None,
) -> str:
    """Log native GenAI assessments, aggregate metrics and ``cases.json``.

    Uses precomputed outputs, never a predictor, judge or application service.
    Missing/non-numeric/non-finite metrics are skipped, not treated as successes.
    Failed cases retain a generic error even when content logging is enabled.
    ``log_content`` opts into questions and outputs (credentials/URLs/structured
    exceptions remain redacted). IDs, types and metric names must be non-secret.

    MLflow >=3.16,<4 is required only when called. An existing active run is
    rejected before changing the tracking URI; the previous URI is restored.
    A new SQLite experiment stores artifacts beside its database, not in mlruns.
    Server-backed experiments use the server's default artifact location.
    Type labels are preserved as content; ``types.<encoded>.…`` metric paths
    use reversible UTF-8 byte escaping (e.g. ``pl+orig`` becomes ``pl_2b_orig``).
    Call ``preflight`` before generation to validate prerequisites early.
    Empty evaluations log counts and an empty artifact, without invoking GenAI.
    This function, like ``mlflow.genai.evaluate``, is not thread-safe.

    ``judges`` (scorery z ``library.judges``, wymaga ``log_content``): zamiast
    zapisanych wyników ewaluacja odtwarza każdy przypadek przez ``predict_fn`` —
    span RETRIEVER z fragmentami kontekstu i odpowiedź jako wynik — żeby scorery
    RAGAS/DeepEval widziały kontekst. Metryki deterministyczne pozostają w tych
    samych śladach; przypadki z błędem lub pustą odpowiedzią nie są oceniane.
    """
    if judges and not log_content:
        raise ValueError("LLM judges require log_content")
    preflight(tracking_uri, experiment, cases=results)
    cases = _prepare_cases(results, log_content)
    mlflow = require_mlflow()
    if judges:
        return _log_judged(
            mlflow,
            results,
            cases,
            judges=judges,
            tracking_uri=tracking_uri,
            experiment=experiment,
            run_name=run_name,
            params=params,
            tags=tags,
        )

    from mlflow.entities import Feedback
    from mlflow.genai.scorers import scorer

    @scorer
    def precomputed_metrics(outputs):
        feedback = [
            Feedback(name=name, value=float(value))
            for name, value in outputs["metrics"].items()
        ]
        feedback.append(
            Feedback(
                name="evaluation_status",
                value="failed" if outputs["error"] is not None else "ok",
            )
        )
        return feedback

    rows = []
    for case in cases:
        inputs = {"case_id": case["id"]}
        if log_content:
            inputs["question"] = case["question"]
        outputs = {key: case[key] for key in ("type", "metrics", "error")}
        if log_content:
            outputs["output"] = case["output"]
        rows.append({"inputs": inputs, "outputs": outputs})

    previous_uri = mlflow.get_tracking_uri()
    try:
        experiment_id = _experiment_id(mlflow, tracking_uri, experiment)
        with mlflow.start_run(experiment_id=experiment_id, run_name=run_name) as run:
            mlflow.log_params(_safe_metadata(params))
            mlflow.set_tags(_safe_metadata(tags))
            mlflow.log_metrics(_aggregate(cases))
            mlflow.log_dict(cases, "cases.json")
            if rows:
                evaluation = mlflow.genai.evaluate(
                    data=rows, scorers=[precomputed_metrics]
                )
                if evaluation.run_id != run.info.run_id:
                    raise RuntimeError("MLflow GenAI evaluation used a different run")
            return run.info.run_id
    finally:
        mlflow.set_tracking_uri(previous_uri)


def _experiment_id(mlflow, tracking_uri: str, experiment: str) -> str:
    """Ustawia tracking URI i zwraca ID eksperymentu (tworzy go w razie potrzeby)."""
    artifact_location = _sqlite_artifacts(tracking_uri)
    mlflow.set_tracking_uri(tracking_uri)
    existing = mlflow.get_experiment_by_name(experiment)
    if existing is not None:
        return existing.experiment_id
    return mlflow.create_experiment(experiment, artifact_location=artifact_location)


def _log_judged(
    mlflow,
    results: list[dict],
    cases: list[dict],
    *,
    judges: list,
    tracking_uri: str,
    experiment: str,
    run_name: str | None,
    params: dict,
    tags: dict,
) -> str:
    """Wariant ``log_evaluation`` z sędziami LLM: odtworzenie przypadków przez
    ``predict_fn`` ze spanem RETRIEVER, jedna ewaluacja dla metryk i sędziów."""
    from mlflow.entities import Feedback, SpanType
    from mlflow.genai.scorers import scorer

    payload = _judge_payload(results)
    by_id = {case["id"]: case for case in cases}

    @scorer(name="precomputed_metrics")
    def precomputed_metrics(inputs):
        case = by_id[inputs["case_id"]]
        feedback = [
            Feedback(name=name, value=float(value))
            for name, value in case["metrics"].items()
        ]
        feedback.append(
            Feedback(
                name="evaluation_status",
                value="failed" if case["error"] is not None else "ok",
            )
        )
        return feedback

    # Span główny (CHAIN) z odpowiedzią jako wynikiem: bez niego korzeniem śladu
    # zostaje span RETRIEVER, a sędziowie z UI MLflow ({{ outputs }}) oceniają
    # fragmenty kontekstu zamiast odpowiedzi.
    @mlflow.trace(name="scriptura_rag", span_type=SpanType.CHAIN)
    def replay(case_id, question, type, judge):
        # bez wywołań modeli: odpowiedź i kontekst zapisane podczas ewaluacji
        item = payload[case_id]
        with mlflow.start_span(name="retrieval", span_type=SpanType.RETRIEVER) as span:
            span.set_inputs({"query": question})
            span.set_outputs([{"page_content": text} for text in item["passages"]])
        return item["answer"]

    rows = [
        {
            "inputs": {
                "case_id": case["id"],
                "type": case["type"],
                "question": payload[case["id"]]["question"],
                "judge": payload[case["id"]]["judge"],
            }
        }
        for case in cases
    ]
    judged = sum(item["judge"] for item in payload.values())

    previous_uri = mlflow.get_tracking_uri()
    try:
        experiment_id = _experiment_id(mlflow, tracking_uri, experiment)
        with mlflow.start_run(experiment_id=experiment_id, run_name=run_name) as run:
            mlflow.log_params(_safe_metadata(params))
            mlflow.set_tags(_safe_metadata(tags))
            mlflow.log_metrics({**_aggregate(cases), "judges.cases": float(judged)})
            mlflow.log_dict(cases, "cases.json")
            if rows:
                evaluation = mlflow.genai.evaluate(
                    data=rows,
                    predict_fn=replay,
                    scorers=[precomputed_metrics, *judges],
                )
                if evaluation.run_id != run.info.run_id:
                    raise RuntimeError("MLflow GenAI evaluation used a different run")
            return run.info.run_id
    finally:
        mlflow.set_tracking_uri(previous_uri)
