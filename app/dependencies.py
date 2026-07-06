"""Shared FastAPI dependencies.

Currently only contains the API key verification used by protected endpoints
in both the Tesla and Life routers.
"""

import secrets

from fastapi import Header, HTTPException

from app.config import get_settings


def verify_shortcut_api_key(
    x_api_key: str | None = Header(
        None, description="API key from iPhone Shortcuts / trusted clients"
    ),
) -> None:
    """Dependency: verify that the provided x-api-key matches the configured SHORTCUT_API_KEY.

    Uses secrets.compare_digest for constant-time comparison (timing-attack resistance).
    All protected write + AI endpoints in both routers depend on this. The header is
    declared optional so a missing key returns 401 like an invalid one, instead of
    FastAPI's 422 validation error.
    """
    settings = get_settings()
    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.shortcut_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
