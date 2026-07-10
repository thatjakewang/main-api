# Tesla Analytics API

A personal backend that records Tesla charging sessions / car expenses and daily life
expenses, serving data to a frontend dashboard. Writes come from
iPhone Shortcuts (protected by an API key).

## Stack

- **FastAPI** — web framework
- **PostgreSQL** — database
- **SQLAlchemy** — database connection
- **uvicorn** — ASGI server

## Project Layout

```text
app/
  main.py          # FastAPI app, CORS, router mounting
  config.py        # pydantic-settings configuration (.env)
  database.py      # engine + per-request session
  dependencies.py  # x-api-key verification
  utils.py         # row serialization, response envelope, date helpers
  routers/         # thin HTTP route handlers (tesla / life)
  services/        # business logic (AI expense summaries)
tests/             # pytest suite (no real DB / no real OpenAI calls)
schema.sql         # reference DDL for rebuilding the database
```

## Environment Variables

All configuration is loaded via `pydantic-settings` from `.env` (or environment variables). Create a `.env` file with the following (defaults exist for some):

```env
DATABASE_URL=postgresql://user:password@host:port/dbname
SHORTCUT_API_KEY=your_api_key
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5.4-mini
APP_TIMEZONE=Asia/Taipei
MONTHLY_INCOME=80000
MONTHLY_FIXED_EXPENSES=35000
TESLA_ODOMETER_KM=21471
```

## Setup & Run

```bash
python -m venv .venv          # Python version pinned in .python-version (3.14)
source .venv/bin/activate
pip install -r requirements.lock   # fully pinned; use requirements.txt for top-level only
uvicorn app.main:app --reload
```

API docs available at http://localhost:8000/docs after startup.

Dependencies: `requirements.txt` lists top-level packages; `requirements.lock` is the
fully pinned set generated with `uv pip compile requirements.txt -o requirements.lock`.
Re-generate the lock after changing `requirements.txt`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```

Tests never touch a real database or OpenAI — DB sessions and the AI client are faked.
GitHub Actions (`.github/workflows/ci.yml`) runs the suite on every push and pull request.

## Database Schema & Migrations

`schema.sql` is the reference DDL for every table the API uses. Rebuild an empty
database with:

```bash
psql "$DATABASE_URL" -f schema.sql
```

Schema changes are delivered as one-off idempotent scripts in `migrations/`
(they auto-load `DATABASE_URL` from the project-root `.env` via `python-dotenv`).
The workflow:

1. Deploy code that works with both the old and the new schema.
2. Run the script on the production server:

   ```bash
   cd /var/www/main-api
   source .venv/bin/activate
   python migrations/<script_name>.py
   ```

3. Update `schema.sql` to match, then delete the script — git history keeps the
   record. Applied so far: `add_tesla_recent_columns.py` (2026-06-03),
   `add_odometer_readings.py` (2026-06), `drop_payment_method.py` (by 2026-06-28).

## Endpoints

### Public

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (pings the DB; 503 if unreachable) |
| GET | `/api/tesla/stats` | Total cost, charging cost, cost per km |
| GET | `/api/tesla/monthly-summary` | Month-by-month cost & efficiency (km driven, cost/km, kWh/100km) |
| GET | `/api/tesla/expenses` | Car expenses grouped by item |
| GET | `/api/tesla/expenses/recent` | Recent 10 car expenses (newest first) |
| GET | `/api/tesla/charging/providers` | Charging cost grouped by provider |
| GET | `/api/tesla/charging/monthly-trend` | Monthly charging trend |
| GET | `/api/tesla/charging/sessions` | Full charging history (per-session cost distribution) |
| GET | `/api/tesla/charging/recent` | Recent 10 charging records (newest first) |
| GET | `/api/tesla/odometer/current` | Latest known odometer reading (km) |
| GET | `/api/tesla/odometer/recent` | Recent 10 odometer readings (newest first) |
| GET | `/api/life/expenses/recent` | Recent 10 daily expenses (newest first) |
| GET | `/api/life/expenses/summary` | Current-month total + count + prev-month same-period total |
| GET | `/api/life/expenses/category` | Current-month totals grouped by category |
| GET | `/api/life/expenses/daily` | Daily totals, trailing 90-day window (zero-filled) |

> Note: all tables carry an `id` (SERIAL) column. The `/recent` endpoints order by the
> record's date column then `id DESC`, so rows logged on the same date come back newest-first.

> Rate limiting: every endpoint except `/health` is capped at 120 requests/minute per
> client IP (in-memory, via slowapi). Behind a reverse proxy, run uvicorn with
> `--proxy-headers` (and `--forwarded-allow-ips`) so the real client IP is used.

### Protected (Header: `x-api-key`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tesla/charging-records` | Create a charging record |
| POST | `/api/tesla/car-expenses` | Create a car expense |
| POST | `/api/tesla/odometer` | Log a total-odometer reading |
| POST | `/api/life/expenses` | Create a daily expense |
| GET | `/api/life/expenses/daily-ai-summary` | AI daily expense summary (JSON) |
| GET | `/api/life/expenses/daily-ai-summary/message` | AI daily expense summary (plain text) |
| GET | `/api/life/expenses/monthly-ai-summary` | AI monthly expense summary (JSON) |
| GET | `/api/life/expenses/monthly-ai-summary/message` | AI monthly expense summary (plain text) |

> Error convention for the AI summary endpoints: the JSON endpoints follow standard HTTP
> semantics and return **502** with a detail message when the AI call fails; the `/message`
> endpoints always return **HTTP 200** readable text (`AI summary failed: ...` on failure),
> so iPhone Shortcuts can forward the body directly without parsing errors.

### POST `/api/tesla/charging-records`

```json
{
  "charge_date": "2026-05-09",
  "provider": "Tesla Supercharger",
  "amount": 150,
  "kwh": 30.5
}
```

Response includes `id`:

```json
{
  "status": "success",
  "message": "Charging record created",
  "data": { "id": 123, "charge_date": "2026-05-09", "provider": "...", "amount": 150, "kwh": 30.5 }
}
```

### POST `/api/tesla/car-expenses`

```json
{
  "date": "2026-05-09",
  "item": "Insurance",
  "amount": 25000
}
```

Response includes `id`:

```json
{
  "status": "success",
  "message": "Car expense created",
  "data": { "id": 45, "date": "2026-05-09", "item": "Insurance", "amount": 25000 }
}
```

### POST `/api/tesla/odometer`

```json
{
  "reading_km": 23120,
  "reading_date": "2026-06-09"
}
```

`reading_date` is optional (defaults to today). Cost-per-km in `/api/tesla/stats`
automatically follows the latest reading.

### GET `/api/life/expenses/monthly-ai-summary`

Returns a short English monthly expense analysis for iPhone Shortcuts to send via iMessage.

Headers:

```http
x-api-key: your_api_key
```

Optional query params:

```text
target_month=2026-05
```

Response:

```json
{
  "status": "success",
  "month": "2026-05",
  "message": "2026-05 Monthly expense analysis...",
  "data": {
    "total_amount": 1401,
    "record_count": 15,
    "categories": [],
    "budget": {
      "monthly_income_configured": true,
      "monthly_fixed_expenses_configured": true,
      "disposable_used_ratio": 3.1,
      "disposable_remaining": 43599
    }
  }
}
```

For the simplest iPhone Shortcuts setup, use the plain text endpoint:

1. Add `Get Contents of URL`.
2. URL: `https://your-domain.com/api/life/expenses/monthly-ai-summary/message`.
3. Method: `GET`.
4. Headers: `x-api-key` = your shortcut API key.
5. Use `Send Message` to send the URL content via iMessage.

## CORS

Allowed origins: `jakewang.dev`, `www.jakewang.dev`, `localhost:5001`
