#!/usr/bin/env bash
# Spójna kopia SQLite (działa przy WAL i przy trwających zapisach) do /backup/DB; zostawia 14 ostatnich.
#   cron (root): 30 2 * * * /opt/scriptura/deploy/backup-db.sh
set -euo pipefail
DB=/cytrus/scriptura/db.sqlite3
OUT=/backup/DB/scriptura-$(date +%F).sqlite
mkdir -p /backup/DB
sudo -u scriptura sqlite3 "$DB" ".backup '$OUT'"   # jako właściciel bazy — root zostawiłby pliki -wal/-shm nie do zapisu dla usługi
gzip -f "$OUT"
ls -1t /backup/DB/scriptura-*.sqlite.gz | tail -n +15 | xargs -r rm -f
