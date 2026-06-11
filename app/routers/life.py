"""Life router - daily personal expenses + AI summaries.

Thin HTTP layer: request validation, auth, and plain DB read endpoints.
All AI summary logic lives in app/services/ai_summary.py; shared serialization,
response envelopes, and date helpers live in app/utils.py.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import verify_shortcut_api_key
from app.services.ai_summary import (
    build_daily_expense_summary,
    build_monthly_expense_summary,
)
from app.utils import (
    get_month_start,
    get_next_month_start,
    get_today,
    serialize_row,
    success_response,
    summary_or_http_error,
    summary_to_plain_text,
)

router = APIRouter()


class DailyExpenseCreate(BaseModel):
    """Payload used when creating a daily expense via the protected POST /expenses endpoint."""
    date: date
    category: str
    amount: int = Field(ge=0)
    payment_method: str | None = None


@router.get("/health")
def life_health_check():
    """Simple health check endpoint for the life (daily expenses) router."""
    return {"status": "life ok"}


@router.post("/expenses")
def create_daily_expense(
    payload: DailyExpenseCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Create a new daily expense record (protected by x-api-key).

    Stores the expense in the daily_expenses table and returns the generated id.
    Requires valid SHORTCUT_API_KEY via the verify dependency.
    """
    query = text("""
        INSERT INTO daily_expenses
            (date, category, amount, payment_method)
        VALUES
            (:date, :category, :amount, :payment_method)
        RETURNING id
    """)

    result = db.execute(
        query,
        {
            "date": payload.date,
            "category": payload.category,
            "amount": payload.amount,
            "payment_method": payload.payment_method,
        },
    )
    db.commit()

    return success_response(
        "Daily expense created",
        {
            "id": result.scalar_one(),
            "date": payload.date.isoformat(),
            "category": payload.category,
            "amount": payload.amount,
            "payment_method": payload.payment_method,
        },
    )


@router.get("/expenses/recent")
def get_recent_daily_expenses(db: Session = Depends(get_db)):
    """Return the 10 most recent daily expense records (newest first).

    Public endpoint (no API key required). Useful for quick overview on the dashboard.
    """
    query = text("""
        SELECT
            id,
            date,
            category,
            amount,
            payment_method,
            created_at
        FROM daily_expenses
        ORDER BY date DESC, created_at DESC
        LIMIT 10
    """)

    rows = db.execute(query).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get("/expenses/summary")
def get_daily_expense_summary(db: Session = Depends(get_db)):
    """Return aggregate total and count of expenses for the current month (APP_TIMEZONE aware).

    Public endpoint. The month boundary uses get_month_start / get_next_month_start
    so it respects the configured timezone instead of the database server's CURRENT_DATE.
    """
    month_start = get_month_start()
    next_month_start = get_next_month_start(month_start)
    month_label = month_start.strftime("%Y-%m")

    query = text("""
        SELECT
            COALESCE(SUM(amount), 0) AS total_amount,
            COUNT(*) AS record_count
        FROM daily_expenses
        WHERE date >= :month_start
          AND date < :next_month_start
    """)

    row = db.execute(
        query, {"month_start": month_start, "next_month_start": next_month_start}
    ).mappings().one()

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
        "total_amount": int(row["total_amount"] or 0),
        "record_count": int(row["record_count"] or 0),
        "prev_month_to_date": int(prev_total or 0),
    }


@router.get("/expenses/category")
def get_expenses_by_category(db: Session = Depends(get_db)):
    """Return current-month expenses grouped by category (highest total first).

    Public endpoint. Uses timezone-aware month boundaries for consistency with
    other summary endpoints.
    """
    month_start = get_month_start()
    next_month_start = get_next_month_start(month_start)

    query = text("""
        SELECT
            category,
            COALESCE(SUM(amount), 0) AS total_amount,
            COUNT(*) AS record_count
        FROM daily_expenses
        WHERE date >= :month_start
          AND date < :next_month_start
        GROUP BY category
        ORDER BY total_amount DESC
    """)

    rows = db.execute(
        query, {"month_start": month_start, "next_month_start": next_month_start}
    ).mappings().all()

    return [
        {
            "category": row["category"],
            "total_amount": int(row["total_amount"] or 0),
            "record_count": int(row["record_count"] or 0),
        }
        for row in rows
    ]


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


@router.get("/expenses/daily-ai-summary")
def get_daily_expense_ai_summary(
    target_date: date | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Generate a daily AI expense summary (JSON) for the given (or today's) date.

    Protected endpoint. The "message" field contains the AI-generated English
    text or a no-record message. AI failures raise HTTP 502.
    """
    report_date = target_date or get_today()
    return summary_or_http_error(build_daily_expense_summary(report_date, db))


@router.get("/expenses/daily-ai-summary/message", response_class=PlainTextResponse)
def get_daily_expense_ai_summary_message(
    target_date: date | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Return only the AI daily summary message as plain text (for iPhone Shortcuts / easy copy).

    Protected endpoint.
    """
    report_date = target_date or get_today()
    return summary_to_plain_text(build_daily_expense_summary(report_date, db))


@router.get("/expenses/monthly-ai-summary")
def get_monthly_expense_ai_summary(
    target_month: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Generate a monthly AI expense summary (JSON) for the given (or current) month.

    Protected endpoint. The "message" will be English text produced by the model
    (or a safe fallback / no-record message). AI failures raise HTTP 502.
    """
    month_start = get_month_start(target_month)
    return summary_or_http_error(build_monthly_expense_summary(month_start, db))


@router.get("/expenses/monthly-ai-summary/message", response_class=PlainTextResponse)
def get_monthly_expense_ai_summary_message(
    target_month: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Return only the AI monthly summary as plain text (ideal for Shortcuts / iMessage).

    Protected endpoint.
    """
    month_start = get_month_start(target_month)
    return summary_to_plain_text(build_monthly_expense_summary(month_start, db))
