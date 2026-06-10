"""Shared helpers used across routers.

Keeps routers thin and responses consistent:
- serialize_value / serialize_row : convert raw DB values into JSON-friendly ones
- success_response                : standard envelope for all write (POST) endpoints
- get_today / get_month_start / get_next_month_start : timezone-aware date helpers
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from app.config import get_settings


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
