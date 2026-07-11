"""Tests for the AI summary service (error envelope, fallbacks, budget context).

The OpenAI client is always mocked — no test makes a network call.
"""

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import ai_summary
from app.services.ai_summary import _finalize_summary, get_monthly_budget_context
from tests.conftest import FakeResult, FakeSession

FINALIZE_DEFAULTS = dict(
    label={"date": "2026-07-06"},
    data={},
    empty_message="No expenses recorded today.",
    payload={"total": 1},
    instructions="test",
    max_output_tokens=100,
    fallback_message="fallback",
)


def fake_client(output_text="summary text"):
    """An OpenAI client double whose responses.create returns the given text."""
    response = SimpleNamespace(output_text=output_text)
    return SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: response))


class TestFinalizeSummary:
    def test_empty_period_short_circuits_without_openai(self, monkeypatch):
        def explode():
            raise AssertionError("OpenAI must not be called for empty periods")

        monkeypatch.setattr(ai_summary, "create_openai_client", explode)
        result = _finalize_summary(FakeSession(), record_count=0, **FINALIZE_DEFAULTS)
        assert result["status"] == "success"
        assert result["message"] == "No expenses recorded today."

    def test_success_uses_model_output(self, monkeypatch):
        monkeypatch.setattr(ai_summary, "create_openai_client", lambda: fake_client("  hi  "))
        result = _finalize_summary(FakeSession(), record_count=3, **FINALIZE_DEFAULTS)
        assert result == {
            "status": "success",
            "date": "2026-07-06",
            "message": "hi",
            "data": {},
        }

    def test_blank_output_falls_back(self, monkeypatch):
        monkeypatch.setattr(ai_summary, "create_openai_client", lambda: fake_client("   "))
        result = _finalize_summary(FakeSession(), record_count=3, **FINALIZE_DEFAULTS)
        assert result["message"] == "fallback"

    def test_config_error_detail_is_surfaced(self, monkeypatch):
        def raise_config_error():
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

        monkeypatch.setattr(ai_summary, "create_openai_client", raise_config_error)
        result = _finalize_summary(FakeSession(), record_count=3, **FINALIZE_DEFAULTS)
        assert result["status"] == "error"
        assert result["error"] == "OPENAI_API_KEY is not configured"

    def test_unexpected_error_is_generic(self, monkeypatch):
        def raise_unexpected():
            raise ValueError("secret internal detail")

        monkeypatch.setattr(ai_summary, "create_openai_client", raise_unexpected)
        result = _finalize_summary(FakeSession(), record_count=3, **FINALIZE_DEFAULTS)
        assert result["status"] == "error"
        assert result["error"] == "AI service error, see server logs"
        assert "secret" not in result["error"]

    def test_db_session_released_before_openai_call(self, monkeypatch):
        session = FakeSession()

        def assert_session_already_closed():
            assert session.closed, "DB session must be closed before the OpenAI call"
            return fake_client()

        monkeypatch.setattr(ai_summary, "create_openai_client", assert_session_already_closed)
        _finalize_summary(session, record_count=3, **FINALIZE_DEFAULTS)
        assert session.closed


class TestOpenAIClient:
    @pytest.fixture(autouse=True)
    def fresh_client_cache(self):
        # The client is lru_cached per process; isolate it from other tests.
        ai_summary.create_openai_client.cache_clear()
        yield
        ai_summary.create_openai_client.cache_clear()

    def test_missing_key_is_config_error(self, monkeypatch):
        monkeypatch.setattr(ai_summary.settings, "openai_api_key", None)
        with pytest.raises(HTTPException) as exc_info:
            ai_summary.create_openai_client()
        assert exc_info.value.status_code == 500
        assert "OPENAI_API_KEY" in exc_info.value.detail

    def test_failure_is_not_cached(self, monkeypatch):
        monkeypatch.setattr(ai_summary.settings, "openai_api_key", None)
        with pytest.raises(HTTPException):
            ai_summary.create_openai_client()

        # Fixing the key must take effect immediately (lru_cache never caches raises).
        monkeypatch.setattr(ai_summary.settings, "openai_api_key", "sk-test")
        client = ai_summary.create_openai_client()
        assert client.api_key == "sk-test"
        assert client.timeout == 30.0  # bounds how long a hung call holds a thread


class TestDailyBuilder:
    def test_queries_payload_and_serialization(self, monkeypatch):
        """One pass over build_daily_expense_summary's full orchestration:
        query params, Decimal/date cleanup, and the exact prompt contract."""
        captured = {}

        def recording_client():
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(output_text="ai says hi")

            return SimpleNamespace(responses=SimpleNamespace(create=create))

        monkeypatch.setattr(ai_summary, "create_openai_client", recording_client)
        # Postgres aggregates arrive as Decimals and dates, never plain ints/strings.
        session = FakeSession(results=[
            FakeResult(rows=[{"total_amount": Decimal("850"), "record_count": 3}]),
            FakeResult(rows=[
                {"category": "Food", "total_amount": Decimal("450"), "record_count": 2},
                {"category": "Drinks", "total_amount": Decimal("400"), "record_count": 1},
            ]),
            FakeResult(rows=[
                {"date": date(2026, 7, 5), "total_amount": Decimal("600"), "record_count": 4},
                {"date": date(2026, 7, 6), "total_amount": Decimal("850"), "record_count": 3},
            ]),
        ])

        result = ai_summary.build_daily_expense_summary(date(2026, 7, 6), session)

        assert result["status"] == "success"
        assert result["message"] == "ai says hi"
        assert result["data"]["total_amount"] == 850
        assert result["data"]["categories"][0] == {
            "category": "Food", "total_amount": 450, "record_count": 2,
        }
        assert result["data"]["recent_days"][0]["date"] == "2026-07-05"

        # All three queries bind the same target date.
        assert all(call[1] == {"target_date": date(2026, 7, 6)} for call in session.calls)

        # The prompt contract: model, instructions, token cap, JSON-clean input.
        assert captured["model"] == ai_summary.settings.openai_model
        assert captured["instructions"] is ai_summary.DAILY_INSTRUCTIONS
        assert captured["max_output_tokens"] == 280
        prompt = json.loads(captured["input"])
        assert prompt["date"] == "2026-07-06"
        assert prompt["currency"] == "TWD"
        assert prompt["today"]["total_amount"] == 850
        assert [day["date"] for day in prompt["recent_days"]] == ["2026-07-05", "2026-07-06"]


class TestBudgetContext:
    def test_no_income_configured(self, monkeypatch):
        monkeypatch.setattr(ai_summary.settings, "monthly_income", None)
        monkeypatch.setattr(ai_summary.settings, "monthly_fixed_expenses", None)
        context = get_monthly_budget_context(30000)
        assert context["monthly_income_configured"] is False
        assert context["disposable_used_ratio"] is None

    def test_income_and_fixed_expenses(self, monkeypatch):
        monkeypatch.setattr(ai_summary.settings, "monthly_income", 80000)
        monkeypatch.setattr(ai_summary.settings, "monthly_fixed_expenses", 35000)
        context = get_monthly_budget_context(30000)
        assert context["disposable_income"] == 45000
        assert context["disposable_remaining"] == 15000
        assert context["disposable_used_ratio"] == 66.7

    def test_zero_disposable_income_avoids_division(self, monkeypatch):
        monkeypatch.setattr(ai_summary.settings, "monthly_income", 35000)
        monkeypatch.setattr(ai_summary.settings, "monthly_fixed_expenses", 35000)
        context = get_monthly_budget_context(1000)
        assert context["disposable_income"] == 0
        assert context["disposable_used_ratio"] is None
