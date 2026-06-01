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
    charge_date: date
    provider: str
    amount: int = Field(ge=0)
    kwh: float = Field(ge=0)


class CarExpenseCreate(BaseModel):
    date: date
    item: str
    amount: int = Field(ge=0)

@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
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


@router.get("/expenses")
def get_expenses(db: Session = Depends(get_db)):
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


@router.get("/charging/providers")
def get_charging_by_provider(db: Session = Depends(get_db)):
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

@router.post("/charging-records")
def create_charging_record(
    payload: ChargingRecordCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    query = text("""
        INSERT INTO charging_records
            (charge_date, provider, amount, kwh)
        VALUES
            (:charge_date, :provider, :amount, :kwh)
    """)

    db.execute(
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
            "charge_date": payload.charge_date.isoformat(),
            "provider": payload.provider,
            "amount": payload.amount,
            "kwh": payload.kwh,
        },
    }

@router.post("/car-expenses")
def create_car_expense(
    payload: CarExpenseCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    query = text("""
        INSERT INTO car_expenses
            (date, item, amount)
        VALUES
            (:date, :item, :amount)
    """)

    db.execute(
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
            "date": payload.date.isoformat(),
            "item": payload.item,
            "amount": payload.amount,
        },
    }