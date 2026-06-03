#!/usr/bin/env python
"""
Migration: Add id + created_at columns to charging_records and car_expenses
           to enable stable ordering + populated id/created_at in the
           /charging/recent and /expenses/recent responses (and in create responses).

The API endpoints are backward-compatible and will work (with id/created_at=null
and date-only ordering) even if you deploy before running this. Running the
migration later will automatically enrich responses and improve sort stability
on subsequent requests (no restart required).

Run this on the PRODUCTION server after `git pull` (can be before or after
restarting the API).

The script will automatically load DATABASE_URL from the .env file in the
project root (no need to manually `source .env`).

Usage:
    cd /var/www/main-api
    source .venv/bin/activate
    python migrations/add_tesla_recent_columns.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def run_migration():
    # Always load .env from the project root, no matter what the current
    # working directory is (e.g. running from inside migrations/).
    # This matches how the real app loads config via pydantic-settings.
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ ERROR: DATABASE_URL environment variable is not set.")
        print("   Make sure a .env file exists in the project root with DATABASE_URL.")
        print(f"   Looked for .env at: {project_root / '.env'}")
        sys.exit(1)

    print(f"Connecting to database (host hidden for safety)...")
    engine = create_engine(db_url)

    with engine.begin() as conn:
        print("1/4  Adding id + created_at to charging_records ...")
        conn.execute(
            text(
                """
                ALTER TABLE charging_records
                ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY,
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """
            )
        )

        print("2/4  Adding id + created_at to car_expenses ...")
        conn.execute(
            text(
                """
                ALTER TABLE car_expenses
                ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY,
                ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """
            )
        )

        print("3/4  Backfilling created_at for existing charging records ...")
        conn.execute(
            text(
                """
                UPDATE charging_records
                SET created_at = charge_date::timestamp
                WHERE created_at IS NULL AND charge_date IS NOT NULL
            """
            )
        )

        print("4/4  Backfilling created_at for existing car expenses ...")
        conn.execute(
            text(
                """
                UPDATE car_expenses
                SET created_at = date::timestamp
                WHERE created_at IS NULL AND date IS NOT NULL
            """
            )
        )

    print("\n✅ Migration completed successfully!")
    print("   You can now restart the API service.")


if __name__ == "__main__":
    run_migration()
