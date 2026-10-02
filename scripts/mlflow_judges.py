"""Sędziowie LLM Scriptury zarejestrowani w eksperymencie MLflow (strona „Judges” w UI).

Sędziowie z make_judge (instrukcje + model) mogą działać po stronie serwera MLflow:
uruchamiasz je z UI na wybranych śladach albo włączasz automatyczną ocenę nowych śladów.
Scorery RAGAS/DeepEval z eval_mlflow tak nie działają — serwer odrzuca scorery z kodem
(@scorer), więc tamte zostają w komendzie, a tu są kryteria specyficzne dla Scriptury.

Każdy sędzia ocenia jedno kryterium i zwraca yes/no (agregowalne w MLflow). Ślady
z eval_mlflow --judges mają w inputs pytanie i typ, w outputs — odpowiedź.

Użycie (venv z mlflow, tracking na serwerze):
  python scripts/mlflow_judges.py register --experiment scriptura-evaluation
  python scripts/mlflow_judges.py run --experiment scriptura-evaluation --run-id <RUN_ID>
  python scripts/mlflow_judges.py list --experiment scriptura-evaluation

Model: --model albo SCRIPTURA_JUDGE_MODEL, domyślnie ollama:/deepseek-v4-pro:cloud
(serwer MLflow wywołuje go przez lokalną Ollamę na tym samym hoście).
"""

import argparse
import os
import sys
from typing import Literal

DEFAULT_MODEL = "ollama:/deepseek-v4-pro:cloud"

# źródła w odpowiedzi: [n] literatura naukowa, [P n] Ojcowie Kościoła, [A n] teksty ANE
_SOURCES = (
    "Odpowiedź może powoływać się na trzy rodzaje źródeł: literaturę naukową "
    "(odsyłacze [1], [2]…), tradycję patrystyczną — Ojców Kościoła ([P1], [P2]…) "
    "oraz teksty porównawcze starożytnego Bliskiego Wschodu ([A1], [A2]…)."
)

JUDGES = {
    "tradycja_vs_egzegeza": (
        "Czy odpowiedź odróżnia interpretację Ojców Kościoła i teksty starożytnego "
        "Bliskiego Wschodu od ustaleń współczesnej egzegezy i od tekstu biblijnego.",
        "Oceniasz odpowiedź asystenta teologii biblijnej.\n\n"
        "Dane wejściowe (pytanie): {{ inputs }}\n\n"
        "Odpowiedź: {{ outputs }}\n\n"
        f"{_SOURCES}\n\n"
        "Kryterium: interpretacje Ojców [P…] są przedstawione jako interpretacja tradycji, "
        "nie jako ustalenie współczesnej egzegezy; teksty [A…] jako materiał porównawczy, "
        "nie jako tekst biblijny ani wniosek naukowy. Jeśli odpowiedź nie powołuje się "
        "na [P…] ani [A…], kryterium jest spełnione.\n\n"
        "Odpowiedz yes, jeśli kryterium jest spełnione, albo no, jeśli nie. "
        "Uzasadnienie napisz po polsku, w 1–2 zdaniach, z cytatem fragmentu, "
        "który przesądził o ocenie.",
    ),
    "zrozumiala_dla_laika": (
        "Czy odpowiedź w trybie popularnym jest zrozumiała dla osoby bez wykształcenia "
        "teologicznego.",
        "Oceniasz odpowiedź asystenta teologii biblijnej w trybie popularnym, "
        "przeznaczonym dla osób bez wykształcenia teologicznego.\n\n"
        "Dane wejściowe (pytanie): {{ inputs }}\n\n"
        "Odpowiedź: {{ outputs }}\n\n"
        "Kryterium: główna odpowiedź na pytanie pada w pierwszych 2–3 zdaniach, "
        "a terminy hebrajskie, greckie i fachowe (np. glosa, Septuaginta, hapax) "
        "są objaśnione przy pierwszym użyciu albo wynikają jasno z kontekstu. "
        "Nie oceniaj poprawności merytorycznej — tylko przystępność.\n\n"
        "Odpowiedz yes, jeśli kryterium jest spełnione, albo no, jeśli nie. "
        "Uzasadnienie napisz po polsku, w 1–2 zdaniach, wskazując nieobjaśniony "
        "termin albo miejsce, w którym pada odpowiedź.",
    ),
}


def build(model: str) -> list:
    from mlflow.genai.judges import make_judge

    return [
        make_judge(
            name=name,
            description=description,
            instructions=instructions,
            model=model,
            feedback_value_type=Literal["yes", "no"],
            generate_rationale_first=True,
        )
        for name, (description, instructions) in JUDGES.items()
    ]


def experiment_id(mlflow, name: str) -> str:
    experiment = mlflow.get_experiment_by_name(name)
    if experiment is None:
        sys.exit(f"Brak eksperymentu {name!r}")
    return experiment.experiment_id


def cmd_register(args, mlflow) -> None:
    exp_id = experiment_id(mlflow, args.experiment)
    for judge in build(args.model):
        registered = judge.register(experiment_id=exp_id)
        print(f"{registered.name}: zarejestrowany ({args.model})")


def cmd_list(args, mlflow) -> None:
    from mlflow.genai.scorers import list_scorers

    for scorer in list_scorers(experiment_id=experiment_id(mlflow, args.experiment)):
        print(f"{scorer.name}: sample_rate={scorer.sample_rate}")


def cmd_run(args, mlflow) -> None:
    """Ocena istniejących śladów runu (np. z eval_mlflow --judges) bez generowania."""
    exp_id = experiment_id(mlflow, args.experiment)
    traces = mlflow.search_traces(locations=[exp_id], run_id=args.run_id)
    if traces.empty:
        sys.exit(f"Run {args.run_id} nie ma śladów")
    with mlflow.start_run(experiment_id=exp_id, run_name=args.run_name):
        mlflow.set_tag("scriptura.judged_run", args.run_id)
        result = mlflow.genai.evaluate(data=traces, scorers=build(args.model))
    for key, value in sorted(result.metrics.items()):
        print(f"{key}: {value:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("register", "list", "run"))
    parser.add_argument("--experiment", default="scriptura-evaluation")
    parser.add_argument(
        "--model", default=os.environ.get("SCRIPTURA_JUDGE_MODEL", DEFAULT_MODEL)
    )
    parser.add_argument("--run-id", help="run, którego ślady ocenić (komenda run)")
    parser.add_argument("--run-name", default="scriptura-judges")
    args = parser.parse_args()
    if args.command == "run" and not args.run_id:
        parser.error("run wymaga --run-id")

    os.environ.setdefault("MLFLOW_ENABLE_TELEMETRY", "false")
    os.environ.setdefault("MLFLOW_GENAI_EVAL_LLM_TIMEOUT", "300")
    import mlflow

    {"register": cmd_register, "list": cmd_list, "run": cmd_run}[args.command](
        args, mlflow
    )


if __name__ == "__main__":
    main()
