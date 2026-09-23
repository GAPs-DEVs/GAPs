#!/bin/bash

# Stop if downloading, decompression, or SQL execution fails.
set -euo pipefail
umask 077

# Usage: bash db/scripts/restore_db_from_s3.sh
# Select a numbered S3 backup, then confirm replacement of the existing database.
# Restore the gzip-compressed SQL produced by backup_db_to_s3.sh.
# This script does not support .dump files created with pg_dump -Fc.
if [ "$#" -ne 0 ]; then
  echo "Usage: bash $0" >&2
  exit 64
fi

PROJECT_DIR="$HOME/Guitar_Practice_Support_SW"
BACKUP_DIR="$HOME/backups/guitar-practice"
S3_BUCKET="appserver-db-backup"
S3_PREFIX="guitar-practice/"
RESTORE_DB="guitar_practice"

# Stop if listing fails; distinguish a failed request from an empty backup list.
# Read the entire key after the date, time, and size into the final variable to
# preserve spaces in file names. Only show .sql.gz files used by our backup script.
echo 'Listing S3 backups...'
S3_LIST=$(aws s3 ls "s3://$S3_BUCKET/$S3_PREFIX" --recursive)
BACKUP_KEYS=()
while read -r backup_date backup_time backup_size backup_key; do
  case "$backup_key" in
    *.sql.gz)
      BACKUP_KEYS+=("$backup_key")
      printf '%d) %s %s | %s bytes | %s\n' \
        "${#BACKUP_KEYS[@]}" "$backup_date" "$backup_time" "$backup_size" "$backup_key"
      ;;
  esac
done <<< "$S3_LIST"

if [ "${#BACKUP_KEYS[@]}" -eq 0 ]; then
  echo 'No .sql.gz backups available to restore.'
  exit 0
fi

# Compare input directly with displayed numbers to avoid numeric conversion errors
# or invalid array access. Retry invalid input; 0 or EOF (Ctrl+D) exits unchanged.
S3_FILE=""
while [ -z "$S3_FILE" ]; do
  read -r -p 'Select a backup number (0: cancel): ' choice || exit 0
  [ "$choice" != "0" ] || exit 0
  for index in "${!BACKUP_KEYS[@]}"; do
    if [ "$choice" = "$((index + 1))" ]; then
      S3_FILE="s3://$S3_BUCKET/${BACKUP_KEYS[$index]}"
      break
    fi
  done
  if [ -z "$S3_FILE" ]; then
    echo 'Please enter a number shown in the list.'
  fi
done
printf 'Selected backup: %s\n' "$S3_FILE"

cd "$PROJECT_DIR"
mkdir -p "$BACKUP_DIR"
# Check the permissions file before deletion to avoid a missing-file failure later.
test -r db/grant_api_role.sql.example

# Use temporary files to avoid overwriting existing backups.
# Remove only these temporary files on exit; retain the original S3 backup.
DOWNLOAD_FILE=$(mktemp "$BACKUP_DIR/restore.XXXXXX.sql.gz")
SQL_FILE="${DOWNLOAD_FILE%.gz}"
trap 'rm -f -- "$DOWNLOAD_FILE" "$SQL_FILE"' EXIT
aws s3 cp "$S3_FILE" "$DOWNLOAD_FILE"
gzip -t "$DOWNLOAD_FILE"
# Fully decompress before deleting the database; stop here on disk or gzip errors.
# Feed psql a complete file to avoid partial restoration if a decompression pipe fails.
gzip -dc "$DOWNLOAD_FILE" > "$SQL_FILE"
test -s "$SQL_FILE"

echo "Warning: $RESTORE_DB will be deleted and recreated from the selected backup."
echo 'Stop the API and applications that write to the database until restoration completes.'
read -r -p "Type $RESTORE_DB to continue (anything else: cancel): " confirmation || exit 0
if [ "$confirmation" != "$RESTORE_DB" ]; then
  echo 'Restore cancelled.'
  exit 0
fi

# Back up the current state locally before deletion. Do not delete if backup fails.
# Retain this file for recovery if restoration fails; it is not uploaded to S3.
SAFETY_FILE=$(mktemp "$BACKUP_DIR/before-restore.XXXXXX.sql.gz")
docker compose -f db/docker-compose.yml exec -T postgres \
  pg_dump -U guitar_migrator -d "$RESTORE_DB" | gzip > "$SAFETY_FILE"
gzip -t "$SAFETY_FILE"
printf 'Pre-restore backup: %s\n' "$SAFETY_FILE"

# Keep the container and replace only the database under the same name.
# Let dropdb fail on active client connections instead of terminating them forcibly.
# Database deletion and creation are separate from the SQL restore transaction below
# and are not automatically rolled back.
docker compose -f db/docker-compose.yml exec -T postgres \
  dropdb -U guitar_migrator --maintenance-db=postgres "$RESTORE_DB"
docker compose -f db/docker-compose.yml exec -T postgres \
  createdb -U guitar_migrator -T template0 "$RESTORE_DB"

# Roll back the restore transaction on SQL errors.
# Failure does not automatically recover previous data; retain the pre-restore backup.
# Original roles must exist in the container to restore ownership and permissions.
docker compose -f db/docker-compose.yml exec -T postgres \
  psql -X -U guitar_migrator -d "$RESTORE_DB" \
  --single-transaction -v ON_ERROR_STOP=1 -f - < "$SQL_FILE"

# Reapply database CONNECT permissions, which are not included in a default pg_dump.
docker compose -f db/docker-compose.yml exec -T postgres \
  psql -X -U guitar_migrator -d "$RESTORE_DB" \
  --single-transaction -v ON_ERROR_STOP=1 -f - < db/grant_api_role.sql.example

echo "Restore completed: $RESTORE_DB"

