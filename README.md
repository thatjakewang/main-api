# Tesla Analytics API

A personal Tesla cost-tracking backend that records charging sessions and car expenses, serving data to a frontend dashboard.

## Stack

- **FastAPI** — web framework
- **PostgreSQL** — database
- **SQLAlchemy** — database connection
- **uvicorn** — ASGI server

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
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API docs available at http://localhost:8000/docs after startup.

## Database Migrations (one-off scripts)

Some schema changes are delivered as standalone scripts in `migrations/`.

These scripts auto-load `DATABASE_URL` from the `.env` in the project root (using `python-dotenv`).

### Applied migrations

- `add_tesla_recent_columns.py` — executed on production (web-01) on 2026-06-03.

On the production server (for future migrations):

```bash
cd /var/www/main-api
source .venv/bin/activate
python migrations/<script_name>.py
```

You can run the script before or after restarting the API service (the API endpoints are backward-compatible).

## Endpoints

### Public

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/tesla/stats` | Total cost, charging cost, cost per km |
| GET | `/api/tesla/expenses` | Car expenses grouped by item |
| GET | `/api/tesla/expenses/recent` | Recent 10 car expenses (newest first) |
| GET | `/api/tesla/charging/providers` | Charging cost grouped by provider |
| GET | `/api/tesla/charging/monthly-trend` | Monthly charging trend |
| GET | `/api/tesla/charging/recent` | Recent 10 charging records (newest first) |

> Note: `charging_records` and `car_expenses` tables were extended with `id` (SERIAL) and `created_at` (for stable recent ordering, matching `daily_expenses`).
> The `/charging/recent`, `/expenses/recent`, and the two create endpoints are backward-compatible:
> they work on old schema (id/created_at returned as null) and automatically use the richer data + better
> ordering once the migration has been applied (no API restart needed after migration).

### Protected (Header: `x-api-key`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tesla/charging-records` | Create a charging record |
| POST | `/api/tesla/car-expenses` | Create a car expense |
| POST | `/api/life/expenses` | Create a daily expense |
| GET | `/api/life/expenses/daily-ai-summary` | Create an AI daily expense summary JSON response |
| GET | `/api/life/expenses/daily-ai-summary/message` | Create an AI daily expense summary plain text message |
| GET | `/api/life/expenses/monthly-ai-summary` | Create an AI monthly expense summary JSON response |
| GET | `/api/life/expenses/monthly-ai-summary/message` | Create an AI monthly expense summary plain text message |

### POST `/api/tesla/charging-records`

```json
{
  "charge_date": "2026-05-09",
  "provider": "Tesla Supercharger",
  "amount": 150,
  "kwh": 30.5
}
```

Response includes `id` (may be null until migration run):

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

Response includes `id` (may be null until migration run):

```json
{
  "status": "success",
  "message": "Car expense created",
  "data": { "id": 45, "date": "2026-05-09", "item": "Insurance", "amount": 25000 }
}
```

### GET `/api/life/expenses/monthly-ai-summary`

Returns a short Traditional Chinese monthly expense analysis for iPhone Shortcuts to send via iMessage.

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
