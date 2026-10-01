#!/usr/bin/env bash
# Code: git push -> server checkout, migrate, collectstatic, restart.
# Data replacement: --data --replace-db (snapshot + remote backup; services stay stopped for reindex).
set -euo pipefail
HOST="root@steve141.mikrus.xyz"; PORT=10141
APP=/opt/scriptura; DATA=/cytrus/scriptura/data
SSH="ssh -p $PORT"
BRANCH=${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}
WITH_DATA=false; REPLACE_DB=false; SNAPSHOT_DIR=""; SERVICES_STOPPED=false
for arg in "$@"; do
  case "$arg" in
    --data) WITH_DATA=true ;;
    --replace-db) REPLACE_DB=true ;;
    *) echo "Usage: $0 [--data --replace-db]" >&2; exit 2 ;;
  esac
done
if [[ "$WITH_DATA" != "$REPLACE_DB" ]]; then
  echo "--data requires --replace-db: replacement also overwrites production accounts and history." >&2
  echo "For incremental source updates use manage.py update_corpus on the server." >&2
  exit 2
fi
cleanup() {
  if [[ -n "$SNAPSHOT_DIR" ]]; then rm -rf -- "$SNAPSHOT_DIR"; fi
  if [[ "$SERVICES_STOPPED" == true ]]; then
    echo "Data deployment: web/update services are stopped. After checking the transfer run:" >&2
    echo "bash $APP/deploy/setup_after_rsync.sh" >&2
  fi
}
trap cleanup EXIT

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Uncommitted changes: commit or stash before deploying." >&2; exit 1
fi
git push origin "$BRANCH"

if [[ "$WITH_DATA" == true ]]; then
  SNAPSHOT_DIR=$(mktemp -d)
  # Backup API reads committed WAL transactions, not just the main database file.
  .venv/bin/python deploy/sqlite_snapshot.py --from-settings --output "$SNAPSHOT_DIR/db.sqlite3"
  .venv/bin/python manage.py export_vectors --out data/vectors.jsonl.gz
  SERVICES_STOPPED=true
  $SSH "$HOST" bash -s <<'BACKUP'
set -euo pipefail
for unit in scriptura-update.timer scriptura-update.service scriptura-web.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then systemctl stop "$unit"; fi
done
install -d -m 700 -o scriptura -g scriptura /cytrus/scriptura/backups
if [[ -f /cytrus/scriptura/db.sqlite3 ]]; then
  backup="/cytrus/scriptura/backups/pre-deploy-$(date -u +%Y%m%dT%H%M%S)-$$.sqlite3"
  sudo -u scriptura python3 - /cytrus/scriptura/db.sqlite3 "$backup" <<'PY'
import os, sqlite3, sys
from contextlib import closing
from pathlib import Path
source, target = sys.argv[1:]
fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
with closing(sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True)) as origin:
    with closing(sqlite3.connect(target)) as destination:
        origin.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Invalid production backup; replacement aborted")
print("Production backup:", target)
PY
fi
BACKUP
  rsync -av -e "$SSH" data/library/ "$HOST:$DATA/library/"
  rsync -av -e "$SSH" data/manifests/ "$HOST:$DATA/manifests/"
  rsync -av -e "$SSH" data/vectors.jsonl.gz "$HOST:$DATA/"
  if [[ -f data/authors.jsonl ]]; then rsync -av -e "$SSH" data/authors.jsonl "$HOST:$DATA/"; fi
  rsync -av -e "$SSH" "$SNAPSHOT_DIR/db.sqlite3" "$HOST:/cytrus/scriptura/db.sqlite3.incoming"
  $SSH "$HOST" bash -s <<'REPLACE'
set -euo pipefail
chown scriptura:scriptura /cytrus/scriptura/db.sqlite3.incoming
chmod 600 /cytrus/scriptura/db.sqlite3.incoming
sudo -u scriptura python3 - <<'PY'
import os, sqlite3
from contextlib import closing
from pathlib import Path
path = Path("/cytrus/scriptura/db.sqlite3")
if path.exists():
    # Checkpoint BEFORE replacement: failure or interruption leaves the old DB intact.
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=20)) as db:
        busy, _, _ = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RuntimeError("Production database still busy; replacement aborted")
# A remaining sidecar indicates another connection or failed SQLite cleanup.
# Never delete it blindly, and never pair an incoming DB with an old WAL.
if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm")):
    raise RuntimeError("SQLite sidecars remain; stop other database connections first")
os.replace(str(path) + ".incoming", path)
PY
chown -R scriptura:scriptura /cytrus/scriptura/data
REPLACE
fi

$SSH "$HOST" bash -s -- "$BRANCH" <<'REMOTE'
set -euo pipefail
cd /opt/scriptura
branch=$1
sudo -u scriptura git fetch origin
sudo -u scriptura git checkout -q "$branch"
sudo -u scriptura git reset -q --hard "origin/$branch"
echo "Code: $(git --no-pager log -1 --format='%h %s')"
[ -d .venv ] || { echo "Missing venv: run deploy/setup_after_rsync.sh"; exit 1; }
sudo -u scriptura .venv/bin/pip install -q -e ".[prod,harvest]"
sudo -u scriptura .venv/bin/python manage.py migrate --noinput
sudo -u scriptura .venv/bin/python manage.py collectstatic --noinput
REMOTE

if [[ "$WITH_DATA" == false ]]; then
  $SSH "$HOST" "systemctl restart scriptura-web; sleep 3; systemctl --no-pager --lines=3 status scriptura-web"
fi
