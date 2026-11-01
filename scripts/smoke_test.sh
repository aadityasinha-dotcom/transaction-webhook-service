#!/usr/bin/env bash
# End-to-end check against a running instance (local or deployed).
#   ./scripts/smoke_test.sh                      # defaults to http://localhost:8000
#   ./scripts/smoke_test.sh https://my-app.vercel.app
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
TXN_ID="txn_smoke_$(date +%s)"
PAYLOAD=$(cat <<JSON
{"transaction_id":"$TXN_ID","source_account":"acc_user_789","destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}
JSON
)

echo "== Health check"
curl -s "$BASE_URL/" ; echo

echo "== Send the same webhook 3 times (expect 202 each time, and the response time)"
for i in 1 2 3; do
  curl -s -o /dev/null -w "  attempt $i -> HTTP %{http_code} in %{time_total}s\n" \
    -X POST "$BASE_URL/v1/webhooks/transactions" \
    -H "Content-Type: application/json" -d "$PAYLOAD"
done

echo "== Status immediately (expect PROCESSING, processed_at null)"
curl -s "$BASE_URL/v1/transactions/$TXN_ID" ; echo

echo "== Waiting 35s for background processing..."
sleep 35

echo "== Status after delay (expect PROCESSED with processed_at set, still exactly one record)"
curl -s "$BASE_URL/v1/transactions/$TXN_ID" ; echo
