#!/bin/bash

set -euo pipefail

umask 077

PROJECT_DIR="$HOME/Guitar_Practice_Support_SW"

BACKUP_DIR="$HOME/backups/guitar-practice"
S3_PATH="s3://appserver-db-backup/guitar-practice"
BACKUP_FILE="guitar_practice_$(date +%F_%H%M%S).sql.gz"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

docker compose -f db/docker-compose.yml exec -T postgres \
  pg_dump -U guitar_migrator -d guitar_practice \
  | gzip > "$BACKUP_DIR/$BACKUP_FILE"

gzip -t "$BACKUP_DIR/$BACKUP_FILE"

aws s3 cp "$BACKUP_DIR/$BACKUP_FILE" "$S3_PATH/$BACKUP_FILE"
echo "Backup completed: $S3_PATH/$BACKUP_FILE"

