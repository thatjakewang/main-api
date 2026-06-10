"""Database session management (SQLAlchemy 1.x style).

A single engine is created at import time. Per-request sessions are provided
via the get_db dependency. The app uses raw SQL (text()) rather than ORM models
because the use-case is read-heavy analytics on small personal tables.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

settings = get_settings()

# Create the SQLAlchemy engine once at import time.
# pool_pre_ping=True helps detect stale connections (useful for cloud Postgres).
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

# Session factory. We create a new Session per request via the get_db dependency.
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db():
    """FastAPI dependency that yields a SQLAlchemy session for a single request.

    The session is rolled back if the request raises (so a failed write never
    leaves a dangling transaction) and always closed afterwards. Standard
    request-scoped DB session pattern for FastAPI + SQLAlchemy.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()