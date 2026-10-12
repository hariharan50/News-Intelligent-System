#!/bin/sh
# Daily PostgreSQL backups, run by the "backup" service in docker-compose.prod.yml.
#
# Every 10 minutes: if today's backup doesn't exist yet and it's past BACKUP_HOUR (UTC),
# write /backups/nis-YYYY-MM-DD.dump (pg_dump custom format: compressed, restorable with
# pg_restore), then delete backups older than BACKUP_KEEP_DAYS.
#
# Copy /backups off the server too (another disk, S3, a laptop): a backup on the same
# machine doesn't survive losing the machine.
set -eu

: "${BACKUP_HOUR:=21}"        # 21:00 UTC = 02:30 IST, after the evening news has settled
: "${BACKUP_KEEP_DAYS:=14}"
export PGHOST="${PGHOST:-postgres}" PGUSER PGPASSWORD PGDATABASE

mkdir -p /backups
echo "backup: daily at ${BACKUP_HOUR}:00 UTC, keeping ${BACKUP_KEEP_DAYS} days in /backups"

while true; do
  today=$(date -u +%Y-%m-%d)
  hour=$(date -u +%H)
  target="/backups/nis-${today}.dump"
  if [ ! -f "$target" ] && [ "$hour" -ge "$BACKUP_HOUR" ]; then
    echo "backup: writing $target"
    if pg_dump --format=custom --no-owner --file="${target}.partial" "$PGDATABASE"; then
      mv "${target}.partial" "$target"
      echo "backup: done ($(du -h "$target" | cut -f1))"
      find /backups -name 'nis-*.dump' -mtime +"$BACKUP_KEEP_DAYS" -print -delete
    else
      echo "backup: FAILED" >&2
      rm -f "${target}.partial"
    fi
  fi
  sleep 600
done
