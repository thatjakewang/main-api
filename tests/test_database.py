"""Tests for the get_db dependency's session lifecycle (rollback/close).

The docstring on get_db promises "a failed write never leaves a dangling
transaction"; these pin that promise without touching a real database.
"""

import pytest

from app import database
from tests.conftest import FakeSession


@pytest.fixture
def fake_session(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    return session


def test_session_closes_after_normal_use(fake_session):
    gen = database.get_db()
    next(gen)
    with pytest.raises(StopIteration):
        next(gen)

    assert fake_session.closed
    assert not fake_session.rolled_back


def test_session_rolls_back_and_closes_when_request_raises(fake_session):
    gen = database.get_db()
    next(gen)
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("boom"))

    assert fake_session.rolled_back
    assert fake_session.closed
