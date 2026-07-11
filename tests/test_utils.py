"""Tests for the pure helpers in app/utils.py (dates, serialization, envelopes)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.utils import (
    current_month_range,
    get_month_start,
    get_next_month_start,
    get_today,
    serialize_row,
    serialize_value,
    summary_or_http_error,
    summary_to_plain_text,
)


class TestDateHelpers:
    def test_month_start_from_string(self):
        assert get_month_start("2026-02") == date(2026, 2, 1)

    def test_month_start_defaults_to_current_month(self):
        today = get_today()
        assert get_month_start(None) == date(today.year, today.month, 1)

    @pytest.mark.parametrize("bad", ["2026", "2026-13", "02-2026", "abc", "2026-02-01"])
    def test_month_start_rejects_bad_format(self, bad):
        with pytest.raises(HTTPException) as exc_info:
            get_month_start(bad)
        assert exc_info.value.status_code == 422

    def test_next_month_start_normal(self):
        assert get_next_month_start(date(2026, 7, 1)) == date(2026, 8, 1)

    def test_next_month_start_year_rollover(self):
        assert get_next_month_start(date(2026, 12, 1)) == date(2027, 1, 1)

    def test_current_month_range_is_consistent(self):
        month_start, next_month_start = current_month_range()
        assert month_start.day == 1
        assert get_next_month_start(month_start) == next_month_start

    def test_get_today_survives_invalid_timezone(self, monkeypatch):
        from zoneinfo import ZoneInfo

        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "app_timezone", "Not/AZone")
        before = datetime.now(ZoneInfo("Asia/Taipei")).date()
        result = get_today()  # must fall back to Asia/Taipei, not raise
        after = datetime.now(ZoneInfo("Asia/Taipei")).date()
        assert result in {before, after}


class TestSerialization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (date(2026, 7, 6), "2026-07-06"),
            (datetime(2026, 7, 6, 12, 30), "2026-07-06T12:30:00"),
            (Decimal("10"), 10),
            (Decimal("10.25"), 10.25),
            (10.256, 10.26),
            ("text", "text"),
            (42, 42),
            (None, None),
        ],
    )
    def test_serialize_value(self, raw, expected):
        assert serialize_value(raw) == expected

    def test_integral_decimal_becomes_int(self):
        assert isinstance(serialize_value(Decimal("10")), int)

    def test_serialize_row(self):
        row = {"d": date(2026, 1, 2), "amount": Decimal("3.5")}
        assert serialize_row(row) == {"d": "2026-01-02", "amount": 3.5}


class TestSummaryShaping:
    def test_success_summary_passes_through(self):
        summary = {"status": "success", "message": "hi"}
        assert summary_or_http_error(summary) is summary

    def test_error_summary_becomes_502(self):
        with pytest.raises(HTTPException) as exc_info:
            summary_or_http_error({"status": "error", "error": "boom"})
        assert exc_info.value.status_code == 502
        assert "boom" in exc_info.value.detail

    def test_plain_text_success(self):
        response = summary_to_plain_text({"status": "success", "message": "hello"})
        assert response.status_code == 200
        assert response.body == b"hello"

    def test_plain_text_error_stays_200(self):
        response = summary_to_plain_text({"status": "error", "error": "boom"})
        assert response.status_code == 200
        assert b"boom" in response.body
