"""Tests for the AI summary service (error envelope, fallbacks, budget context).

The OpenAI client is always mocked — no test makes a network call.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import ai_summary
from app.services.ai_summary import _finalize_summary, get_monthly_budget_context
from tests.conftest import FakeSession

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
