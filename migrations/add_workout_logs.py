#!/usr/bin/env python
"""
Migration: Create the workout_logs table.

Stores individual set-level workout records. Each row represents one set
completed — exercise name, weight (kg), and reps. The set number within a
session can be derived via ROW_NUMBER() ordered by created_at.

This is a one-off script kept for reference.
It is idempotent (CREATE TABLE IF NOT EXISTS) and safe to re-run.

The script will automatically load DATABASE_URL from the .env file in the
project root (no need to manually `source .env`).

Usage:
    cd /var/www/main-api
    source .venv/bin/activate
    python migrations/add_workout_logs.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

EXERCISES = [
    # Chest
    ("Barbell Bench Press", "Chest"),
    ("Dumbbell Bench Press", "Chest"),
    ("Incline Dumbbell Press", "Chest"),
    ("Pec Deck Fly", "Chest"),
    ("Cable Fly", "Chest"),
    # Back
    ("Lat Pulldown", "Back"),
    ("Seated Cable Row", "Back"),
    ("Single-Arm Dumbbell Row", "Back"),
    # Shoulders
    ("Barbell Overhead Press", "Shoulders"),
    ("Dumbbell Shoulder Press", "Shoulders"),
    # Arms
    ("Dumbbell Bicep Curl", "Arms"),
    ("Hammer Curl", "Arms"),
    ("Tricep Pushdown", "Arms"),
    ("Overhead Tricep Extension", "Arms"),
    # Legs
    ("Squat", "Legs"),
    ("Leg Press", "Legs"),
    ("Leg Extension", "Legs"),
    ("Lunge", "Legs"),
]


def run_migration():
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ ERROR: DATABASE_URL environment variable is not set.")
        print("   Make sure a .env file exists in the project root with DATABASE_URL.")
        print(f"   Looked for .env at: {project_root / '.env'}")
        sys.exit(1)

    print("Connecting to database (host hidden for safety)...")
    engine = create_engine(db_url)

    with engine.begin() as conn:
        print("1/2  Creating workout_logs table ...")
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS workout_logs (
                    id            SERIAL PRIMARY KEY,
                    exercise_name TEXT NOT NULL,
                    weight_kg     NUMERIC(5, 2) NOT NULL,
                    reps          INTEGER NOT NULL CHECK (reps > 0),
                    date          DATE NOT NULL,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        print("2/2  Creating index on (exercise_name, date) for history queries ...")
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_workout_logs_exercise_date
                ON workout_logs (exercise_name, date DESC)
                """
            )
        )

    print("\n✅ Migration completed successfully!")
    print("   You can now restart the API service.")
    print()
    print("   Exercise list for reference (hardcoded in router):")
    for name, group in EXERCISES:
        print(f"   [{group}] {name}")


if __name__ == "__main__":
    run_migration()
