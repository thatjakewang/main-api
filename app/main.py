from fastapi import FastAPI
from app.routers import tesla

app = FastAPI(
    title="My Tesla Analytics API",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(
    tesla.router,
    prefix="/api/tesla",
    tags=["Tesla"],
)