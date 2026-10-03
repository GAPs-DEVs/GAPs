#!/bin/sh
# One-shot commands executed by the Docker Compose `migrate` service.
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

export PGDATABASE="$POSTGRES_DB"
export PGUSER="$POSTGRES_USER"
export PGPASSWORD="$POSTGRES_PASSWORD"

run_migrations() {
  for migration in /migrations/*.sql; do
    echo "Applying ${migration##*/}"
    psql -v ON_ERROR_STOP=1 -f "$migration"
  done

  echo "Applying API database permissions"
  psql -v ON_ERROR_STOP=1 -f /admin/grant_api_role.sql.example
}

case "${1:-migrate}" in
  migrate)
    run_migrations
    ;;
  seed)
    echo "Applying development seed data"
    psql -v ON_ERROR_STOP=1 -f /seed/development.sql
    ;;
  *)
    echo "Usage: database.sh [migrate|seed]" >&2
    exit 64
    ;;
esac
