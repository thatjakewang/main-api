-- Reference DDL for the main-api database (PostgreSQL).
-- Rebuild an empty database with:  psql "$DATABASE_URL" -f schema.sql
--
-- Generated from pg_dump --schema-only on 2026-07-10 (odometer_readings DDL
-- taken from the applied add_odometer_readings migration, see git history).
-- Keep this file in sync whenever a migration changes the schema.
--
-- Notes:
-- - The older Tesla tables (charging_records, car_expenses) predate the API
--   and allow NULLs; the API itself always writes every column.
-- - created_at is not read by the API (kept for auditing); /recent ordering
--   uses the record's date column + id instead.

CREATE TABLE IF NOT EXISTS charging_records (
    id SERIAL PRIMARY KEY,
    charge_date date,
    provider text,
    amount bigint,
    kwh double precision,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS car_expenses (
    id SERIAL PRIMARY KEY,
    date date,
    item text,
    amount bigint,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS odometer_readings (
    id SERIAL PRIMARY KEY,
    reading_km integer NOT NULL,
    reading_date date NOT NULL,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_expenses (
    id SERIAL PRIMARY KEY,
    date date NOT NULL,
    category text NOT NULL,
    amount integer NOT NULL,
    created_at timestamp DEFAULT CURRENT_TIMESTAMP
);
