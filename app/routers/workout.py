"""Workout router - workout log recording.

Provides one endpoint:
- POST /logs : record one set (protected by x-api-key)
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import verify_shortcut_api_key

router = APIRouter()

VALID_EXERCISE_NAMES = {
    # Chest
    "Barbell Bench Press",
    "Dumbbell Bench Press",
    "Incline Dumbbell Press",
    "Pec Deck Fly",
    "Cable Fly",
    # Back
    "Lat Pulldown",
    "Seated Cable Row",
    "Single-Arm Dumbbell Row",
    # Shoulders
    "Barbell Overhead Press",
    "Dumbbell Shoulder Press",
    # Arms
    "Dumbbell Bicep Curl",
    "Hammer Curl",
    "Tricep Pushdown",
    "Overhead Tricep Extension",
    # Legs
    "Squat",
    "Leg Press",
    "Leg Extension",
    "Lunge",
}


class WorkoutLogCreate(BaseModel):
    """Payload for logging one set via POST /logs."""
    exercise_name: str
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
    if payload.exercise_name not in VALID_EXERCISE_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown exercise: '{payload.exercise_name}'.",
        )

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

    return {
        "status": "success",
        "message": "Workout log created",
        "data": {
            "id": row["id"],
            "exercise_name": payload.exercise_name,
            "weight_kg": payload.weight_kg,
            "reps": payload.reps,
            "date": payload.date.isoformat(),
            "created_at": row["created_at"].isoformat(),
        },
    }
