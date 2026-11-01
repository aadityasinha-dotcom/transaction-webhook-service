# Transaction Webhook Service

A small FastAPI service that receives transaction webhooks from a payment processor,
acknowledges them immediately (HTTP 202, a few milliseconds) and processes every
transaction **exactly once** in the background, with a simulated 30‑second external call.

| | |
|---|---|
| **Live API** | https://transaction-webhook-service-beta.vercel.app |
| **Interactive docs** | https://transaction-webhook-service-beta.vercel.app/docs |
| **Stack** | Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Supabase Postgres · Vercel |

## API

| Method | Path | Purpose | Response |
|---|---|---|---|
| `GET` | `/` | Health check | `200` `{"status":"HEALTHY","current_time":"2024-01-15T10:30:00Z"}` |
| `POST` | `/v1/webhooks/transactions` | Receive a webhook | `202` `{"status":"accepted","transaction_id":"..."}` |
| `GET` | `/v1/transactions/{transaction_id}` | Look up status (testing) | `200` one-item list, `404` if unknown |
| `POST` | `/v1/internal/process` | Internal: run processing for one id (called by Supabase, needs `X-Internal-Secret`) | `200` / `401` / `404` |

Webhook body:

```json
{
  "transaction_id": "txn_abc123def456",
  "source_account": "acc_user_789",
  "destination_account": "acc_merchant_456",
  "amount": 1500,
  "currency": "INR"
}
```

Status response (`processed_at` is `null` while `status` is `PROCESSING`):

```json
[{
  "transaction_id": "txn_abc123def456",
  "source_account": "acc_user_789",
  "destination_account": "acc_merchant_456",
  "amount": 1500.0,
  "currency": "INR",
  "status": "PROCESSED",
  "created_at": "2024-01-15T10:30:00Z",
  "processed_at": "2024-01-15T10:30:30Z"
}]
```

Malformed payloads get `422`. Duplicate `transaction_id`s still get `202` but are stored and processed once.

## How it works

```
 Payment processor                 Vercel (FastAPI)                     Supabase (Postgres)
 ─────────────────                 ────────────────                     ───────────────────
 POST /v1/webhooks/transactions ─► INSERT ... (unique transaction_id) ─► row: PROCESSING
                  ◄── 202 (~ms) ──┘                                        │ AFTER INSERT trigger
                                                                           ▼ pg_net (async HTTP)
                                   POST /v1/internal/process ◄─────────────┘
                                   sleep 30s (simulated external API)
                                   UPDATE ... SET status='PROCESSED'
                                     WHERE status='PROCESSING'          ─► row: PROCESSED
                                                                           ▲
                                   pg_cron (every minute): re-send any row still
                                   PROCESSING after 2 min  ────────────────┘
```

1. **Intake** – the webhook handler does one thing: insert the row. A `UNIQUE` constraint on
   `transaction_id` makes duplicates fail at the database, so even concurrent duplicate
   webhooks produce one row. The conflict is caught and the request still returns `202`.
2. **Trigger** – Vercel functions are serverless, so nothing can keep running after the
   response. Instead, a Postgres `AFTER INSERT` trigger uses `pg_net` to make an
   *asynchronous* HTTP call to `/v1/internal/process`. The insert is never slowed down.
3. **Processing** – the internal endpoint first *claims* the row with one atomic update
   (`processing_started_at = now() WHERE status = 'PROCESSING' AND lease not held`). A
   duplicate delivery, which does happen in practice with `pg_net`, therefore returns
   `in_progress` immediately instead of sleeping. The winner sleeps 30 s (the DB connection
   is released during the sleep) and then runs a second conditional update
   (`WHERE status = 'PROCESSING'`), so a transaction can never be processed twice.
4. **Reliability** – a `pg_cron` job re-triggers any transaction still `PROCESSING` whose
   claim is older than two minutes (lost request, function crash, deploy mid-processing).
   Failures are recorded in `last_error` / `processing_attempts` for inspection.

Locally (or on any long-lived server) set `PROCESSING_TRIGGER=inline` and the same processing
function runs as a FastAPI background task right after the `202`, with no Supabase trigger needed.

## Run locally

Requires Python 3.12+. [`uv`](https://docs.astral.sh/uv/) is the quickest way:

```bash
uv sync                                   # creates .venv and installs everything
cp .env.example .env                      # defaults: SQLite file + inline processing
uv run uvicorn app.main:app --reload      # http://localhost:8000  (docs at /docs)
```

Without uv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest pytest-asyncio httpx
uvicorn app.main:app --reload
```

## Test

Unit/integration tests (SQLite, delay set to 0, ~1 s):

```bash
uv run pytest -v
```

They cover: fast `202`, single transaction processed, 5 sequential and 10 *concurrent*
duplicates stored once, processing idempotent even if triggered twice at the same time,
the delay being honoured, `404`/`422`/`401` paths.

End-to-end against a running instance (real 30 s delay):

```bash
./scripts/smoke_test.sh                               # local
./scripts/smoke_test.sh https://transaction-webhook-service-beta.vercel.app   # deployed
```

Or by hand:

```bash
BASE=https://transaction-webhook-service-beta.vercel.app
BODY='{"transaction_id":"txn_demo_1","source_account":"acc_user_789","destination_account":"acc_merchant_456","amount":1500,"currency":"INR"}'

curl -i -X POST $BASE/v1/webhooks/transactions -H 'Content-Type: application/json' -d "$BODY"   # 202
curl -i -X POST $BASE/v1/webhooks/transactions -H 'Content-Type: application/json' -d "$BODY"   # 202 again, no new row
curl $BASE/v1/transactions/txn_demo_1      # status: PROCESSING
sleep 35
curl $BASE/v1/transactions/txn_demo_1      # status: PROCESSED, processed_at set
```

## Deploy

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Dashboard → **Connect** → copy the **Transaction pooler** URI (port `6543`). This is `DATABASE_URL`.
   If the database password contains `@`, `#`, `/` or similar, URL-encode it (`@` → `%40`).
3. Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. This is `INTERNAL_SECRET`.
4. Open `supabase/migrations/0001_init.sql`, set the two `vault.create_secret(...)` values
   (your Vercel URL + `/v1/internal/process`, and the secret), then run the whole file in
   **SQL Editor**. It creates the table, the trigger, the retry cron job and enables `pg_net`/`pg_cron`.
   Apply any later files in `supabase/migrations/` in order the same way.

   If you deploy to Vercel first and only know the URL afterwards, run the migration with the
   real URL later or update it:
   `update vault.secrets set secret = 'https://transaction-webhook-service-beta.vercel.app/v1/internal/process' where name = 'processor_url';`

### 2. Vercel

1. Push this folder to a public GitHub repo and import it in Vercel (framework is auto-detected
   from `app/main.py`; `vercel.json` sets a 60 s `maxDuration` for the 30 s processing step).
   `vercel.json` also pins the function to `fra1` (Frankfurt) so it sits next to a Supabase
   project in `eu-central-1`; every request makes a few database round trips, so keeping the
   function in the same region is what keeps the webhook well under 500 ms. Change the region
   if your Supabase project lives elsewhere.
2. Environment variables (Project → Settings → Environment Variables):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Supabase transaction-pooler URI |
   | `INTERNAL_SECRET` | the secret from step 1.3 |
   | `PROCESSING_TRIGGER` | `supabase` |
   | `AUTO_CREATE_TABLES` | `false` |
   | `PROCESSING_DELAY_SECONDS` | `30` (optional, default) |

3. Deploy, then run `./scripts/smoke_test.sh https://transaction-webhook-service-beta.vercel.app`.

Debugging the pipeline from the Supabase SQL editor:

```sql
select * from net._http_response order by created desc limit 20;      -- trigger/cron HTTP results
select * from cron.job_run_details order by start_time desc limit 20;  -- retry job runs
select transaction_id, status, processing_attempts, last_error from public.transactions;
```

## Technical choices

- **FastAPI** – async, fast, typed request validation via Pydantic, OpenAPI docs for free.
- **Supabase Postgres + SQLAlchemy 2 (async, psycopg 3)** – a real relational `UNIQUE`
  constraint is the simplest correct idempotency mechanism; the transaction pooler suits
  serverless (short-lived connections, prepared statements disabled).
- **DB-triggered processing instead of an in-process worker or a queue** – Vercel cannot
  run long-lived workers, and adding Redis/Celery would be a second managed service for a
  single 30 s step. Postgres already offers a durable queue (the table), an async HTTP
  client (`pg_net`) and a scheduler (`pg_cron`), so the whole pipeline lives in the database
  and nothing is lost if a function dies.
- **Lease + conditional `UPDATE ... WHERE status='PROCESSING'`** – exactly-once processing
  without locks, and duplicate deliveries cost milliseconds rather than a 30 s invocation.
- **Connection released during the 30 s sleep** – a burst of webhooks cannot exhaust the pool.
- **Timestamps in UTC, amounts as `Numeric(18,2)`** – no float money, no timezone ambiguity.
- **Same code path for local and cloud** – `PROCESSING_TRIGGER=inline` runs processing as a
  FastAPI background task locally; tests use SQLite with a zero delay so the suite runs in ~1 s.

## Project layout

```
app/
  main.py          FastAPI app + lifespan (Vercel entrypoint)
  config.py        settings from environment
  db.py            async engine / sessions
  models.py        Transaction table
  schemas.py       request/response models
  services.py      record_webhook (idempotent insert) + process_transaction
  routers/         health, webhooks, transactions, internal
supabase/migrations/               schema, trigger, retry cron (apply in order)
scripts/smoke_test.sh               end-to-end check
tests/                              pytest suite
vercel.json                         function maxDuration
```
