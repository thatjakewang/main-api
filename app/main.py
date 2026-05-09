from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import tesla, life

app = FastAPI(
    title="My Tesla Analytics API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://jakewang.dev",
        "https://www.jakewang.dev",
        "http://127.0.0.1:5001",
        "http://localhost:5001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok"}


app.include_router(
    tesla.router,
    prefix="/api/tesla",
    tags=["Tesla"],
)

app.include_router(
    life.router,
    prefix="/api/life",
    tags=["Life"],
)