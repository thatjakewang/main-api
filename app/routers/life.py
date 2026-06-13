"""Life router - daily personal expenses + AI summaries.

Thin HTTP layer: request validation, auth, and plain DB read endpoints.
All AI summary logic lives in app/services/ai_summary.py; shared serialization,
response envelopes, write/recent helpers, and date helpers live in app/utils.py.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import verify_shortcut_api_key
from app.services.ai_summary import (
    build_daily_expense_summary,
    build_monthly_expense_summary,
)
from app.services.expense_stats import expense_categories, expense_totals
from app.utils import (
    create_record,
    current_month_range,
    fetch_recent,
    get_month_start,
    get_next_month_start,
    get_today,
    register_ai_summary_pair,
)

router = APIRouter()


class DailyExpenseCreate(BaseModel):
    """Payload used when creating a daily expense via the protected POST /expenses endpoint."""
    date: date
    category: str
    amount: int = Field(ge=0)
    payment_method: str | None = None


@router.post("/expenses")
def create_daily_expense(
    payload: DailyExpenseCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Create a new daily expense record (protected by x-api-key)."""
    return create_record(
        db,
        """
        INSERT INTO daily_expenses (date, category, amount, payment_method)
        VALUES (:date, :category, :amount, :payment_method)
        RETURNING id
        """,
        payload,
        "Daily expense created",
    )


@router.get("/expenses/recent")
def get_recent_daily_expenses(db: Session = Depends(get_db)):
    """Return the 10 most recent daily expense records (newest first). Public."""
    return fetch_recent(db, "daily_expenses", "id, date, category, amount, payment_method, created_at")


@router.get("/expenses/summary")
def get_daily_expense_summary(db: Session = Depends(get_db)):
    """Return aggregate total and count of expenses for the current month (APP_TIMEZONE aware).

    Public endpoint. The month boundary uses get_month_start / get_next_month_start
    so it respects the configured timezone instead of the database server's CURRENT_DATE.
    """
    month_start, next_month_start = current_month_range()
    month_label = month_start.strftime("%Y-%m")
    totals = expense_totals(db, month_start, next_month_start)

    # Same-period total for the previous month (1st through the same day
    # number, clamped to the previous month's length) so the dashboard can
    # show a fair month-over-month comparison mid-month.
    today = get_today()
    days_elapsed = (today - month_start).days + 1  # incl. today
    prev_month_start = get_month_start(
        (month_start - timedelta(days=1)).strftime("%Y-%m")
    )
    prev_period_end = min(
        prev_month_start + timedelta(days=days_elapsed),
        get_next_month_start(prev_month_start),
    )

    prev_query = text("""
        SELECT COALESCE(SUM(amount), 0) AS total_amount
        FROM daily_expenses
        WHERE date >= :start
          AND date < :end
    """)
    prev_total = db.execute(
        prev_query, {"start": prev_month_start, "end": prev_period_end}
    ).scalar()

    return {
        "month": month_label,
        "total_amount": totals["total_amount"],
        "record_count": totals["record_count"],
        "prev_month_to_date": int(prev_total or 0),
    }


@router.get("/expenses/category")
def get_expenses_by_category(db: Session = Depends(get_db)):
    """Return current-month expenses grouped by category (highest total first).

    Public endpoint. Uses timezone-aware month boundaries for consistency with
    other summary endpoints.
    """
    month_start, next_month_start = current_month_range()
    return expense_categories(db, month_start, next_month_start)


@router.get("/expenses/monthly")
def get_monthly_expenses(db: Session = Depends(get_db)):
    """Return month-by-month spending for the last 12 months, with a
    per-category breakdown for each month (public).

    Covers the current month plus the 11 before it (timezone-aware month
    boundaries), ordered chronologically. Months with no records are simply
    absent. Powers the stacked Monthly Spending chart on the MyLife dashboard.
    """
    month_start = get_month_start()
    # First day of the month 11 months before the current one
    year, month = month_start.year, month_start.month - 11
    if month <= 0:
        year, month = year - 1, month + 12
    start = month_start.replace(year=year, month=month)

    query = text("""
        SELECT
            TO_CHAR(DATE_TRUNC('month', date), 'YYYY-MM') AS month,
            category,
            COALESCE(SUM(amount), 0) AS total_amount,
            COUNT(*) AS record_count
        FROM daily_expenses
        WHERE date >= :start
        GROUP BY DATE_TRUNC('month', date), category
        ORDER BY DATE_TRUNC('month', date)
    """)
    rows = db.execute(query, {"start": start}).mappings().all()

    # Fold category rows into one entry per month
    months: dict[str, dict] = {}
    for row in rows:
        entry = months.setdefault(
            row["month"],
            {"month": row["month"], "total_amount": 0, "record_count": 0, "categories": {}},
        )
        amount = int(row["total_amount"] or 0)
        entry["total_amount"] += amount
        entry["record_count"] += int(row["record_count"] or 0)
        entry["categories"][row["category"]] = amount

    return list(months.values())


@router.get("/expenses/weekday")
def get_expenses_by_weekday(db: Session = Depends(get_db)):
    """Return average daily spending per weekday over the last 12 weeks (public).

    The window is exactly 84 days ending today, so every weekday occurs
    exactly 12 times and the averages are directly comparable. All seven
    weekdays are always returned (zeros included), Monday first.
    """
    start = get_today() - timedelta(days=83)

    query = text("""
        SELECT
            EXTRACT(ISODOW FROM date)::int AS weekday,
            COALESCE(SUM(amount), 0) AS total_amount
        FROM daily_expenses
        WHERE date >= :start
        GROUP BY EXTRACT(ISODOW FROM date)
    """)
    rows = db.execute(query, {"start": start}).mappings().all()
    totals = {int(row["weekday"]): int(row["total_amount"] or 0) for row in rows}

    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [
        {
            "weekday": dow,
            "label": labels[dow - 1],
            "total_amount": totals.get(dow, 0),
            "avg_amount": round(totals.get(dow, 0) / 12),
        }
        for dow in range(1, 8)
    ]


# AI summaries: each call registers the JSON endpoint plus its /message
# plain-text twin (both protected by x-api-key). See utils.register_ai_summary_pair.
register_ai_summary_pair(router, "/expenses/daily-ai-summary", build_daily_expense_summary)
register_ai_summary_pair(
    router, "/expenses/monthly-ai-summary", build_monthly_expense_summary, by_month=True
)
