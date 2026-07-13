"""Tests for the Life router's date-window logic.

Pins get_today (both in app.utils, used by current_month_range, and the copy
imported into the life router) so month boundaries are deterministic. The AI
summary endpoints are covered by test_endpoints.py / test_ai_summary.py.
"""

from datetime import date
from decimal import Decimal

import pytest

from app import utils
from app.routers import life
from tests.conftest import TEST_API_KEY, FakeResult, FakeSession


@pytest.fixture
def fixed_today(monkeypatch):
    """Return a setter that pins 'today' for utils and the life router."""

    def set_today(value: date) -> None:
        monkeypatch.setattr(utils, "get_today", lambda: value)
        monkeypatch.setattr(life, "get_today", lambda: value)

    return set_today


class TestMonthlySummaryWindow:
    def make_session(self):
        return FakeSession(results=[
            FakeResult(rows=[{"total_amount": 5000, "record_count": 10}]),
            FakeResult(rows=[{"total_amount": 4000, "record_count": 8}]),
        ])

    def test_mid_month_compares_same_number_of_days(self, client_for, fixed_today):
        fixed_today(date(2026, 7, 10))
        session = self.make_session()
        body = client_for(session).get("/api/life/expenses/summary").json()

        assert body == {"month": "2026-07", "total_amount": 5000,
                        "record_count": 10, "prev_month_to_date": 4000}
        assert session.calls[0][1] == {
            "month_start": date(2026, 7, 1), "next_month_start": date(2026, 8, 1),
        }
        # previous-month window covers the same 10 elapsed days: [Jun 1, Jun 11)
        assert session.calls[1][1] == {
            "month_start": date(2026, 6, 1), "next_month_start": date(2026, 6, 11),
        }

    def test_late_month_window_clamps_to_shorter_previous_month(self, client_for, fixed_today):
        fixed_today(date(2026, 3, 30))  # 30 days elapsed; Feb 2026 has only 28
        session = self.make_session()
        client_for(session).get("/api/life/expenses/summary")

        assert session.calls[1][1] == {
            "month_start": date(2026, 2, 1), "next_month_start": date(2026, 3, 1),
        }


class TestDailySeriesWindow:
    def test_long_history_uses_trailing_90_days(self, client_for, fixed_today):
        fixed_today(date(2026, 7, 10))
        session = FakeSession(results=[
            FakeResult(scalar_value=date(2026, 1, 1)),  # MIN(date): long history
            FakeResult(rows=[{"date": date(2026, 7, 1), "total_amount": 300}]),
        ])
        body = client_for(session).get("/api/life/expenses/daily").json()

        assert body == [{"date": "2026-07-01", "total_amount": 300}]
        assert session.calls[1][1] == {"start": date(2026, 4, 12), "end": date(2026, 7, 10)}

    def test_short_history_starts_at_first_record(self, client_for, fixed_today):
        fixed_today(date(2026, 7, 10))
        session = FakeSession(results=[
            FakeResult(scalar_value=date(2026, 6, 20)),
            FakeResult(rows=[]),
        ])
        assert client_for(session).get("/api/life/expenses/daily").json() == []
        assert session.calls[1][1] == {"start": date(2026, 6, 20), "end": date(2026, 7, 10)}

    def test_empty_table_still_uses_trailing_window(self, client_for, fixed_today):
        fixed_today(date(2026, 7, 10))
        session = FakeSession(results=[FakeResult(scalar_value=None), FakeResult(rows=[])])
        assert client_for(session).get("/api/life/expenses/daily").json() == []
        assert session.calls[1][1] == {"start": date(2026, 4, 12), "end": date(2026, 7, 10)}


class TestWrites:
    def test_create_daily_expense_envelope(self, client_for):
        session = FakeSession(results=[FakeResult(rows=[{"id": 42}])])
        response = client_for(session).post(
            "/api/life/expenses",
            headers={"x-api-key": TEST_API_KEY},
            json={"date": "2026-07-06", "category": "Food", "amount": 120},
        )
        assert response.status_code == 200
        assert response.json() == {
            "status": "success",
            "message": "Daily expense created",
            "data": {"id": 42, "date": "2026-07-06", "category": "Food", "amount": 120},
        }


class TestCategoryEndpoint:
    def test_current_month_bounds_and_row_shape(self, client_for, fixed_today):
        fixed_today(date(2026, 7, 10))
        # Postgres SUM comes back as Decimal; expense_categories must emit ints.
        session = FakeSession(rows=[
            {"category": "Food", "total_amount": Decimal("4500"), "record_count": 15},
        ])
        body = client_for(session).get("/api/life/expenses/category").json()

        assert body == [{"category": "Food", "total_amount": 4500, "record_count": 15}]
        assert session.calls[0][1] == {
            "month_start": date(2026, 7, 1), "next_month_start": date(2026, 8, 1),
        }


class TestCategoryAllTimeScope:
    def test_scope_all_drops_date_filter(self, client_for):
        session = FakeSession(rows=[
            {"category": "Food", "total_amount": Decimal("98000"), "record_count": 400},
        ])
        body = client_for(session).get("/api/life/expenses/category?scope=all").json()

        assert body == [{"category": "Food", "total_amount": 98000, "record_count": 400}]
        # no date bounds are bound to the query
        assert session.calls[0][1] == {}
        assert "WHERE" not in str(session.calls[0][0])

    def test_invalid_scope_is_rejected(self, client_for):
        response = client_for(FakeSession(rows=[])).get(
            "/api/life/expenses/category?scope=year"
        )
        assert response.status_code == 422


class TestCategoryMonthlyEndpoint:
    def test_window_starts_12_months_back_and_rows_serialize(self, client_for, fixed_today):
        fixed_today(date(2026, 7, 10))
        session = FakeSession(rows=[
            {"month": "2026-06", "category": "Food", "total_amount": Decimal("4500")},
            {"month": "2026-07", "category": "Drinks", "total_amount": Decimal("800")},
        ])
        body = client_for(session).get("/api/life/expenses/category/monthly").json()

        assert body == [
            {"month": "2026-06", "category": "Food", "total_amount": 4500},
            {"month": "2026-07", "category": "Drinks", "total_amount": 800},
        ]
        # current month (2026-07) plus the 11 before it -> starts 2025-08-01
        assert session.calls[0][1] == {"start": date(2025, 8, 1)}

    def test_window_handles_year_rollover(self, client_for, fixed_today):
        fixed_today(date(2026, 12, 5))  # month - 11 = 1, no rollover needed
        session = FakeSession(rows=[])
        client_for(session).get("/api/life/expenses/category/monthly")
        assert session.calls[0][1] == {"start": date(2026, 1, 1)}

    def test_window_rolls_back_into_previous_year(self, client_for, fixed_today):
        fixed_today(date(2026, 3, 15))
        session = FakeSession(rows=[])
        client_for(session).get("/api/life/expenses/category/monthly")
        assert session.calls[0][1] == {"start": date(2025, 4, 1)}


class TestRecentEndpoint:
    def test_rows_are_serialized(self, client_for):
        session = FakeSession(rows=[
            {"id": 2, "date": date(2026, 7, 6), "category": "Food", "amount": 120},
        ])
        body = client_for(session).get("/api/life/expenses/recent").json()
        assert body == [
            {"id": 2, "date": "2026-07-06", "category": "Food", "amount": 120},
        ]
