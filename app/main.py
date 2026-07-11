"""Main FastAPI application entrypoint.

Mounts the Tesla and Life routers, configures CORS for the personal website + local dev,
and exposes basic health/root endpoints. The real business logic lives in the routers.
"""

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers import life, tesla

app = FastAPI(
    title="My Tesla Analytics API",
    version="0.1.0",
    # Hide API docs/schema in production — no need to hand attackers a map
    # of every endpoint (including the protected ones).
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Compress JSON responses over 500 bytes (charging/sessions etc. grow over time).
app.add_middleware(GZipMiddleware, minimum_size=500)

# Rate limiting: per-client-IP cap on every endpoint (in-memory storage — fine for
# a single-process deployment). /health is exempt so monitors are never throttled.
# Note: if the app sits behind a reverse proxy, uvicorn needs --proxy-headers (and
# --forwarded-allow-ips) so get_remote_address sees the real client IP.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],
    headers_enabled=True,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# How long browsers/proxies may cache public GET responses (seconds).
# Kept short: entries added via iPhone Shortcuts should show up on the
# dashboards right away — the browser cache can't be invalidated remotely,
# so this window is the maximum staleness. Still absorbs reload bursts.
PUBLIC_CACHE_MAX_AGE = 30


@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    """Add Cache-Control to successful public /api GET responses.

    Requests carrying x-api-key (protected AI endpoints) are skipped so
    per-user AI summaries are never marked publicly cacheable.
    """
    response = await call_next(request)
    if (
        request.method == "GET"
        and request.url.path.startswith("/api")
        and response.status_code == 200
        and "x-api-key" not in request.headers
    ):
        response.headers.setdefault("Cache-Control", f"public, max-age={PUBLIC_CACHE_MAX_AGE}")
    return response

# CORS is intentionally narrow (only the real frontend domains + local dev).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://jakewang.dev",
        "https://www.jakewang.dev",
        "http://127.0.0.1:5001",
        "http://localhost:5001",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@limiter.exempt
def health_check(db: Session = Depends(get_db)):
    """Health check for the entire API (used by load balancers, monitors, etc.).

    Pings the database with SELECT 1 so a dead DB shows up as 503 instead of a
    green health check in front of a broken service.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "unreachable"},
        )
    return {"status": "ok", "database": "ok"}


@app.get("/")
def root():
    """Root endpoint returning basic service info and a link to the health check."""
    return {
        "status": "ok",
        "service": "tesla-api",
        "health": "/health",
    }


# Tesla cost tracking (public stats + protected writes for charging/car expenses)
app.include_router(tesla.router, prefix="/api/tesla", tags=["Tesla"])

# Daily life expenses + AI summaries (some endpoints public, AI ones protected)
app.include_router(life.router, prefix="/api/life", tags=["Life"])
