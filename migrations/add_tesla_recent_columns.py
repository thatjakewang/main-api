#!/usr/bin/env python
"""
Migration: Add id + created_at columns to charging_records and car_expenses
           so that the new /charging/recent and /expenses/recent endpoints work.

Run this on the PRODUCTION server after `git pull`, before restarting the API.

Usage (after cd into main-api dir and loading env):
    source .venv/bin/activate
    source .env
    python migrations/add_tesla_recent_columns.py
"""
import os
import sys

from sqlalchemy import create_engine, text


def run_migration():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("❌ ERROR: DATABASE_URL environment variable is not set.")
        print("   Make sure you have sourced your .env file or set the variable.")
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
