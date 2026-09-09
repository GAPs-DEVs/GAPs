#!/bin/sh
# This script is processed only when PostgreSQL initializes an empty data volume.
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${GUITAR_API_USER:?GUITAR_API_USER is required}"
: "${GUITAR_API_PASSWORD:?GUITAR_API_PASSWORD is required}"

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 \
  --set "api_user=$GUITAR_API_USER" \
  --set "api_password=$GUITAR_API_PASSWORD" <<'SQL'
CREATE ROLE :"api_user"
    LOGIN
    PASSWORD :'api_password'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOINHERIT;
SQL
