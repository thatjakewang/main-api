"""Shared DB query helpers for daily_expenses.

Both the dashboard router (life.py) and the AI summary service (ai_summary.py)
need the same three query shapes over a date range.  Defining them once here
ensures the two callers can never silently diverge in what "monthly expense"
means.

All three helpers accept the same (db, month_start, next_month_start) signature
and return plain dicts/lists — no SQLAlchemy objects leak out.
"""

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session


def expense_totals(db: Session, month_start: date, next_month_start: date) -> dict:
    """Return total_amount and record_count for [month_start, next_month_start)."""
    row = db.execute(
        text("""
            SELECT
                COALESCE(SUM(amount), 0) AS total_amount,
                COUNT(*) AS record_count
            FROM daily_expenses
            WHERE date >= :month_start
              AND date < :next_month_start
        """),
        {"month_start": month_start, "next_month_start": next_month_start},
    ).mappings().one()
    return {
        "total_amount": int(row["total_amount"] or 0),
        "record_count": int(row["record_count"] or 0),
    }


def expense_categories(db: Session, month_start: date, next_month_start: date) -> list[dict]:
    """Return per-category totals for [month_start, next_month_start), highest first."""
    rows = db.execute(
        text("""
            SELECT
                category,
                COALESCE(SUM(amount), 0) AS total_amount,
                COUNT(*) AS record_count
            FROM daily_expenses
            WHERE date >= :month_start
              AND date < :next_month_start
            GROUP BY category
            ORDER BY total_amount DESC
        """),
        {"month_start": month_start, "next_month_start": next_month_start},
    ).mappings().all()
    return [
        {
            "category": row["category"],
            "total_amount": int(row["total_amount"] or 0),
            "record_count": int(row["record_count"] or 0),
        }
        for row in rows
    ]


def expense_daily_totals(db: Session, month_start: date, next_month_start: date) -> list[dict]:
    """Return per-day totals for [month_start, next_month_start), ordered by date."""
    rows = db.execute(
        text("""
            SELECT
                date,
                COALESCE(SUM(amount), 0) AS total_amount,
                COUNT(*) AS record_count
            FROM daily_expenses
            WHERE date >= :month_start
              AND date < :next_month_start
            GROUP BY date
            ORDER BY date
        """),
        {"month_start": month_start, "next_month_start": next_month_start},
    ).mappings().all()
    return [
        {
            "date": row["date"].isoformat(),
            "total_amount": int(row["total_amount"] or 0),
            "record_count": int(row["record_count"] or 0),
        }
        for row in rows
    ]
