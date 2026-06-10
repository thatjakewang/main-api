"""Application configuration (pydantic-settings).

All runtime configuration (DB connection, API keys, timezone, budget numbers,
odometer reading) is defined here and loaded from .env or environment variables.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file.

    Sensitive values (keys) and deployment-specific values (DB URL, odometer) are
    kept out of source control. Pydantic-settings automatically validates types.
    """

    database_url: str
    shortcut_api_key: str
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.4-mini"
    app_timezone: str = "Asia/Taipei"
    monthly_income: int | None = None
    monthly_fixed_expenses: int | None = None
    tesla_odometer_km: int = 22937

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("monthly_income", "monthly_fixed_expenses", mode="before")
    @classmethod
    def parse_money(cls, value):
        """Parse money values that may arrive as strings with commas (e.g. '80,000').

        Runs before type validation, so consumers always see a clean int (or None).
        Invalid, empty, or negative values become None instead of crashing startup.
        """
        if value is None or isinstance(value, int):
            return value if value is None or value >= 0 else None

        normalized = str(value).strip().replace(",", "")
        if not normalized:
            return None

        try:
            amount = int(normalized)
        except ValueError:
            return None

        return amount if amount >= 0 else None


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance loaded from environment / .env file.

    Using lru_cache means the .env is read only once per process (good for performance
    and to avoid repeated file I/O). All configuration (DB URL, API keys, timezone, etc.)
    lives here.
    """
    return Settings()
