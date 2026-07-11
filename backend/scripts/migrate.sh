#!/bin/sh
set -eu

: "${MIGRATION_DATABASE_URL:?Set MIGRATION_DATABASE_URL to the direct privileged Render PostgreSQL URL}"
: "${DB_APP_PASSWORD:?Set DB_APP_PASSWORD}"
: "${DB_BKASH_PASSWORD:?Set DB_BKASH_PASSWORD}"
: "${DB_NAGAD_PASSWORD:?Set DB_NAGAD_PASSWORD}"
: "${DB_ROCKET_PASSWORD:?Set DB_ROCKET_PASSWORD}"

migration_user="$(
  psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 -Atc 'SELECT CURRENT_USER'
)"
can_create_roles="$(
  psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 -Atc \
    'SELECT rolsuper OR rolcreaterole FROM pg_catalog.pg_roles WHERE rolname = CURRENT_USER'
)"
if [ "$can_create_roles" != "t" ]; then
  echo "Migration user $migration_user needs CREATEROLE to provision isolated application roles." >&2
  exit 1
fi
echo "Migration preflight: user=$migration_user create_roles=ok"

for migration in infra/001_init.sql infra/002_hardening.sql infra/003_case_notes.sql infra/004_historical_analytics.sql; do
  echo "Applying $migration"
  psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 \
    --single-transaction -f "$migration"
done

# Initial migrations create least-privilege roles with development-only
# bootstrap passwords. Rotate them on every deployment to Render secrets.
psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 \
  --set=shared_password="$DB_APP_PASSWORD" \
  --set=bkash_password="$DB_BKASH_PASSWORD" \
  --set=nagad_password="$DB_NAGAD_PASSWORD" \
  --set=rocket_password="$DB_ROCKET_PASSWORD" <<'SQL'
SELECT format('ALTER ROLE app_shared PASSWORD %L', :'shared_password') \gexec
SELECT format('ALTER ROLE app_bkash PASSWORD %L', :'bkash_password') \gexec
SELECT format('ALTER ROLE app_nagad PASSWORD %L', :'nagad_password') \gexec
SELECT format('ALTER ROLE app_rocket PASSWORD %L', :'rocket_password') \gexec
SQL

echo "Migration complete: schemas ready and application-role passwords rotated"
