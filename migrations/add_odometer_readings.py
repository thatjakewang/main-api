#!/usr/bin/env python
"""
Migration: Create the odometer_readings table.

Stores append-only total-odometer snapshots (the number shown on the Tesla
screen) so cost-per-km in /api/tesla/stats always uses the latest reading,
instead of the static TESLA_ODOMETER_KM value baked into config.py.

Keeping every reading (not just the latest) preserves history, so distance
driven per period and charging efficiency can be derived later.

NOT yet executed on production.

This is a one-off script kept for reference.
It is idempotent (CREATE TABLE IF NOT EXISTS + conditional seed) and safe to re-run.

The seed inserts a first row using the current hardcoded odometer value
(matches Settings.tesla_odometer_km) only when the table is empty, so /stats
keeps working before the first real reading is logged.

The script will automatically load DATABASE_URL from the .env file in the
project root (no need to manually `source .env`).

Usage:
    cd /var/www/main-api
    source .venv/bin/activate
    python migrations/add_odometer_readings.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Current hardcoded value from config.py (Settings.tesla_odometer_km).
SEED_ODOMETER_KM = 22937


def run_migration():
    # Always load .env from the project root, no matter what the current
    # working directory is (e.g. running from inside migrations/).
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
        print("1/2  Creating odometer_readings table ...")
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS odometer_readings (
                    id SERIAL PRIMARY KEY,
                    reading_km INTEGER NOT NULL,
                    reading_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
        )

        print("2/2  Seeding first reading if table is empty ...")
        conn.execute(
            text(
                """
                INSERT INTO odometer_readings (reading_km, reading_date)
                SELECT :seed_km, CURRENT_DATE
                WHERE NOT EXISTS (SELECT 1 FROM odometer_readings)
            """
            ),
            {"seed_km": SEED_ODOMETER_KM},
        )

    print("\n✅ Migration completed successfully!")
    print("   You can now restart the API service.")


if __name__ == "__main__":
    run_migration()
