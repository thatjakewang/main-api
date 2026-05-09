# Tesla Analytics API

A personal Tesla cost-tracking backend that records charging sessions and car expenses, serving data to a frontend dashboard.

## Stack

- **FastAPI** — web framework
- **PostgreSQL** — database
- **SQLAlchemy** — database connection
- **uvicorn** — ASGI server

## Environment Variables

Create a `.env` file with the following:

```env
DATABASE_URL=postgresql://user:password@host:port/dbname
SHORTCUT_API_KEY=your_api_key
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

## Endpoints

### Public

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/tesla/stats` | Total cost, charging cost, cost per km |
| GET | `/api/tesla/expenses` | Car expenses grouped by item |
| GET | `/api/tesla/charging/providers` | Charging cost grouped by provider |
| GET | `/api/tesla/charging/monthly-trend` | Monthly charging trend |

### Protected (Header: `x-api-key`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tesla/charging-records` | Create a charging record |
| POST | `/api/tesla/car-expenses` | Create a car expense |

### POST `/api/tesla/charging-records`

```json
{
  "charge_date": "2026-05-09",
  "provider": "Tesla Supercharger",
  "amount": 150,
  "kwh": 30.5
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

## CORS

Allowed origins: `jakewang.dev`, `www.jakewang.dev`, `localhost:5001`
