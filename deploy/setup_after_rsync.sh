#!/usr/bin/env bash
# Na serwerze, jako root, po `./deploy/deploy.sh --data --replace-db`: venv, indeksy, start.
#   bash /opt/scriptura/deploy/setup_after_rsync.sh [--eval]
set -euo pipefail
INSTALL_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --eval) INSTALL_ARGS=(--eval) ;;
    *) echo "Usage: $0 [--eval]" >&2; exit 2 ;;
  esac
done
cd /opt/scriptura
for unit in scriptura-update.timer scriptura-update.service scriptura-web.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then systemctl stop "$unit"; fi
done
# rsync przynosi pliki z właścicielem z komputera deweloperskiego — baza musi należeć do usługi,
# inaczej „attempt to write a readonly database” (historia rozmów, uploady, zmiany w adminie)
chown -R scriptura:scriptura /opt/scriptura /cytrus/scriptura
# Nie usuwamy WAL istniejącej bazy: może zawierać zatwierdzone transakcje.
[ -d .venv ] || sudo -u scriptura python3 -m venv .venv
sudo -u scriptura .venv/bin/pip install -q --upgrade pip
sudo -u scriptura bash deploy/install_dependencies.sh "${INSTALL_ARGS[@]}"
sudo -u scriptura .venv/bin/python manage.py migrate --noinput
sudo -u scriptura .venv/bin/python manage.py collectstatic --noinput | tail -1
sudo -u scriptura .venv/bin/python manage.py index_search --recreate
if [ -f /cytrus/scriptura/data/vectors.jsonl.gz ]; then
  sudo -u scriptura .venv/bin/python manage.py reindex_chunks --recreate --vectors-file /cytrus/scriptura/data/vectors.jsonl.gz
  sudo -u scriptura .venv/bin/python manage.py index_ane --recreate --vectors-file /cytrus/scriptura/data/vectors.jsonl.gz
  sudo -u scriptura .venv/bin/python manage.py index_patristics --recreate --vectors-file /cytrus/scriptura/data/vectors.jsonl.gz
else
  echo "brak vectors.jsonl.gz — reindex_chunks policzy embeddingi lokalnie (wolno)"
  sudo -u scriptura .venv/bin/python manage.py reindex_chunks --recreate
fi
systemctl restart scriptura-web
if systemctl is-enabled --quiet scriptura-update.timer; then
  systemctl start scriptura-update.timer
fi
sleep 6
curl -sI http://127.0.0.1:8534/ | head -1
curl -sI http://127.0.0.1:40021/ | head -1
