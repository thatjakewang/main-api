#!/usr/bin/env python
"""
Migration: Drop the unused payment_method column from daily_expenses.

The column was only ever displayed in the Recent Expenses table and drove no
stats/charts/AI analysis, so it has been removed from the API. Run this AFTER
deploying the new code (which no longer reads or writes the column).

Idempotent: safe to run more than once (DROP COLUMN IF EXISTS).

Usage (after cd into main-api dir and loading env):
    source .venv/bin/activate
    source .env
    python migrations/drop_payment_method.py
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

    print("Connecting to database (host hidden for safety)...")
    engine = create_engine(db_url)

    with engine.begin() as conn:
        print("1/1  Dropping payment_method from daily_expenses ...")
        conn.execute(
            text("ALTER TABLE daily_expenses DROP COLUMN IF EXISTS payment_method")
        )

    print("\n✅ Migration completed successfully!")
    print("   You can now restart the API service.")


if __name__ == "__main__":
    run_migration()
