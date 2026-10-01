#!/usr/bin/env bash
# Consistent SQLite backup (including WAL); retain 14 latest backups in an app-owned directory.
# cron (root): 30 2 * * * /opt/scriptura/deploy/backup-db.sh
set -euo pipefail
DB=/cytrus/scriptura/db.sqlite3
DIR=/backup/DB/scriptura
OUT="$DIR/scriptura-$(date -u +%Y%m%dT%H%M%S)-$$.sqlite"
install -d -m 700 -o scriptura -g scriptura "$DIR"
sudo -u scriptura /opt/scriptura/.venv/bin/python /opt/scriptura/deploy/sqlite_snapshot.py --source "$DB" --output "$OUT"
gzip "$OUT"
ls -1t "$DIR"/scriptura-*.sqlite.gz | tail -n +15 | xargs -r rm -f
