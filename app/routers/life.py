"""Life router - daily personal expenses + AI summaries.

Thin HTTP layer: request validation, auth, and plain DB read endpoints.
All AI summary logic lives in app/services/ai_summary.py; shared serialization,
response envelopes, and date helpers live in app/utils.py.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
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
)

router = APIRouter()


class DailyExpenseCreate(BaseModel):
    """Payload used when creating a daily expense via the protected POST /expenses endpoint."""
    date: date
    category: str
    amount: int = Field(ge=0)
    payment_method: str | None = None


def _summary_or_http_error(summary: dict) -> dict:
    """Enforce the JSON error convention for AI summary endpoints.

    JSON endpoints follow standard HTTP semantics: an AI failure becomes a 502
    (Bad Gateway, upstream AI provider failed) instead of a 200 with an embedded
    error. The /message endpoints keep returning HTTP 200 readable text so
    iPhone Shortcuts can forward the body directly.
    """
    if summary.get("status") == "error":
        raise HTTPException(
            status_code=502,
            detail=f"AI summary failed: {summary.get('error', 'unknown error')}",
        )

    return summary


def _summary_to_plain_text(summary: dict) -> PlainTextResponse:
    """Convert a summary dict from the AI service into a plain-text response.

    Handles the error case gracefully so Shortcuts clients always receive
    readable text instead of a JSON error or a KeyError.
    """
    if summary.get("status") == "error":
        return PlainTextResponse(f"AI summary failed: {summary.get('error', 'unknown error')}")

    return PlainTextResponse(summary["message"])


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

    return {
        "month": month_label,
        "total_amount": int(row["total_amount"] or 0),
        "record_count": int(row["record_count"] or 0),
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
    return _summary_or_http_error(build_daily_expense_summary(report_date, db))


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
    return _summary_to_plain_text(build_daily_expense_summary(report_date, db))


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
    return _summary_or_http_error(build_monthly_expense_summary(month_start, db))


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
    return _summary_to_plain_text(build_monthly_expense_summary(month_start, db))
