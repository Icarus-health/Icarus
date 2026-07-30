#!/bin/bash
# Legt die Anwendungsdatenbank für Mem0 an (Nutzer, Auth, API-Keys).
# Die Default-Datenbank aus POSTGRES_DB bleibt der pgvector-Speicher für Memories.
#
# Läuft nur beim allerersten Start eines leeren Postgres-Volumes
# (docker-entrypoint-initdb.d). Nach `make clean` läuft es erneut.
set -e

: "${APP_DB_NAME:=mem0_app}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	SELECT 'CREATE DATABASE $APP_DB_NAME'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$APP_DB_NAME')\gexec
EOSQL

echo "init-db: Datenbank '$APP_DB_NAME' bereit."
