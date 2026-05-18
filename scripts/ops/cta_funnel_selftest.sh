set -e

DB_USER=$(docker exec aici-auth-db printenv POSTGRES_USER)
DB_NAME=$(docker exec aici-auth-db printenv POSTGRES_DB)
ADMIN_USER=$(grep '^AICI_ADMIN_USERNAME=' .env | cut -d= -f2- | tr -d '"')
ADMIN_PASS=$(grep '^AICI_ADMIN_PASSWORD=' .env | cut -d= -f2- | tr -d '"')

ACCOUNT_ID=$(cat /proc/sys/kernel/random/uuid)
BILLING_EVENT_ID=$(cat /proc/sys/kernel/random/uuid)
TAG=$(date -u +%Y%m%d%H%M%S)
CTA_ID="funnel_selftest_${TAG}"
PROVIDER_EVENT_ID="funnel_paid_${TAG}"

curl -sS -X POST "https://aici.pro/api/v1/events/cta" \
  -H "Content-Type: application/json" \
  --data "{\"cta_id\":\"${CTA_ID}\",\"location\":\"hero\",\"href\":\"https://aici.pro/pricing\",\"metadata\":{\"account_id\":\"${ACCOUNT_ID}\",\"page_path\":\"/\",\"auth_state\":\"authenticated\"}}"

docker exec -i aici-auth-db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" \
  -c "INSERT INTO auth_accounts (id,email,full_name,newsletter_opt_in,created_at,updated_at)
      VALUES ('${ACCOUNT_ID}'::uuid,'funnel-${TAG}@example.test','Funnel Selftest',FALSE,NOW()+interval '2 seconds',NOW()+interval '2 seconds');" \
  -c "UPDATE auth_accounts
      SET email_verified_at=NOW()+interval '4 seconds', updated_at=NOW()+interval '4 seconds'
      WHERE id='${ACCOUNT_ID}'::uuid;" \
  -c "INSERT INTO billing_events (id,provider_event_id,provider,event_type,account_id,processed_at,created_at,updated_at)
      VALUES ('${BILLING_EVENT_ID}'::uuid,'${PROVIDER_EVENT_ID}',
      (SELECT enumlabel::billing_event_provider FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid
       WHERE t.typname='billing_event_provider' AND lower(enumlabel)='stripe' LIMIT 1),
      'checkout.session.completed','${ACCOUNT_ID}'::uuid,
      NOW()+interval '6 seconds',NOW()+interval '6 seconds',NOW()+interval '6 seconds');"

START=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u -d '2 hours' +%Y-%m-%dT%H:%M:%SZ)

RESP=$(curl -sS --max-time 20 -u "$ADMIN_USER:$ADMIN_PASS" \
"http://127.0.0.1:8000/api/v1/admin/cta-analytics/funnel?start_at=${START}&end_at=${END}&lookback_days=7&cta_id=${CTA_ID}")

echo "$RESP"
echo "$RESP" | grep -q '"paid_users":1' && echo "OK: funnel works"


Clean after tests: 

docker exec -i aici-auth-db psql -U "$DB_USER" -d "$DB_NAME" -c "DELETE FROM billing_events WHERE account_id='${ACCOUNT_ID}'::uuid;"
docker exec -i aici-auth-db psql -U "$DB_USER" -d "$DB_NAME" -c "DELETE FROM auth_accounts WHERE id='${ACCOUNT_ID}'::uuid;"