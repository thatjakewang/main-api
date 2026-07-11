"""HTTP-level tests: health, auth, cache headers, CORS, gzip, rate limiting.

All DB access goes through FakeSession — no real database is involved.
TestRateLimit stays last in the file: it deliberately exhausts the per-path
budget for "/", and the limiter's in-memory window spans the whole test run.
"""

from datetime import date

from app.main import PUBLIC_CACHE_MAX_AGE
from tests.conftest import TEST_API_KEY, FakeResult, FakeSession


class TestHealth:
    def test_health_ok_when_db_reachable(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}

    def test_health_503_when_db_down(self, client_for):
        client = client_for(FakeSession(execute_error=RuntimeError("connection refused")))
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unreachable"

    def test_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestAuth:
    PROTECTED_PATH = "/api/life/expenses/daily-ai-summary"

    def test_missing_api_key_is_401_not_422(self, client):
        response = client.get(self.PROTECTED_PATH)
        assert response.status_code == 401

    def test_wrong_api_key_is_401(self, client):
        response = client.get(self.PROTECTED_PATH, headers={"x-api-key": "nope"})
        assert response.status_code == 401

    def test_post_without_key_is_401(self, client):
        response = client.post(
            "/api/life/expenses",
            json={"date": "2026-07-06", "category": "Food", "amount": 100},
        )
        assert response.status_code == 401

    def test_correct_key_reaches_handler(self, client_for):
        # Three queries run for the daily summary: totals (one), categories (all),
        # recent days (all). Zero records short-circuits before any OpenAI call.
        session = FakeSession(results=[
            FakeResult(rows=[{"total_amount": 0, "record_count": 0}]),
            FakeResult(rows=[]),
            FakeResult(rows=[]),
        ])
        client = client_for(session)
        response = client.get(self.PROTECTED_PATH, headers={"x-api-key": TEST_API_KEY})
        assert response.status_code == 200
        assert response.json()["message"] == "No expenses recorded today."

    def test_message_twin_returns_plain_text(self, client_for):
        session = FakeSession(results=[
            FakeResult(rows=[{"total_amount": 0, "record_count": 0}]),
            FakeResult(rows=[]),
            FakeResult(rows=[]),
        ])
        client = client_for(session)
        response = client.get(
            f"{self.PROTECTED_PATH}/message", headers={"x-api-key": TEST_API_KEY}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text == "No expenses recorded today."


class TestCacheHeaders:
    def test_public_api_get_is_cacheable(self, client):
        response = client.get("/api/life/expenses/recent")
        assert response.status_code == 200
        assert response.json() == []
        assert (
            response.headers["Cache-Control"]
            == f"public, max-age={PUBLIC_CACHE_MAX_AGE}"
        )

    def test_health_is_not_cacheable(self, client):
        response = client.get("/health")
        assert "Cache-Control" not in response.headers

    def test_keyed_request_is_not_publicly_cacheable(self, client_for):
        session = FakeSession(results=[
            FakeResult(rows=[{"total_amount": 0, "record_count": 0}]),
            FakeResult(rows=[]),
            FakeResult(rows=[]),
        ])
        client = client_for(session)
        response = client.get(
            "/api/life/expenses/daily-ai-summary", headers={"x-api-key": TEST_API_KEY}
        )
        assert response.status_code == 200
        assert "Cache-Control" not in response.headers

    def test_error_responses_are_not_cacheable(self, client):
        response = client.get("/api/life/nope")
        assert response.status_code == 404
        assert "Cache-Control" not in response.headers


class TestMonthlyAISummary:
    """Endpoint-level coverage of register_ai_summary_pair's by_month branch."""

    def make_session(self):
        # Three queries: totals (one), categories (all), daily totals (all).
        return FakeSession(results=[
            FakeResult(rows=[{"total_amount": 0, "record_count": 0}]),
            FakeResult(rows=[]),
            FakeResult(rows=[]),
        ])

    def test_empty_month_short_circuits(self, client_for):
        session = self.make_session()
        response = client_for(session).get(
            "/api/life/expenses/monthly-ai-summary?target_month=2026-06",
            headers={"x-api-key": TEST_API_KEY},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["month"] == "2026-06"
        assert body["message"] == "No expenses recorded this month."
        assert "budget" in body["data"]
        # target_month drives the queried range, not today's month
        assert session.calls[0][1] == {
            "month_start": date(2026, 6, 1), "next_month_start": date(2026, 7, 1),
        }

    def test_bad_target_month_is_422(self, client):
        response = client.get(
            "/api/life/expenses/monthly-ai-summary?target_month=2026-13",
            headers={"x-api-key": TEST_API_KEY},
        )
        assert response.status_code == 422
        assert "YYYY-MM" in response.json()["detail"]

    def test_message_twin_also_validates_month(self, client):
        response = client.get(
            "/api/life/expenses/monthly-ai-summary/message?target_month=nope",
            headers={"x-api-key": TEST_API_KEY},
        )
        assert response.status_code == 422


class TestCORS:
    """The allowlist is the whole CORS policy — pin both directions.

    Uses /health (limiter-exempt) so these can never eat rate-limit budget.
    """

    def test_frontend_origin_is_allowed(self, client):
        response = client.get("/health", headers={"origin": "https://jakewang.dev"})
        assert response.headers["access-control-allow-origin"] == "https://jakewang.dev"

    def test_unknown_origin_gets_no_cors_headers(self, client):
        response = client.get("/health", headers={"origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_preflight_from_frontend_is_accepted(self, client):
        response = client.options("/api/tesla/stats", headers={
            "origin": "https://www.jakewang.dev",
            "access-control-request-method": "GET",
        })
        assert response.status_code == 200
        assert (
            response.headers["access-control-allow-origin"] == "https://www.jakewang.dev"
        )


class TestGZip:
    def test_large_responses_are_compressed(self, client_for):
        rows = [
            {"charge_date": date(2026, 1, 1), "provider": "Supercharger",
             "amount": 100, "kwh": 20.5},
        ] * 40  # well past the 500-byte minimum
        client = client_for(FakeSession(rows=rows))
        response = client.get(
            "/api/tesla/charging/sessions", headers={"accept-encoding": "gzip"}
        )
        assert response.headers.get("content-encoding") == "gzip"
        assert len(response.json()) == 40  # httpx transparently decompresses

    def test_small_responses_stay_uncompressed(self, client):
        response = client.get(
            "/api/life/expenses/recent", headers={"accept-encoding": "gzip"}
        )
        assert "content-encoding" not in response.headers


class TestRateLimit:
    def test_burst_beyond_limit_returns_429(self, client):
        statuses = [client.get("/").status_code for _ in range(125)]
        assert 429 in statuses
        # Everything before the first 429 succeeded normally
        assert statuses[0] == 200

    def test_health_is_exempt(self, client):
        statuses = {client.get("/health").status_code for _ in range(125)}
        assert statuses == {200}
