"""Workout router - workout log recording and reading.

Endpoints:
- POST /logs            : record one set (protected by x-api-key)
- GET  /logs/recent       : 10 most recent sets (public, for the dashboard)
- GET  /stats             : current-month KPI numbers (public)
- GET  /exercises/prs     : per-exercise personal records (public)
- GET  /exercises/history : per-day progression for one exercise (public)
- GET  /volume/weekly     : week-by-week training volume trend (public)
- GET  /days              : per-day volume for the calendar heatmap (public)

Exercise names are free-form text (no server-side whitelist), so new exercises
can be added from the iPhone Shortcut without touching the API.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import verify_shortcut_api_key
from app.utils import (
    get_month_start,
    get_next_month_start,
    get_today,
    serialize_row,
    serialize_value,
    success_response,
)

router = APIRouter()


class WorkoutLogCreate(BaseModel):
    """Payload for logging one set via POST /logs.

    exercise_name is free-form (must be non-empty); weight and reps must be positive.
    """
    exercise_name: str = Field(min_length=1)
    weight_kg: float = Field(gt=0)
    reps: int = Field(gt=0)
    date: date


@router.post("/logs")
def create_workout_log(
    payload: WorkoutLogCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_shortcut_api_key),
):
    """Record one set to workout_logs (protected by x-api-key)."""
    query = text("""
        INSERT INTO workout_logs (exercise_name, weight_kg, reps, date)
        VALUES (:exercise_name, :weight_kg, :reps, :date)
        RETURNING id, created_at
    """)

    result = db.execute(
        query,
        {
            "exercise_name": payload.exercise_name,
            "weight_kg": payload.weight_kg,
            "reps": payload.reps,
            "date": payload.date,
        },
    )
    db.commit()

    row = result.mappings().one()

    return success_response(
        "Workout log created",
        {
            "id": row["id"],
            "exercise_name": payload.exercise_name,
            "weight_kg": payload.weight_kg,
            "reps": payload.reps,
            "date": payload.date.isoformat(),
            "created_at": row["created_at"].isoformat(),
        },
    )


@router.get("/logs/recent")
def get_recent_workout_logs(db: Session = Depends(get_db)):
    """Return the 10 most recent sets (newest first). Public, for the dashboard."""
    query = text("""
        SELECT id, exercise_name, weight_kg, reps, date, created_at
        FROM workout_logs
        ORDER BY date DESC, created_at DESC, id DESC
        LIMIT 10
    """)
    rows = db.execute(query).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get("/stats")
def get_workout_stats(db: Session = Depends(get_db)):
    """Return current-month workout KPIs (public).

    workout_days / total_sets / total_volume_kg are scoped to the current month
    (APP_TIMEZONE aware, same month boundaries as the life endpoints);
    last_workout_date is all-time.
    """
    month_start = get_month_start()
    next_month_start = get_next_month_start(month_start)

    month_query = text("""
        SELECT
            COUNT(DISTINCT date) AS workout_days,
            COUNT(*) AS total_sets,
            COALESCE(SUM(weight_kg * reps), 0) AS total_volume_kg
        FROM workout_logs
        WHERE date >= :month_start
          AND date < :next_month_start
    """)
    last_query = text("""
        SELECT MAX(date) AS last_workout_date
        FROM workout_logs
    """)

    month_row = db.execute(
        month_query, {"month_start": month_start, "next_month_start": next_month_start}
    ).mappings().one()
    last_workout_date = db.execute(last_query).scalar()

    return {
        "month": month_start.strftime("%Y-%m"),
        "workout_days": int(month_row["workout_days"] or 0),
        "total_sets": int(month_row["total_sets"] or 0),
        "total_volume_kg": round(float(month_row["total_volume_kg"] or 0)),
        "last_workout_date": serialize_value(last_workout_date),
    }


@router.get("/exercises/prs")
def get_exercise_prs(db: Session = Depends(get_db)):
    """Return per-exercise personal records, heaviest first (public).

    For each exercise: the max weight ever lifted, total sets logged, and the
    date it was last performed. Free-form names group naturally by exact match.
    """
    query = text("""
        SELECT
            exercise_name,
            MAX(weight_kg) AS max_weight_kg,
            COUNT(*) AS total_sets,
            MAX(date) AS last_performed
        FROM workout_logs
        GROUP BY exercise_name
        ORDER BY max_weight_kg DESC
    """)
    rows = db.execute(query).mappings().all()

    return [serialize_row(row) for row in rows]


@router.get("/exercises/history")
def get_exercise_history(
    name: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    """Return per-day progression for one exercise, oldest first (public).

    For each day the exercise was performed: the heaviest set of the day and
    the day's total volume. Powers the progression line chart; the frontend
    picks `name` from the /exercises/prs list, so exact match is fine.
    """
    query = text("""
        SELECT
            date,
            MAX(weight_kg) AS max_weight_kg,
            COALESCE(SUM(weight_kg * reps), 0) AS total_volume_kg
        FROM workout_logs
        WHERE exercise_name = :name
        GROUP BY date
        ORDER BY date
    """)
    rows = db.execute(query, {"name": name}).mappings().all()

    return [
        {
            "date": serialize_value(row["date"]),
            "max_weight_kg": float(row["max_weight_kg"]),
            "total_volume_kg": round(float(row["total_volume_kg"] or 0)),
        }
        for row in rows
    ]


@router.get("/volume/weekly")
def get_weekly_volume(db: Session = Depends(get_db)):
    """Return week-by-week training volume (sum of weight x reps) and workout days.

    Covers the last 26 weeks (including the current one). Weeks start on
    Monday (Postgres DATE_TRUNC('week') is ISO), ordered chronologically.
    Weeks with no training are simply absent. Public.
    """
    today = get_today()
    this_week_start = today - timedelta(days=today.weekday())
    start = this_week_start - timedelta(weeks=25)

    query = text("""
        SELECT
            TO_CHAR(DATE_TRUNC('week', date), 'YYYY-MM-DD') AS week_start,
            COALESCE(SUM(weight_kg * reps), 0) AS total_volume_kg,
            COUNT(DISTINCT date) AS workout_days
        FROM workout_logs
        WHERE date >= :start
        GROUP BY DATE_TRUNC('week', date)
        ORDER BY DATE_TRUNC('week', date)
    """)
    rows = db.execute(query, {"start": start}).mappings().all()

    return [
        {
            "week_start": row["week_start"],
            "total_volume_kg": round(float(row["total_volume_kg"] or 0)),
            "workout_days": int(row["workout_days"] or 0),
        }
        for row in rows
    ]


@router.get("/days")
def get_workout_days(db: Session = Depends(get_db)):
    """Return per-day training volume for the past 12 months (public).

    Powers the calendar heatmap. Only days with at least one logged set are
    returned; the frontend renders the full calendar and fills in the gaps.
    Window is computed from get_today() so it follows APP_TIMEZONE.
    """
    start = get_today() - timedelta(days=365)

    query = text("""
        SELECT
            date,
            COALESCE(SUM(weight_kg * reps), 0) AS total_volume_kg,
            COUNT(*) AS total_sets
        FROM workout_logs
        WHERE date >= :start
        GROUP BY date
        ORDER BY date
    """)
    rows = db.execute(query, {"start": start}).mappings().all()

    return [
        {
            "date": serialize_value(row["date"]),
            "total_volume_kg": round(float(row["total_volume_kg"] or 0)),
            "total_sets": int(row["total_sets"]),
        }
        for row in rows
    ]
