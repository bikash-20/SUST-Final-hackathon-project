#!/bin/sh
set -eu

: "${MIGRATION_DATABASE_URL:?Set MIGRATION_DATABASE_URL to the privileged Railway PostgreSQL URL}"
: "${DB_APP_PASSWORD:?Set DB_APP_PASSWORD}"
: "${DB_BKASH_PASSWORD:?Set DB_BKASH_PASSWORD}"
: "${DB_NAGAD_PASSWORD:?Set DB_NAGAD_PASSWORD}"
: "${DB_ROCKET_PASSWORD:?Set DB_ROCKET_PASSWORD}"

for migration in infra/001_init.sql infra/002_hardening.sql infra/003_case_notes.sql infra/004_historical_analytics.sql; do
  psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration"
done

# Initial migrations create least-privilege roles with development-only
# bootstrap passwords. Rotate them on every deployment to Railway secrets.
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
