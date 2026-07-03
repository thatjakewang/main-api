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
    category: str = Field(min_length=1, max_length=100)
    amount: int = Field(ge=0)


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
        INSERT INTO daily_expenses (date, category, amount)
        VALUES (:date, :category, :amount)
        RETURNING id
        """,
        payload,
        "Daily expense created",
    )


@router.get("/expenses/recent")
def get_recent_daily_expenses(db: Session = Depends(get_db)):
    """Return the 10 most recent daily expense records (newest first). Public."""
    return fetch_recent(db, "daily_expenses", "id, date, category, amount")


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


@router.get("/expenses/daily")
def get_daily_expenses(db: Session = Depends(get_db)):
    """Return daily total spending for a trailing 90-day window ending today (public).

    Hybrid window: normally the last 90 days ending today (timezone-aware via
    get_today), but while the data spans less than 90 days the window starts at
    the first record instead, so the chart never shows empty leading days.
    Days with no expenses are returned with a zero total — generate_series
    produces the full date range and a LEFT JOIN fills the gaps — so the line
    chart stays continuous instead of skipping empty days. Powers the Daily
    Spending chart on the MyLife dashboard.
    """
    today = get_today()
    first = db.execute(text("SELECT MIN(date) FROM daily_expenses")).scalar()

    trailing_start = today - timedelta(days=89)
    start = max(first, trailing_start) if first else trailing_start
    end = today

    query = text("""
        SELECT
            day::date AS date,
            COALESCE(SUM(e.amount), 0) AS total_amount
        FROM generate_series(:start, :end, INTERVAL '1 day') AS day
        LEFT JOIN daily_expenses e ON e.date = day::date
        GROUP BY day
        ORDER BY day
    """)
    rows = db.execute(query, {"start": start, "end": end}).mappings().all()

    return [
        {"date": row["date"].isoformat(), "total_amount": int(row["total_amount"] or 0)}
        for row in rows
    ]


# AI summaries: each call registers the JSON endpoint plus its /message
# plain-text twin (both protected by x-api-key). See utils.register_ai_summary_pair.
register_ai_summary_pair(router, "/expenses/daily-ai-summary", build_daily_expense_summary)
register_ai_summary_pair(
    router, "/expenses/monthly-ai-summary", build_monthly_expense_summary, by_month=True
)
