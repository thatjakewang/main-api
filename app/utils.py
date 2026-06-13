"""Shared helpers used across routers.

Keeps routers thin and responses consistent:
- serialize_value / serialize_row : convert raw DB values into JSON-friendly ones
- success_response                : standard envelope for all write (POST) endpoints
- create_record                   : shared INSERT -> commit -> envelope flow for POST endpoints
- fetch_recent                    : shared "10 most recent rows" query for /recent endpoints
- get_today / get_month_start / get_next_month_start / current_month_range : timezone-aware date helpers
- summary_or_http_error / summary_to_plain_text : response shaping for AI summary endpoints
- register_ai_summary_pair        : registers the JSON + plain-text endpoint pair for one AI summary
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import verify_shortcut_api_key


def serialize_value(value):
    """Convert a single DB value into a JSON-friendly Python value.

    - date / datetime -> ISO-8601 string
    - Decimal         -> int when integral, otherwise float rounded to 2 decimals
    - float           -> rounded to 2 decimals
    - everything else (str, int, None) is returned unchanged
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        number = float(value)
        return int(number) if number.is_integer() else round(number, 2)
    if isinstance(value, float):
        return round(value, 2)
    return value


def serialize_row(row) -> dict:
    """Convert one SQLAlchemy RowMapping into a JSON-friendly dict."""
    return {key: serialize_value(value) for key, value in row.items()}


def success_response(message: str, data: dict) -> dict:
    """Standard success envelope returned by all write (POST) endpoints."""
    return {"status": "success", "message": message, "data": data}


def create_record(db: Session, insert_sql: str, payload: BaseModel, message: str) -> dict:
    """Shared body of every write (POST) endpoint.

    Executes an INSERT ... RETURNING statement (parameters come from the
    payload's fields, so the :placeholders must match the model field names),
    commits, and returns the standard success envelope echoing the generated
    columns (id, created_at, ...) plus the submitted payload.
    """
    fields = payload.model_dump()
    returned = db.execute(text(insert_sql), fields).mappings().one()
    db.commit()

    return success_response(
        message,
        {**serialize_row(returned), **{key: serialize_value(value) for key, value in fields.items()}},
    )


def fetch_recent(db: Session, table: str, columns: str, order_col: str = "date") -> list[dict]:
    """Return the 10 most recent rows of a table (newest first), JSON-ready.

    All /recent endpoints share this exact shape: order by the record's date
    column, then created_at, then id as tie-breakers. `table` / `columns` /
    `order_col` are hardcoded by callers (never user input), so building the
    SQL with an f-string is safe here.
    """
    rows = db.execute(text(f"""
        SELECT {columns}
        FROM {table}
        ORDER BY {order_col} DESC, created_at DESC, id DESC
        LIMIT 10
    """)).mappings().all()

    return [serialize_row(row) for row in rows]


def get_today() -> date:
    """Return today's date in the configured APP_TIMEZONE.

    Falls back to Asia/Taipei if the configured timezone is invalid.
    Used to determine 'today' and 'current month' consistently with the user's
    local time rather than server UTC or DB CURRENT_DATE.
    """
    settings = get_settings()
    try:
        timezone = ZoneInfo(settings.app_timezone)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Asia/Taipei")

    return datetime.now(timezone).date()


def get_month_start(target_month: str | None = None) -> date:
    """Compute the first day of a month.

    If target_month (YYYY-MM) is not provided, uses the current month according to
    get_today() (APP_TIMEZONE aware). Validates format and raises 422 on error.
    """
    if not target_month:
        today = get_today()
        return date(today.year, today.month, 1)

    try:
        parsed_month = datetime.strptime(target_month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="target_month must use YYYY-MM format",
        ) from exc

    return date(parsed_month.year, parsed_month.month, 1)


def get_next_month_start(month_start: date) -> date:
    """Return the first day of the month following the given month_start date.

    Handles year rollover for December. Used together with get_month_start to
    build inclusive [start, end) date ranges for queries.
    """
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)

    return date(month_start.year, month_start.month + 1, 1)


def current_month_range() -> tuple[date, date]:
    """Return (month_start, next_month_start) for the current month (APP_TIMEZONE aware).

    Convenience wrapper used by endpoints that query the current month's data
    and need both boundaries at once, eliminating the repeated two-liner.
    """
    month_start = get_month_start()
    return month_start, get_next_month_start(month_start)


def summary_or_http_error(summary: dict) -> dict:
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


def summary_to_plain_text(summary: dict) -> PlainTextResponse:
    """Convert a summary dict from the AI service into a plain-text response.

    Handles the error case gracefully so Shortcuts clients always receive
    readable text instead of a JSON error or a KeyError.
    """
    if summary.get("status") == "error":
        return PlainTextResponse(f"AI summary failed: {summary.get('error', 'unknown error')}")

    return PlainTextResponse(summary["message"])


def register_ai_summary_pair(router, path: str, build_summary, *, by_month: bool = False) -> None:
    """Register the JSON + plain-text endpoint pair for one AI summary type.

    Every AI summary is exposed twice, always protected by x-api-key:
    - GET `path`          : full JSON envelope; AI failures become HTTP 502
    - GET `path`/message  : just the message as plain text, always HTTP 200
                            (so iPhone Shortcuts can forward the body directly)

    `by_month` switches the optional query parameter from target_date
    (YYYY-MM-DD, defaults to today) to target_month (YYYY-MM, defaults to the
    current month). Both interpret dates in APP_TIMEZONE.
    """
    if by_month:
        def resolve_summary(
            target_month: str | None = None,
            db: Session = Depends(get_db),
            _: None = Depends(verify_shortcut_api_key),
        ) -> dict:
            return build_summary(get_month_start(target_month), db)
    else:
        def resolve_summary(
            target_date: date | None = None,
            db: Session = Depends(get_db),
            _: None = Depends(verify_shortcut_api_key),
        ) -> dict:
            return build_summary(target_date or get_today(), db)

    @router.get(path)
    def ai_summary_json(summary: dict = Depends(resolve_summary)):
        """Full AI summary as JSON (protected). AI failures raise HTTP 502."""
        return summary_or_http_error(summary)

    @router.get(f"{path}/message", response_class=PlainTextResponse)
    def ai_summary_message(summary: dict = Depends(resolve_summary)):
        """Only the AI summary message as plain text (protected, for Shortcuts)."""
        return summary_to_plain_text(summary)
