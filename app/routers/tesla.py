"""Tesla router - cost tracking for a personal Tesla (charging + car expenses).

Public read-only stats endpoints plus protected write endpoints (used by iPhone
Shortcuts / automation). All monetary values are stored as integers and kWh as
floats. The id + created_at columns were added in
migrations/add_tesla_recent_columns.py (executed on prod 2026-06-03).
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import verify_shortcut_api_key
from app.utils import serialize_row, success_response

router = APIRouter()
settings = get_settings()


class ChargingRecordCreate(BaseModel):
    """Payload for creating a charging record (Tesla Supercharger, etc.)."""
    charge_date: date
    provider: str
    amount: int = Field(ge=0)
    kwh: float = Field(ge=0)


class CarExpenseCreate(BaseModel):
    """Payload for recording a car-related expense (insurance, maintenance, etc.)."""
    date: date
    item: str
    amount: int = Field(ge=0)


class OdometerReadingCreate(BaseModel):
    """Payload for logging a total-odometer reading (the number shown on the car screen)."""
    reading_km: int = Field(ge=0)
    reading_date: date = Field(default_factory=date.today)


def get_latest_odometer(db: Session) -> int:
    """Return the most recent odometer reading, or the config seed if none exist yet."""
    reading = db.execute(text("""
        SELECT reading_km
        FROM odometer_readings
        ORDER BY reading_date DESC, created_at DESC, id DESC
        LIMIT 1
    """)).scalar()
    return int(reading) if reading is not None else settings.tesla_odometer_km


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """Return high-level Tesla cost statistics (public).

    Includes lifetime totals, average price per kWh, and cost per km based on
    the latest odometer reading. All queries are simple aggregates over the
    entire history (personal use, tables stay small).
    """
    expense_query = text("""
        SELECT COALESCE(SUM(amount), 0) AS car_expense_total
        FROM car_expenses
    """)
    charging_query = text("""
        SELECT
            COALESCE(SUM(amount), 0) AS charging_cost,
            COALESCE(SUM(kwh), 0) AS energy_kwh
        FROM charging_records
    """)

    expense = db.execute(expense_query).mappings().one()
    charging = db.execute(charging_query).mappings().one()

    car_expense_total = float(expense["car_expense_total"])
    charging_cost = float(charging["charging_cost"])
    energy_kwh = float(charging["energy_kwh"])

    total_cost = car_expense_total + charging_cost
    avg_price_per_kwh = round(charging_cost / energy_kwh, 2) if energy_kwh else 0

    odometer_km = get_latest_odometer(db)
    cost_per_km = round(total_cost / odometer_km, 2) if odometer_km else 0

    return {
        "total_cost": total_cost,
        "charging_cost": charging_cost,
        "energy_kwh": round(energy_kwh, 2),
        "avg_price_per_kwh": avg_price_per_kwh,
        "odometer_km": odometer_km,
        "cost_per_km": cost_per_km,
    }


@router.get("/expenses")
def get_expenses(db: Session = Depends(get_db)):
    """Return car expenses grouped by item (e.g. Insurance, Tires), sorted by total desc. Public."""
    query = text("""
        SELECT item, SUM(amount) AS total_amount
        FROM car_expenses
        GROUP BY item
        ORDER BY total_amount DESC
    """)
    rows = db.execute(query).mappings().all()

    return [
        {"item": row["item"], "total_amount": float(row["total_amount"] or 0)}
        for row in rows
    ]


@router.get("/charging/providers")
def get_charging_by_provider(db: Session = Depends(get_db)):
    """Return charging statistics grouped by provider (Supercharger, Home, etc.).

    Includes total kWh, total cost, and average price per kWh per provider.
    Public endpoint. NULLIF protects against division by zero.
    """
    query = text("""
        SELECT
            provider,
            SUM(kwh) AS total_kwh,
            SUM(amount) AS total_amount,
            SUM(amount) / NULLIF(SUM(kwh), 0) AS avg_price_per_kwh
        FROM charging_records
        GROUP BY provider
        ORDER BY total_amount DESC
    """)
    rows = db.execute(query).mappings().all()

    return [
        {
            "provider": row["provider"],
            "total_kwh": round(float(row["total_kwh"] or 0), 2),
            "total_amount": float(row["total_amount"] or 0),
            "avg_price_per_kwh": round(float(row["avg_price_per_kwh"] or 0), 2),
        }
        for row in rows
    ]


@router.get("/charging/monthly-trend")
def get_monthly_charging_trend(db: Session = Depends(get_db)):
    """Return month-by-month charging totals and average price per kWh.

    Uses Postgres DATE_TRUNC for monthly bucketing. Ordered chronologically. Public.
    """
    query = text("""
        SELECT
            TO_CHAR(DATE_TRUNC('month', charge_date), 'YYYY-MM') AS month,
            SUM(kwh) AS total_kwh,
            SUM(amount) AS total_amount,
            SUM(amount) / NULLIF(SUM(kwh), 0) AS avg_price_per_kwh
        FROM charging_records
        GROUP BY DATE_TRUNC('month', charge_date)
        ORDER BY DATE_TRUNC('month', charge_date)
    """)
    rows = db.execute(query).mappings().all()

    return [
        {
            "month": row["month"],
            "total_kwh": round(float(row["total_kwh"] or 0), 2),
            "total_amount": float(row["total_amount"] or 0),
            "avg_price_per_kwh": round(float(row["avg_price_per_kwh"] or 0), 2),
        }
        for row in rows
    ]


@router.get("/charging/recent")
def get_recent_charging_records(db: Session = Depends(get_db)):
    """Return the 10 most recent charging records (newest first). Public."""
    query = text("""
        SELECT id, charge_date, provider, amount, kwh, created_at
        FROM charging_records
        ORDER BY charge_date DESC, created_at DESC, id DESC
        LIMIT 10
    """)
    rows = db.execute(query).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get("/expenses/recent")
def get_recent_car_expenses(db: Session = Depends(get_db)):
    """Return the 10 most recent car expense records (newest first). Public."""
    query = text("""
        SELECT id, date, item, amount, created_at
        FROM car_expenses
        ORDER BY date DESC, created_at DESC, id DESC
        LIMIT 10
    """)
    rows = db.execute(query).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post("/charging-records")
def create_charging_record(
    payload: ChargingRecordCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Insert a new charging record (protected by x-api-key).

    Used by iPhone Shortcuts or other trusted clients to log a charge session.
    """
    query = text("""
        INSERT INTO charging_records (charge_date, provider, amount, kwh)
        VALUES (:charge_date, :provider, :amount, :kwh)
        RETURNING id
    """)
    new_id = db.execute(
        query,
        {
            "charge_date": payload.charge_date,
            "provider": payload.provider,
            "amount": payload.amount,
            "kwh": payload.kwh,
        },
    ).scalar_one()
    db.commit()

    return success_response(
        "Charging record created",
        {
            "id": new_id,
            "charge_date": payload.charge_date.isoformat(),
            "provider": payload.provider,
            "amount": payload.amount,
            "kwh": payload.kwh,
        },
    )


@router.post("/car-expenses")
def create_car_expense(
    payload: CarExpenseCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Insert a new car expense record (protected by x-api-key).

    Used by Shortcuts etc. to log one-off car costs (tires, insurance, etc.).
    """
    query = text("""
        INSERT INTO car_expenses (date, item, amount)
        VALUES (:date, :item, :amount)
        RETURNING id
    """)
    new_id = db.execute(
        query,
        {
            "date": payload.date,
            "item": payload.item,
            "amount": payload.amount,
        },
    ).scalar_one()
    db.commit()

    return success_response(
        "Car expense created",
        {
            "id": new_id,
            "date": payload.date.isoformat(),
            "item": payload.item,
            "amount": payload.amount,
        },
    )


@router.get("/odometer/current")
def get_current_odometer(db: Session = Depends(get_db)):
    """Return the latest known total odometer in km (public, for the dashboard)."""
    return {"odometer_km": get_latest_odometer(db)}


@router.get("/odometer/recent")
def get_recent_odometer_readings(db: Session = Depends(get_db)):
    """Return the 10 most recent odometer readings (newest first). Public."""
    query = text("""
        SELECT id, reading_km, reading_date, created_at
        FROM odometer_readings
        ORDER BY reading_date DESC, created_at DESC, id DESC
        LIMIT 10
    """)
    rows = db.execute(query).mappings().all()

    return [serialize_row(row) for row in rows]


@router.post("/odometer")
def create_odometer_reading(
    payload: OdometerReadingCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Log a total-odometer reading (protected by x-api-key).

    reading_km is the cumulative number shown on the Tesla screen; cost-per-km
    in /stats automatically follows the latest reading.
    """
    new_id = db.execute(
        text("""
            INSERT INTO odometer_readings (reading_km, reading_date)
            VALUES (:reading_km, :reading_date)
            RETURNING id
        """),
        {"reading_km": payload.reading_km, "reading_date": payload.reading_date},
    ).scalar_one()
    db.commit()

    return success_response(
        "Odometer reading created",
        {
            "id": new_id,
            "reading_km": payload.reading_km,
            "reading_date": payload.reading_date.isoformat(),
        },
    )
