#!/usr/bin/env bash
# Duża ingestia (np. całe czasopisma) w trzech etapach; embeddingi liczy GPU laptopa przez tunel SSH.
#
#   sudo -u scriptura deploy/bulk-ingest.sh ingest data/manifests/journal-*.jsonl   # PDF -> chunki w bazie, bez indeksu
#   sudo -u scriptura deploy/bulk-ingest.sh translate                               # przekład chunków nie-pl (model w chmurze)
#   sudo -u scriptura deploy/bulk-ingest.sh embed                                   # wektory na GPU laptopa + indeks
#
# Etapy 1–2 nie potrzebują laptopa. Przed etapem 3 na laptopie:
#   ollama pull snowflake-arctic-embed2            # ten sam model co na serwerze (OLLAMA_EMBED_MODEL)
#   ssh -p 10141 -N -R 127.0.0.1:11435:127.0.0.1:11434 root@steve141.mikrus.xyz
# Przerwany etap 3 wznawia się tym samym poleceniem (reindex_chunks --missing).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
TUNNEL="${EMBEDDINGS_TUNNEL:-http://127.0.0.1:11435}"
export PYTHONUNBUFFERED=1

stage="${1:-}"
shift || true
case "$stage" in
  ingest)
    [ $# -gt 0 ] || { echo "podaj manifesty"; exit 2; }
    for m in "$@"; do
      echo "$(date +%T) ingestia (bez indeksu): $m"
      $PY manage.py ingest_manifest "$m" --no-index
    done
    ;;
  translate)
    echo "$(date +%T) przekład chunków obcojęzycznych"
    $PY manage.py translate_chunks
    ;;
  embed)
    curl -fsS -m 10 "$TUNNEL/api/tags" >/dev/null || {
      echo "brak tunelu do Ollamy na laptopie ($TUNNEL) — patrz nagłówek skryptu"; exit 1; }
    echo "$(date +%T) wektory przez $TUNNEL i indeks (tylko dokumenty niepełne w indeksie)"
    EMBEDDINGS_BASE_URL="$TUNNEL" EMBEDDINGS_BATCH="${EMBEDDINGS_BATCH_GPU:-64}" \
      $PY manage.py reindex_chunks --missing 2>&1 | grep -v -E "^(HEAD|POST|GET|PUT) http"
    ;;
  *)
    sed -n '2,12p' "$0"; exit 2 ;;
esac
echo "$(date +%T) KONIEC $stage"
