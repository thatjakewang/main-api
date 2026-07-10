"""HTTP-level tests: health, auth behavior, cache headers, rate limiting.

All DB access goes through FakeSession — no real database is involved.
"""

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
        assert response.headers["Cache-Control"] == "public, max-age=300"

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


class TestRateLimit:
    def test_burst_beyond_limit_returns_429(self, client):
        statuses = [client.get("/").status_code for _ in range(125)]
        assert 429 in statuses
        # Everything before the first 429 succeeded normally
        assert statuses[0] == 200

    def test_health_is_exempt(self, client):
        statuses = {client.get("/health").status_code for _ in range(125)}
        assert statuses == {200}
