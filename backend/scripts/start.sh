#!/bin/sh
set -e
sh scripts/migrate.sh
unset MIGRATION_DATABASE_URL
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
