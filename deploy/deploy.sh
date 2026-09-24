#!/usr/bin/env bash
# Wdrożenie Scriptury na steve141.
#   ./deploy/deploy.sh            # kod: git push -> git pull na serwerze, pip, migrate, collectstatic, restart
#   ./deploy/deploy.sh --data     # + dane: PDF-y, blog, youtube, manifesty, wektory, db.sqlite3 (usługa zatrzymana na czas transferu)
# Wymaga: repo na GitHubie (origin), na serwerze klon w /opt/scriptura z kluczem deploy (read-only).
set -euo pipefail
HOST="root@steve141.mikrus.xyz"; PORT=10141
APP=/opt/scriptura; DATA=/cytrus/scriptura/data
SSH="ssh -p $PORT"
BRANCH=${BRANCH:-$(git rev-parse --abbrev-ref HEAD)}

if [[ -n "$(git status --porcelain)" ]]; then
  echo "!! niezacommitowane zmiany — commit albo stash, potem deploy"; exit 1
fi
git push origin "$BRANCH"

if [[ "${1:-}" == "--data" ]]; then
  $SSH "$HOST" systemctl stop scriptura-web || true
  python manage.py export_vectors --out data/vectors.jsonl.gz
  rsync -av -e "$SSH" data/library/ "$HOST:$DATA/library/"
  rsync -av -e "$SSH" data/manifests/ "$HOST:$DATA/manifests/"
  rsync -av -e "$SSH" data/vectors.jsonl.gz data/authors.jsonl "$HOST:$DATA/" 2>/dev/null || true
  rsync -av -e "$SSH" db.sqlite3 "$HOST:/cytrus/scriptura/db.sqlite3"
  $SSH "$HOST" "chown -R scriptura:scriptura /cytrus/scriptura; rm -f /cytrus/scriptura/db.sqlite3-wal /cytrus/scriptura/db.sqlite3-shm"
fi

$SSH "$HOST" bash -s <<REMOTE
set -e
cd $APP
sudo -u scriptura git fetch origin && sudo -u scriptura git checkout -q $BRANCH && sudo -u scriptura git reset -q --hard origin/$BRANCH
echo "kod: \$(git log -1 --format='%h %s')"
[ -d .venv ] || { echo "brak venv — uruchom: bash $APP/deploy/setup_after_rsync.sh"; exit 0; }
sudo -u scriptura .venv/bin/pip install -q -e ".[prod,harvest]"
sudo -u scriptura .venv/bin/python manage.py migrate --noinput
sudo -u scriptura .venv/bin/python manage.py collectstatic --noinput | tail -1
REMOTE

if [[ "${1:-}" == "--data" ]]; then
  echo ">>> dane przeniesione; na serwerze: bash $APP/deploy/setup_after_rsync.sh (indeksy z wektorów + start usługi)"
else
  $SSH "$HOST" "systemctl restart scriptura-web; sleep 3; systemctl --no-pager --lines=3 status scriptura-web | tail -3"
fi
