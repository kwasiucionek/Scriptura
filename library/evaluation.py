"""Offline-testable evaluation core; no MLflow dependency or logging side effects.

``load_dataset`` validates the entire JSONL file, including rows beyond ``limit``.
``evaluate_cases`` also validates all supplied cases before invoking any backend.
Retrieval follows scripts/eval_retrieval.py, with explicit open-only ACL. RAG
uses the existing ask pipeline and its configured top-k (the evaluator's ``k``
is retrieval-only). RAG sources lack chunk order, so no retrieval metrics are
inferred from their document IDs.

Outputs contain questions and, for RAG, answers/source snippets. A command must
redact these unless --log-content is enabled; ``note`` stays on EvaluationCase
and is never copied to a result. Required-term coverage is casefolded substring
matching: a LEXICAL proxy, not semantic correctness or factual truth. Citation
validity checks source existence, not whether a source supports a claim.
"""

import hashlib
import json
import math
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TypedDict


@dataclass
class EvaluationCase:
    id: str
    q: str
    type: str = "pl"
    relevant: list[str] = field(default_factory=list)
    authors: list[str] | None = None  # names, not Author IDs or slugs
    works: list[str] | None = None  # work codes; [] explicitly disables works
    expected_refused: bool | None = None
    required_terms: list[str] = field(default_factory=list)
    include_ane: bool | None = None
    include_patristics: bool | None = None
    mode: str | None = None  # optional per-case override
    note: str | None = None


class EvaluationResult(TypedDict):
    id: str
    type: str
    question: str
    metrics: dict[str, float]
    output: dict
    error: str | None


class AggregateSummary(TypedDict):
    count: int
    success_count: int
    error_count: int
    metrics: dict[str, float]
    metric_counts: dict[str, int]


class EvaluationAggregate(TypedDict):
    all: AggregateSummary
    by_type: dict[str, AggregateSummary]


_CHUNK_ID = re.compile(r"[0-9]+:[0-9]+\Z")
_STAGE_TIME = re.compile(r"[a-z][a-z0-9_]*_ms\Z")
_MODES = ("scientific", "popular")


def _task(task: str) -> None:
    if task not in ("retrieval", "rag"):
        raise ValueError("task must be retrieval or rag")


def _positive_int(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _strings(value, name: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{name} must be a list of nonempty strings")
    return [item.strip() for item in value]


def _optional_strings(value, name: str) -> list[str] | None:
    return None if value is None else _strings(value, name)


def _case(row: dict, task: str) -> EvaluationCase:
    if not isinstance(row, dict):
        raise ValueError("case must be a JSON object")
    q = row.get("q")
    if not isinstance(q, str) or not q.strip():
        raise ValueError("q must be a nonempty string")
    kind = row.get("type", "pl")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("type must be a nonempty string")
    kind = kind.strip()
    if kind.casefold() == "all":
        raise ValueError("type all is reserved")
    relevant = _strings(row.get("relevant", []), "relevant")
    normalized = []
    for value in relevant:
        if not _CHUNK_ID.fullmatch(value):
            raise ValueError("relevant entries must have document_id:order format")
        document_id, order = map(int, value.split(":"))
        if document_id < 1:
            raise ValueError("relevant document IDs must be positive")
        normalized.append(f"{document_id}:{order}")
    if task == "retrieval" and not normalized:
        raise ValueError("retrieval requires nonempty relevant")
    for key in ("expected_refused", "include_ane", "include_patristics"):
        if row.get(key) is not None and type(row[key]) is not bool:
            raise ValueError(f"{key} must be a boolean or null")
    mode = row.get("mode")
    if mode is not None and mode not in _MODES:
        raise ValueError("mode must be scientific or popular")
    note = row.get("note")
    if note is not None and not isinstance(note, str):
        raise ValueError("note must be a string or null")
    case_id = row.get("id")
    if "id" in row and (not isinstance(case_id, str) or not case_id.strip()):
        raise ValueError("id must be a nonempty string")
    case = EvaluationCase(
        id=case_id.strip() if case_id is not None else "",
        q=q.strip(),
        type=kind,
        relevant=list(dict.fromkeys(normalized)),
        authors=_optional_strings(row.get("authors"), "authors"),
        works=_optional_strings(row.get("works"), "works"),
        expected_refused=row.get("expected_refused"),
        required_terms=_strings(row.get("required_terms", []), "required_terms"),
        include_ane=row.get("include_ane"),
        include_patristics=row.get("include_patristics"),
        mode=mode,
        note=note,
    )
    if not case.id:
        identity = asdict(case)
        del identity["id"], identity["note"]
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True)
        case.id = "case-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return case


def _unique(case: EvaluationCase, seen: set[str]) -> None:
    if case.id in seen:
        raise ValueError("duplicate case id")
    seen.add(case.id)


def load_dataset(
    path: str | Path, task: str = "retrieval", limit: int | None = None
) -> list[EvaluationCase]:
    """Read UTF-8 JSONL; skip blank lines; raise ValueError on invalid data.

    Missing IDs are SHA-256 hashes of normalized evaluation fields (excluding
    note), stable across file paths, line numbers and JSON key ordering. Duplicate
    explicit or generated IDs are rejected. Unknown legacy metadata is ignored.
    File/encoding errors propagate. ``limit`` is applied AFTER full validation.
    """
    _task(task)
    if limit is not None:
        _positive_int(limit, "limit")
    cases, seen = [], set()
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                case = _case(json.loads(line), task)
                _unique(case, seen)
            except ValueError as exc:
                # JSONDecodeError can contain input; never include the raw record.
                message = (
                    "invalid JSON"
                    if isinstance(exc, json.JSONDecodeError)
                    else str(exc)
                )
                raise ValueError(f"line {line_number}: {message}") from None
            cases.append(case)
    return cases if limit is None else cases[:limit]


def _stage_metrics(metrics: dict[str, float], timings: dict) -> None:
    for name, value in timings.items():
        if (
            isinstance(name, str)
            and _STAGE_TIME.fullmatch(name)
            and name != "latency_ms"
            and type(value) in (int, float)
            and math.isfinite(value)
            and value >= 0
        ):
            metrics[name] = float(value)


def _retrieval(case: EvaluationCase, k: int, include_content: bool):
    from corpus.sigla import extract, ordinal_range
    from library import search

    sigla = [ordinal_range(r) for match in extract(case.q) for r in match.refs] or None
    search.LAST_TIMINGS.clear()
    hits = search.retrieve(
        case.q,
        k=k,
        access=["open"],
        authors=case.authors or None,
        sigla=sigla,
        user_id=None,
        personal_only=False,
    )
    ids = [f"{hit.document_id}:{hit.order}" for hit in hits]
    relevant = set(case.relevant)
    found = [index for index, value in enumerate(ids) if value in relevant]
    pairs = {tuple(map(int, value.split(":"))) for value in relevant}
    near = any(
        (hit.document_id, order) in pairs
        for hit in hits
        for order in (hit.order - 1, hit.order, hit.order + 1)
    )
    metrics = {
        "recall_at_k": len(set(ids) & relevant) / len(relevant),
        "near_at_k": float(near),
        "mrr_at_k": 1.0 / (found[0] + 1) if found else 0.0,
        "hit_at_1": float(bool(found) and found[0] == 0),
    }
    _stage_metrics(metrics, search.LAST_TIMINGS)
    output = {"retrieved_ids": ids}
    if include_content:
        output["content"] = [hit.text for hit in hits]
    return metrics, output


def _rag_metrics(case: EvaluationCase, result: dict) -> dict[str, float]:
    answer = result.get("answer", "")
    checked = result.get("quotes_checked", 0)
    verified = result.get("quotes_verified", 0)
    refused = result.get("refused", False)
    metrics = {
        "answer_nonempty": float(bool(answer.strip())),
        "quotes_checked": float(checked),
        "quotes_verified": float(verified),
        "quotes_altered": float(len(result.get("quotes_altered", []))),
        "quotes_unverified": float(len(result.get("quotes_unverified", []))),
        "refused": float(refused),
    }
    if checked > 0:
        metrics["quote_verification_rate"] = verified / checked
    # Each expanded reference counts once per marker occurrence. A malformed
    # marker counts as ONE invalid check, with no credit for partially parsed refs.
    valid, invalid = 0, 0
    for check in result.get("citation_checks", []):
        refs = check.get("refs", [])
        if check.get("reason", "") not in ("", "missing_source") or not refs:
            invalid += 1
        elif check.get("status") == "ok":
            valid += len(refs)
        else:
            missing = set(check.get("missing", []))
            if missing:
                invalid += sum(ref in missing for ref in refs)
                valid += sum(ref not in missing for ref in refs)
            else:
                invalid += len(refs)
    if valid + invalid:
        metrics.update(
            {
                "citation_checked_count": float(valid + invalid),
                "citation_valid_count": float(valid),
                "citation_invalid_count": float(invalid),
                "citation_validity_rate": valid / (valid + invalid),
            }
        )
    if case.expected_refused is not None:
        metrics["refusal_accuracy"] = float(refused == case.expected_refused)
    if case.required_terms:
        folded = answer.casefold()
        metrics["required_terms_coverage"] = sum(
            term.casefold() in folded for term in case.required_terms
        ) / len(case.required_terms)
    usage = result.get("usage", {})
    for name in ("prompt_tokens", "completion_tokens"):
        value = usage.get(name)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            metrics[name] = float(value)
    _stage_metrics(metrics, result.get("timings", {}))
    return metrics


def _rag(case: EvaluationCase, mode: str, include_content: bool = False):
    from rag import service

    # pełne teksty fragmentów tylko na żądanie (sędziowie LLM w MLflow ich potrzebują)
    extra = {"include_chunk_text": True} if include_content else {}
    sources, result = {}, None
    for event, data in service.ask(
        case.q,
        authors=case.authors or None,
        works=case.works,
        mode=case.mode or mode,
        access=["open"],
        user_id=None,
        personal_only=False,
        include_ane=case.include_ane,
        include_patristics=case.include_patristics,
        **extra,
    ):
        if event == "error":
            raise RuntimeError("RAG failed")
        if event == "sources":
            sources = data
        elif event == "done":
            if result is not None or not isinstance(data, dict):
                raise RuntimeError("Invalid RAG completion")
            result = data
    if result is None:
        raise RuntimeError("Missing RAG completion")
    return _rag_metrics(case, result), {"result": result, "sources": sources}


def evaluate_cases(
    cases: Iterable[EvaluationCase],
    task: str,
    k: int = 5,
    mode: str = "scientific",
    *,
    include_content: bool = False,
) -> list[EvaluationResult]:
    """Evaluate sequentially, returning one result per case in input order.

    Validate arguments and ALL cases first. Backend/stream failures produce only
    success=0, measured latency, output={} and error='evaluation_failed'; continue
    with subsequent cases. No raw errors, deltas or partial sources are retained.
    Latency is wall-clock monotonic time around each full evaluation. Success=1
    means pipeline completion, not correctness/non-refusal/nonempty answer.
    ``include_content`` adds retrieval texts; RAG always returns result/sources,
    and with ``include_content`` its chunk sources also carry full ``text``.
    """
    _task(task)
    _positive_int(k, "k")
    if mode not in _MODES:
        raise ValueError("mode must be scientific or popular")
    if type(include_content) is not bool:
        raise ValueError("include_content must be a boolean")
    validated, seen = [], set()
    for case in cases:
        if not isinstance(case, EvaluationCase):
            raise ValueError("cases must contain EvaluationCase instances")
        case = _case(asdict(case), task)
        _unique(case, seen)
        validated.append(case)
    results = []
    for case in validated:
        start = time.monotonic()
        try:
            metrics, output = (
                _retrieval(case, k, include_content)
                if task == "retrieval"
                else _rag(case, mode, include_content)
            )
            metrics["success"] = 1.0
            error = None
        except Exception:
            metrics, output, error = {"success": 0.0}, {}, "evaluation_failed"
        metrics["latency_ms"] = (time.monotonic() - start) * 1000
        results.append(
            EvaluationResult(
                id=case.id,
                type=case.type,
                question=case.q,
                metrics=metrics,
                output=output,
                error=error,
            )
        )
    return results


def _summary(results: list[EvaluationResult]) -> AggregateSummary:
    values: dict[str, list[float]] = {}
    for result in results:
        for name, value in result["metrics"].items():
            values.setdefault(name, []).append(value)
    return AggregateSummary(
        count=len(results),
        success_count=sum(
            result["metrics"].get("success") == 1.0 for result in results
        ),
        error_count=sum(result["error"] is not None for result in results),
        metrics={name: sum(samples) / len(samples) for name, samples in values.items()},
        metric_counts={name: len(samples) for name, samples in values.items()},
    )


def aggregate_results(results: Iterable[EvaluationResult]) -> EvaluationAggregate:
    """Return {'all': summary, 'by_type': {raw_type: summary}}.

    Each summary contains count, success_count, error_count, metrics (per-case
    macro means) and metric_counts (applicable sample counts). Missing metrics
    never become zeros: failed cases contribute only success/latency. Overall
    means are over cases, not over group means. Raw type labels are nested keys,
    NOT MLflow metric paths; the command must escape/slug them when flattening.
    """
    rows = list(results)
    groups: dict[str, list[EvaluationResult]] = {}
    for result in rows:
        groups.setdefault(result["type"], []).append(result)
    return EvaluationAggregate(
        all=_summary(rows),
        by_type={kind: _summary(group) for kind, group in groups.items()},
    )
