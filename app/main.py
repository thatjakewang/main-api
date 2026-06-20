"""Main FastAPI application entrypoint.

Mounts the Tesla and Life routers, configures CORS for the personal website + local dev,
and exposes basic health/root endpoints. The real business logic lives in the routers.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import life, tesla

app = FastAPI(
    title="My Tesla Analytics API",
    version="0.1.0",
)

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
    """Root endpoint returning basic service info and links to docs/health."""
    return {
        "status": "ok",
        "service": "tesla-api",
        "docs": "/docs",
        "health": "/health",
    }


# Tesla cost tracking (public stats + protected writes for charging/car expenses)
app.include_router(tesla.router, prefix="/api/tesla", tags=["Tesla"])

# Daily life expenses + AI summaries (some endpoints public, AI ones protected)
app.include_router(life.router, prefix="/api/life", tags=["Life"])
