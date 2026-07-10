"""Shared test fixtures.

Environment variables are set BEFORE any app module is imported so the cached
Settings instance (and the module-level `settings` in ai_summary/tesla) is built
from test values, never from the developer's real .env (real env vars take
priority over the .env file in pydantic-settings).

No test ever touches a real database: endpoints get a FakeSession via
dependency_overrides, and the engine created at import time never connects.
"""

import os

os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test_never_connected"
os.environ["SHORTCUT_API_KEY"] = "test-api-key"
os.environ["OPENAI_API_KEY"] = ""
os.environ["APP_TIMEZONE"] = "Asia/Taipei"

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app

TEST_API_KEY = "test-api-key"


class FakeResult:
    """Mimics the subset of SQLAlchemy's Result used by the app (mappings/one/all/scalar)."""

    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar = scalar_value

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]

    def scalar(self):
        return self._scalar


class FakeSession:
    """A DB session double: returns canned rows, records close/rollback calls.

    `results` (a list of FakeResult) makes consecutive execute() calls return
    different result sets, for endpoints that run several queries. Every
    execute()'s positional args land in `calls`, so tests can assert the
    parameters bound to each query (calls[i][1] is the params dict, if any).
    """

    def __init__(self, rows=None, scalar_value=None, execute_error=None, results=None):
        self.rows = rows
        self.scalar_value = scalar_value
        self.execute_error = execute_error
        self.results = list(results) if results else None
        self.closed = False
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append(args)
        if self.execute_error is not None:
            raise self.execute_error
        if self.results is not None:
            return self.results.pop(0)
        return FakeResult(self.rows, self.scalar_value)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def fake_db():
    """Default healthy fake session returning no rows."""
    return FakeSession(rows=[])


@pytest.fixture
def client(fake_db):
    """TestClient wired to the fake session; override is removed after each test."""
    app.dependency_overrides[get_db] = lambda: fake_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def client_for():
    """Factory: build a TestClient whose get_db yields the given fake session."""

    def factory(session: FakeSession) -> TestClient:
        app.dependency_overrides[get_db] = lambda: session
        return TestClient(app)

    yield factory
    app.dependency_overrides.clear()
