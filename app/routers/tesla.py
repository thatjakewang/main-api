"""Tesla router - cost tracking for a personal Tesla (charging + car expenses).

Public read-only stats endpoints plus protected write endpoints (used by iPhone
Shortcuts / automation). Includes recent lists for charges and car expenses,
modeled after the life router's Recent Expenses. All monetary values are stored
as integers (or bigint) and kWh as floats.
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import verify_shortcut_api_key

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

# Returns aggregated Tesla cost stats (total expenses, charging costs, cost per km).
# Public endpoint - no authentication required.
@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    # Returns aggregated Tesla cost stats (total expenses, charging costs, cost per km).
    # Public endpoint - no authentication required.
    """Return high-level Tesla cost statistics (public).

    Includes lifetime totals, average price per kWh, and cost per km based on
    the configured TESLA_ODOMETER_KM. All queries are simple aggregates over the
    entire history (personal use, tables stay small).
    """
    expense_query = text("""
        SELECT
            COALESCE(SUM(amount), 0) AS car_expense_total
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

    odometer_km = settings.tesla_odometer_km
    cost_per_km = round(total_cost / odometer_km, 2) if odometer_km else 0

    return {
        "total_cost": total_cost,
        "charging_cost": charging_cost,
        "energy_kwh": round(energy_kwh, 2),
        "avg_price_per_kwh": avg_price_per_kwh,
        "odometer_km": odometer_km,
        "cost_per_km": cost_per_km,
    }


# Returns car expenses grouped and summed by item (e.g. "Insurance", "Tires").
# Sorted by total descending. Public endpoint.
@router.get("/expenses")
def get_expenses(db: Session = Depends(get_db)):
    # Returns car expenses grouped and summed by item (e.g. "Insurance", "Tires").
    # Sorted by total descending. Public endpoint.
    """Return car expenses grouped by item (e.g. Insurance, Tires), sorted by total descending.

    Public endpoint.
    """
    query = text("""
        SELECT
            item,
            SUM(amount) AS total_amount
        FROM car_expenses
        GROUP BY item
        ORDER BY total_amount DESC
    """)

    rows = db.execute(query).mappings().all()

    return [
        {
            "item": row["item"],
            "total_amount": float(row["total_amount"] or 0),
        }
        for row in rows
    ]


# Groups charging records by provider and calculates totals + avg price per kWh.
# Public endpoint for dashboard insights.
@router.get("/charging/providers")
def get_charging_by_provider(db: Session = Depends(get_db)):
    # Groups charging records by provider and calculates totals + avg price per kWh.
    # Public endpoint for dashboard insights.
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


# Provides monthly trend data for charging (kWh, cost, avg price).
# Uses DB functions for bucketing. Public endpoint.
@router.get("/charging/monthly-trend")
def get_monthly_charging_trend(db: Session = Depends(get_db)):
    # Provides monthly trend data for charging (kWh, cost, avg price).
    # Uses DB functions for bucketing. Public endpoint.
    """Return month-by-month charging totals and average price per kWh.

    Uses Postgres DATE_TRUNC for monthly bucketing. Ordered chronologically.
    Public endpoint.
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


# Returns the latest 10 charging records for display (no auth needed).
# Ordered by charge_date then created_at for stable "most recent" behavior.
# Public recent charging list (last 10). No auth.
@router.get("/charging/recent")
def get_recent_charging_records(db: Session = Depends(get_db)):
    # Public recent charging list (last 10). No auth.
    """Return the 10 most recent charging records (newest first).

    Public endpoint (no API key required). Useful for quick overview on the dashboard.
    """
    query = text("""
        SELECT
            id,
            charge_date,
            provider,
            amount,
            kwh,
            created_at
        FROM charging_records
        ORDER BY charge_date DESC, created_at DESC, id DESC
        LIMIT 10
    """)

    rows = db.execute(query).mappings().all()

    return [
        {
            "id": row["id"],
            "charge_date": row["charge_date"].isoformat() if row["charge_date"] else None,
            "provider": row["provider"],
            "amount": int(row["amount"]) if row["amount"] is not None else 0,
            "kwh": round(float(row["kwh"] or 0), 2),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


# Returns the latest 10 car expenses for display (no auth needed).
# Ordered by date then created_at.
# Public recent car expenses list (last 10). No auth.
@router.get("/expenses/recent")
def get_recent_car_expenses(db: Session = Depends(get_db)):
    # Public recent car expenses list (last 10). No auth.
    """Return the 10 most recent car expense records (newest first).

    Public endpoint (no API key required). Useful for quick overview on the dashboard.
    """
    query = text("""
        SELECT
            id,
            date,
            item,
            amount,
            created_at
        FROM car_expenses
        ORDER BY date DESC, created_at DESC, id DESC
        LIMIT 10
    """)

    rows = db.execute(query).mappings().all()

    return [
        {
            "id": row["id"],
            "date": row["date"].isoformat() if row["date"] else None,
            "item": row["item"],
            "amount": int(row["amount"]) if row["amount"] is not None else 0,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


# Protected endpoint to insert a new charging session record.
# Requires valid x-api-key header.
@router.post("/charging-records")
def create_charging_record(
    payload: ChargingRecordCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    # Protected endpoint to insert a new charging session record.
    # Requires valid x-api-key header.
    """Insert a new charging record (protected).

    Used by iPhone Shortcuts or other trusted clients to log a charge session.
    """
    query = text("""
        INSERT INTO charging_records
            (charge_date, provider, amount, kwh)
        VALUES
            (:charge_date, :provider, :amount, :kwh)
        RETURNING id
    """)

    result = db.execute(
        query,
        {
            "charge_date": payload.charge_date,
            "provider": payload.provider,
            "amount": payload.amount,
            "kwh": payload.kwh,
        },
    )
    db.commit()

    return {
        "status": "success",
        "message": "Charging record created",
        "data": {
            "id": result.scalar_one(),
            "charge_date": payload.charge_date.isoformat(),
            "provider": payload.provider,
            "amount": payload.amount,
            "kwh": payload.kwh,
        },
    }

# Protected endpoint to record a one-time car expense (e.g. maintenance, insurance).
# Requires valid x-api-key.
@router.post("/car-expenses")
def create_car_expense(
    payload: CarExpenseCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    # Protected endpoint to record a one-time car expense.
    # Requires valid x-api-key.
    """Insert a new car expense record (protected).

    Used by Shortcuts etc. to log one-off car costs (tires, insurance, etc.).
    """
    query = text("""
        INSERT INTO car_expenses
            (date, item, amount)
        VALUES
            (:date, :item, :amount)
        RETURNING id
    """)

    result = db.execute(
        query,
        {
            "date": payload.date,
            "item": payload.item,
            "amount": payload.amount,
        },
    )
    db.commit()

    return {
        "status": "success",
        "message": "Car expense created",
        "data": {
            "id": result.scalar_one(),
            "date": payload.date.isoformat(),
            "item": payload.item,
            "amount": payload.amount,
        },
    }