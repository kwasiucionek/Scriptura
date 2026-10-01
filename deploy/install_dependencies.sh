#!/usr/bin/env bash
# Shared dependency selection for code deploy and post-data setup.
set -euo pipefail
EXTRAS=prod,harvest
for arg in "$@"; do
  case "$arg" in
    --eval) EXTRAS=prod,harvest,eval ;;
    *) echo "Usage: $0 [--eval]" >&2; exit 2 ;;
  esac
done
cd "$(dirname "${BASH_SOURCE[0]}")/.."
.venv/bin/python -m pip install --no-cache-dir --disable-pip-version-check -e ".[$EXTRAS]"
.venv/bin/python -m pip check
if [[ "$EXTRAS" == prod,harvest,eval ]]; then
  MLFLOW_ENABLE_TELEMETRY=false .venv/bin/python -c 'from library.mlflow_eval import require_mlflow; print("MLflow ready:", require_mlflow().__version__)'
fi
