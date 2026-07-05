"""Main FastAPI application entrypoint.

Mounts the Tesla and Life routers, configures CORS for the personal website + local dev,
and exposes basic health/root endpoints. The real business logic lives in the routers.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

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

# How long browsers/proxies may cache public GET responses (seconds).
# Dashboards tolerate slightly stale data; this cuts repeat DB hits on reloads.
PUBLIC_CACHE_MAX_AGE = 300


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
def health_check():
    """Basic health check for the entire API (used by load balancers, monitors, etc.)."""
    return {"status": "ok"}


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
