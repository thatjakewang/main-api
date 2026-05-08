from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    expense_query = text("""
        SELECT
            COALESCE(SUM(amount), 0) AS total_cost
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

    total_cost = float(expense["total_cost"])
    charging_cost = float(charging["charging_cost"])
    energy_kwh = float(charging["energy_kwh"])

    return {
        "total_cost": total_cost,
        "charging_cost": charging_cost,
        "energy_kwh": round(energy_kwh, 2),
        "avg_price_per_kwh": round(charging_cost / energy_kwh, 2) if energy_kwh else 0,
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