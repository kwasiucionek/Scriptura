"""Opcjonalni sędziowie LLM (RAGAS, DeepEval) jako scorery MLflow w ewaluacji RAG.

Import modułu nie importuje MLflow, RAGAS ani DeepEval — robi to dopiero
``build_judges``, więc brak ``.[judges]`` kończy komendę przed generowaniem odpowiedzi.

Specyfikacja sędziów: lista ``framework:Metryka`` po przecinku, np.
``ragas:Faithfulness,deepeval:AnswerRelevancy``; ``default`` = ``DEFAULT_JUDGES``.
Dozwolone są tylko metryki bez odpowiedzi wzorcowej i bez embeddingów OpenAI, więc
każde wywołanie idzie do skonfigurowanego modelu sędziego (URI MLflow
``<dostawca>:/<model>``, np. ``ollama:/deepseek-v4-pro:cloud`` przez lokalną Ollamę).

Oceniana treść (pytanie, odpowiedź, pobrane fragmenty) trafia do modelu sędziego
i do śladów w MLflow — komenda wymaga więc ``--log-content``.
"""

import os

DEFAULT_JUDGE_MODEL = "ollama:/deepseek-v4-pro:cloud"
DEFAULT_JUDGES = (
    ("ragas", "Faithfulness"),
    ("deepeval", "Faithfulness"),
    ("deepeval", "AnswerRelevancy"),
)
# metryki bez odpowiedzi wzorcowej i bez embeddingów (żadnych domyślnych wywołań OpenAI)
SUPPORTED = {
    "ragas": (
        "Faithfulness",
        "ResponseGroundedness",
        "ContextRelevance",
        "ContextUtilization",
    ),
    "deepeval": ("Faithfulness", "AnswerRelevancy", "ContextualRelevancy"),
}
# telemetria bibliotek wyłączona, chyba że użytkownik ustawił inaczej
_TELEMETRY_OFF = {
    "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
    "DEEPEVAL_GRPC_LOGGING": "NO",
    "RAGAS_DO_NOT_TRACK": "true",
    "MLFLOW_ENABLE_TELEMETRY": "false",
}


def parse_judges(spec: str) -> list[tuple[str, str]]:
    """``"default,ragas:ContextRelevance"`` -> unikalne pary (framework, metryka)."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("Pusta lista sędziów")
    available = ", ".join(
        f"{f}:{m}" for f, metrics in SUPPORTED.items() for m in metrics
    )
    items: list[tuple[str, str]] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if raw.casefold() == "default":
            items.extend(DEFAULT_JUDGES)
            continue
        framework, sep, metric = raw.partition(":")
        framework, metric = framework.strip().casefold(), metric.strip()
        if not sep or metric not in SUPPORTED.get(framework, ()):
            raise ValueError(f"Nieobsługiwany sędzia {raw!r}; dostępne: {available}")
        items.append((framework, metric))
    unique = list(dict.fromkeys(items))
    if not unique:
        raise ValueError("Pusta lista sędziów")
    return unique


def judge_name(framework: str, metric: str) -> str:
    """Nazwa oceny w MLflow; rozdziela metryki o tej samej nazwie z RAGAS i DeepEval."""
    return f"{framework}/{metric}"


def validate_model(model: str) -> None:
    provider, sep, name = (model or "").partition(":/")
    if not provider or not sep or not name.strip("/"):
        raise ValueError(
            "Model sędziego w formacie <dostawca>:/<model>, np. ollama:/deepseek-v4-pro:cloud"
        )


def build_judges(items: list[tuple[str, str]], model: str) -> list:
    """Scorery MLflow dla podanych sędziów; RuntimeError, gdy brak pakietów.

    Każdy scorer zewnętrzny jest opakowany: ocena dostaje nazwę ``framework/Metryka``,
    a przypadki z ``judge=False`` (błąd lub pusta odpowiedź RAG) są pomijane zamiast
    oceniania pustej odpowiedzi. Sędzia widzi tylko pytanie, odpowiedź i fragmenty
    ze spanu RETRIEVER — bez identyfikatora przypadku.
    """
    validate_model(model)
    for key, value in _TELEMETRY_OFF.items():
        os.environ.setdefault(key, value)
    try:
        from mlflow.genai.scorers import scorer
        from mlflow.genai.scorers.deepeval import get_scorer as deepeval_scorer
        from mlflow.genai.scorers.ragas import get_scorer as ragas_scorer
    except ImportError:
        raise RuntimeError(
            "Sędziowie wymagają RAGAS i DeepEval: pip install -e '.[eval,judges]'"
        ) from None

    factories = {"ragas": ragas_scorer, "deepeval": deepeval_scorer}
    return [
        _named(
            scorer,
            factories[framework](metric, model=model),
            judge_name(framework, metric),
        )
        for framework, metric in items
    ]


def _named(scorer, inner, name: str):
    @scorer(name=name)
    def judge(inputs, outputs, trace):
        if not inputs.get("judge", True):
            return None
        feedback = inner(inputs=inputs.get("question"), outputs=outputs, trace=trace)
        feedback.name = name
        return feedback

    return judge
