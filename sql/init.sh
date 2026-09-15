#!/bin/sh
# Exécuté une seule fois, au tout premier démarrage de PostgreSQL.
#
# L'image officielle a déjà créé la base `n8n` (POSTGRES_DB). On ajoute `agent`,
# qui porte les données métier, puis on y applique le schéma et le seed.
#
# Un `\connect` en fin de fichier .sql ne suffirait pas : l'entrypoint ouvre une
# session psql distincte par fichier, toujours sur POSTGRES_DB.

set -e

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres <<-SQL
	create database agent;
SQL

for f in /schema/001_schema.sql /schema/002_seed.sql /schema/003_runbooks.sql; do
	[ -f "$f" ] || continue
	echo "[init] application de $f sur la base agent"
	psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d agent -f "$f"
done

echo "[init] base agent prête"
