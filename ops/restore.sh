#!/bin/sh
# Restore the database from a backup made by ops/backup.sh.
#
#   ./ops/restore.sh backups/nis-2026-10-11.dump
#
# Stops everything that writes to the database, replaces its contents with the backup,
# runs migrations (in case the backup is from an older version) and starts things again.
# Everything written since the backup is lost, so take a fresh backup first if you can.
set -eu

DUMP="${1:?usage: ./ops/restore.sh backups/nis-YYYY-MM-DD.dump}"
[ -f "$DUMP" ] || { echo "no such file: $DUMP" >&2; exit 1; }
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

printf 'This REPLACES the database with %s. Type "restore" to continue: ' "$DUMP"
read -r answer
[ "$answer" = "restore" ] || { echo "cancelled"; exit 1; }

$COMPOSE stop api worker scheduler telegram web
$COMPOSE exec -T postgres sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$DUMP"
$COMPOSE run --rm migrate
$COMPOSE up -d
echo "restored from $DUMP"
