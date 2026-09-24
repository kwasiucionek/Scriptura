#!/usr/bin/env bash
# Na serwerze, jako root, po `./deploy/deploy.sh --data` i rsync bazy: venv, zależności, indeksy, start.
#   bash /opt/scriptura/deploy/setup_after_rsync.sh
set -euo pipefail
cd /opt/scriptura
# rsync przynosi pliki z właścicielem z komputera deweloperskiego — baza musi należeć do usługi,
# inaczej „attempt to write a readonly database” (historia rozmów, uploady, zmiany w adminie)
chown -R scriptura:scriptura /opt/scriptura /cytrus/scriptura
rm -f /cytrus/scriptura/db.sqlite3-wal /cytrus/scriptura/db.sqlite3-shm   # stare pliki WAL od poprzedniej kopii
[ -d .venv ] || sudo -u scriptura python3 -m venv .venv
sudo -u scriptura .venv/bin/pip install -q --upgrade pip
sudo -u scriptura .venv/bin/pip install -q -e ".[prod]"
sudo -u scriptura .venv/bin/python manage.py migrate --noinput
sudo -u scriptura .venv/bin/python manage.py collectstatic --noinput | tail -1
sudo -u scriptura .venv/bin/python manage.py index_search --recreate
if [ -f /cytrus/scriptura/data/vectors.jsonl.gz ]; then
  sudo -u scriptura .venv/bin/python manage.py reindex_chunks --recreate --vectors-file /cytrus/scriptura/data/vectors.jsonl.gz
  sudo -u scriptura .venv/bin/python manage.py index_ane --recreate --vectors-file /cytrus/scriptura/data/vectors.jsonl.gz || true
  sudo -u scriptura .venv/bin/python manage.py index_patristics --recreate --vectors-file /cytrus/scriptura/data/vectors.jsonl.gz || true
else
  echo "brak vectors.jsonl.gz — reindex_chunks policzy embeddingi lokalnie (wolno)"
  sudo -u scriptura .venv/bin/python manage.py reindex_chunks --recreate
fi
systemctl restart scriptura-web
sleep 6
curl -sI http://127.0.0.1:8534/ | head -1
curl -sI http://127.0.0.1:40021/ | head -1
