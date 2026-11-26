#!/bin/sh
(
    set -eu

    psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        --set ON_ERROR_STOP=on \
        --set studio_password="$STUDIO_DB_PASSWORD" \
        --set n8n_password="$N8N_DB_PASSWORD" <<'SQL'
CREATE ROLE studio LOGIN CREATEDB PASSWORD :'studio_password';
CREATE DATABASE studio OWNER studio;
CREATE ROLE n8n LOGIN PASSWORD :'n8n_password';
CREATE DATABASE n8n OWNER n8n;
REVOKE CONNECT ON DATABASE studio FROM PUBLIC;
REVOKE CONNECT ON DATABASE n8n FROM PUBLIC;
SQL
)
