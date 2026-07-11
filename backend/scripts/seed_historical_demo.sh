#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: sh backend/scripts/seed_historical_demo.sh https://your-api.onrender.com" >&2
  exit 2
fi

base_url=${1%/}
curl -fsS "$base_url/healthz" >/dev/null

for provider in bkash nagad rocket; do
  echo "Seeding 20 committed salary-window transactions for $provider"
  curl -fsS -X POST "$base_url/v1/simulation/inject" \
    -H 'content-type: application/json' \
    --data "{\"provider\":\"$provider\",\"number_of_transactions\":20,\"min_amount_bdt\":250,\"max_amount_bdt\":1000,\"amount_pattern\":\"varied\",\"distinct_accounts\":20,\"window_seconds\":60,\"is_salary_window\":true}"
  echo
done

# The synthetic clock runs at 60x. Two seconds moves beyond the one-minute
# event window and the minute-scoped analytics cache before final hydration.
sleep 2

echo "Historical analytics snapshot:"
if command -v jq >/dev/null 2>&1; then
  curl -fsS "$base_url/v1/telemetry/snapshot" | jq '{
    historical_analytics,
    latest_forecasts: (.liquidity_forecasts | with_entries(.value |= {
      position_id,
      confidence_score,
      confidence_score_with_history,
      historical_context
    }))
  }'
else
  curl -fsS "$base_url/v1/telemetry/snapshot"
  echo
fi
