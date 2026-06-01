import secrets

from fastapi import Header, HTTPException

from app.config import get_settings


def verify_shortcut_api_key(
    x_api_key: str = Header(..., description="API key from iPhone Shortcuts / trusted clients"),
) -> None:
    """Verify the x-api-key header against the configured SHORTCUT_API_KEY.

    Uses constant-time comparison to avoid timing attacks.
    """
    settings = get_settings()
    if not secrets.compare_digest(x_api_key, settings.shortcut_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
