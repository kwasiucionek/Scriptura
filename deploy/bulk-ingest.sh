#!/usr/bin/env bash
# Duża ingestia (np. całe czasopisma) w trzech etapach; embeddingi liczy GPU laptopa przez tunel SSH.
#
#   sudo -u scriptura deploy/bulk-ingest.sh ingest data/manifests/journal-*.jsonl   # PDF -> chunki w bazie, bez indeksu
#   sudo -u scriptura deploy/bulk-ingest.sh translate                               # przekład chunków nie-pl (model w chmurze)
#   sudo -u scriptura deploy/bulk-ingest.sh embed                                   # wektory na GPU laptopa + indeks
#
# Etapy 1–2 nie potrzebują laptopa. Przed etapem 3 na laptopie:
#   ollama pull snowflake-arctic-embed2            # ten sam model co na serwerze (OLLAMA_EMBED_MODEL)
#   ssh -p 10233 -N -R 127.0.0.1:11435:127.0.0.1:11434 root@roman233.mikrus.xyz
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
    $PY manage.py translate_chunks --workers "${TRANSLATE_WORKERS:-4}"
    ;;
  embed)
    curl -fsS -m 10 "$TUNNEL/api/tags" >/dev/null || {
      echo "brak tunelu do Ollamy na laptopie ($TUNNEL) — patrz nagłówek skryptu"; exit 1; }
    echo "$(date +%T) zgodność wektorów laptopa z indeksem (ten sam model i kwantyzacja?)"
    EMBEDDINGS_BASE_URL="$TUNNEL" $PY manage.py shell -c "
import math, sys
from library.embeddings import embed
from library.models import Chunk
from library.search import fetch_vectors
from corpus.search.client import index_name
chunks = list(Chunk.objects.select_related('document').order_by('id')[:6])
have = fetch_vectors(index_name('chunks'), sorted({c.document_id for c in chunks}))
pairs = [(c, have[c.os_id]) for c in chunks if c.os_id in have]
new = embed([f'{c.document.title}\n{c.section}\n{c.text_pl or c.text}' for c, _ in pairs])
cos = [sum(a * b for a, b in zip(u, v)) / math.sqrt(sum(a * a for a in u) * sum(b * b for b in v)) for (_, u), v in zip(pairs, new)]
print('cosinus min', round(min(cos), 5), 'z', len(cos))
sys.exit(0 if cos and min(cos) > 0.95 else 3)  # inny model ≈ 0; różnice sprzętu/kwantyzacji ≈ 0,98
" || { echo "wektory z laptopa nie zgadzają się z indeksem — sprawdź model (ollama show snowflake-arctic-embed2)"; exit 3; }
    echo "$(date +%T) wektory przez $TUNNEL i indeks (tylko dokumenty niepełne w indeksie)"
    EMBEDDINGS_BASE_URL="$TUNNEL" EMBEDDINGS_BATCH="${EMBEDDINGS_BATCH_GPU:-64}" \
      $PY manage.py reindex_chunks --missing 2>&1 | grep -v -E "^(HEAD|POST|GET|PUT) http"
    ;;
  *)
    sed -n '2,12p' "$0"; exit 2 ;;
esac
echo "$(date +%T) KONIEC $stage"
