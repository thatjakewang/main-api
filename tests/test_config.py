"""Tests for Settings.parse_money (config.py)."""

import pytest

from app.config import Settings


def make_settings(**overrides) -> Settings:
    """Build a Settings instance from explicit values only (no .env, no os.environ)."""
    base = {"database_url": "postgresql://x", "shortcut_api_key": "k"}
    return Settings(_env_file=None, **base, **overrides)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("80,000", 80000),
        ("80000", 80000),
        (80000, 80000),
        (0, 0),
        (None, None),
        ("", None),
        ("   ", None),
        ("abc", None),
        ("-5", None),
        (-5, None),
    ],
)
def test_parse_money(raw, expected):
    settings = make_settings(monthly_income=raw, monthly_fixed_expenses=raw)
    assert settings.monthly_income == expected
    assert settings.monthly_fixed_expenses == expected
