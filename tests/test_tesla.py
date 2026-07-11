"""Tests for the Tesla router: /stats math, /monthly-summary aggregation, writes.

The interesting logic is pure-Python post-processing (odometer deltas, derived
per-km metrics); FakeSession supplies the query results in call order.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.routers import tesla
from app.utils import get_today
from tests.conftest import TEST_API_KEY, FakeResult, FakeSession


class TestStats:
    def test_combines_totals_with_latest_odometer(self, client_for):
        session = FakeSession(results=[
            FakeResult(rows=[{
                "car_expense_total": 30000, "charging_cost": 12000, "energy_kwh": 2400.5,
            }]),
            FakeResult(scalar_value=24000),  # latest odometer reading
        ])
        body = client_for(session).get("/api/tesla/stats").json()
        assert body == {
            "total_cost": 42000.0,
            "charging_cost": 12000.0,
            "energy_kwh": 2400.5,
            "avg_price_per_kwh": 5.0,
            "odometer_km": 24000,
            "cost_per_km": 1.75,
        }

    def test_empty_history_falls_back_to_seed_odometer(self, client_for, monkeypatch):
        monkeypatch.setattr(tesla.settings, "tesla_odometer_km", 21000)
        session = FakeSession(results=[
            FakeResult(rows=[{"car_expense_total": 0, "charging_cost": 0, "energy_kwh": 0}]),
            FakeResult(scalar_value=None),  # no odometer rows yet
        ])
        body = client_for(session).get("/api/tesla/stats").json()
        assert body["odometer_km"] == 21000
        assert body["avg_price_per_kwh"] == 0  # zero kWh must not divide
        assert body["cost_per_km"] == 0


class TestMonthlySummary:
    def test_km_attribution_and_derived_metrics(self, client_for):
        session = FakeSession(results=[
            # charging per month
            FakeResult(rows=[
                {"month": "2026-01", "amount": 500, "kwh": 100.0},
                {"month": "2026-03", "amount": 600, "kwh": 150.0},
            ]),
            # car expenses per month
            FakeResult(rows=[{"month": "2026-02", "amount": 2000}]),
            # last odometer reading per reading-month
            FakeResult(rows=[
                {"month": "2026-01", "reading_km": 10000},
                {"month": "2026-03", "reading_km": 12000},
                {"month": "2026-04", "reading_km": 13000},
            ]),
        ])
        body = client_for(session).get("/api/tesla/monthly-summary").json()

        assert body == [
            # first reading month: no previous reading, so no km attributed
            {"month": "2026-01", "km_driven": None, "total_cost": 500,
             "cost_per_km": None, "kwh": 100.0, "kwh_per_100km": None},
            # expense-only month
            {"month": "2026-02", "km_driven": None, "total_cost": 2000,
             "cost_per_km": None, "kwh": 0.0, "kwh_per_100km": None},
            # the Jan->Mar reading gap is attributed to March (the later month)
            {"month": "2026-03", "km_driven": 2000, "total_cost": 600,
             "cost_per_km": 0.3, "kwh": 150.0, "kwh_per_100km": 7.5},
            # reading-only month still appears, with zero costs
            {"month": "2026-04", "km_driven": 1000, "total_cost": 0,
             "cost_per_km": 0.0, "kwh": 0.0, "kwh_per_100km": 0.0},
        ]

    def test_no_data_returns_empty_list(self, client_for):
        session = FakeSession(results=[
            FakeResult(rows=[]), FakeResult(rows=[]), FakeResult(rows=[]),
        ])
        assert client_for(session).get("/api/tesla/monthly-summary").json() == []


class TestWrites:
    def test_create_charging_record_envelope(self, client_for):
        session = FakeSession(results=[FakeResult(rows=[{"id": 7}])])
        response = client_for(session).post(
            "/api/tesla/charging-records",
            headers={"x-api-key": TEST_API_KEY},
            json={"charge_date": "2026-07-01", "provider": "Tesla Supercharger",
                  "amount": 150, "kwh": 30.5},
        )
        assert response.status_code == 200
        assert response.json() == {
            "status": "success",
            "message": "Charging record created",
            "data": {"id": 7, "charge_date": "2026-07-01",
                     "provider": "Tesla Supercharger", "amount": 150, "kwh": 30.5},
        }

    def test_negative_amount_is_rejected(self, client_for):
        response = client_for(FakeSession()).post(
            "/api/tesla/charging-records",
            headers={"x-api-key": TEST_API_KEY},
            json={"charge_date": "2026-07-01", "provider": "x", "amount": -1, "kwh": 1},
        )
        assert response.status_code == 422

    def test_create_car_expense_envelope(self, client_for):
        session = FakeSession(results=[FakeResult(rows=[{"id": 3}])])
        response = client_for(session).post(
            "/api/tesla/car-expenses",
            headers={"x-api-key": TEST_API_KEY},
            json={"date": "2026-07-01", "item": "Tires", "amount": 8000},
        )
        assert response.status_code == 200
        assert response.json()["data"] == {
            "id": 3, "date": "2026-07-01", "item": "Tires", "amount": 8000,
        }

    def test_odometer_reading_date_defaults_to_app_timezone_today(self, client_for):
        session = FakeSession(results=[FakeResult(rows=[{"id": 9}])])
        before = get_today()
        response = client_for(session).post(
            "/api/tesla/odometer",
            headers={"x-api-key": TEST_API_KEY},
            json={"reading_km": 24500},
        )
        after = get_today()

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == 9
        assert data["reading_km"] == 24500
        # before/after so a run that crosses midnight can't flake
        assert data["reading_date"] in {before.isoformat(), after.isoformat()}


class TestReadEndpoints:
    """The read endpoints share the query -> serialize_row pipeline. The
    parametrized check catches route-level wiring errors; the shape tests pin
    serialization of real DB types (Decimal, date) through each query shape."""

    @pytest.mark.parametrize("path", [
        "/api/tesla/expenses",
        "/api/tesla/charging/providers",
        "/api/tesla/charging/monthly-trend",
        "/api/tesla/charging/sessions",
        "/api/tesla/charging/recent",
        "/api/tesla/expenses/recent",
        "/api/tesla/odometer/recent",
    ])
    def test_read_endpoints_respond_with_lists(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == []

    def test_providers_serialize_db_decimals(self, client_for):
        # Postgres SUM/aggregates come back as Decimal, never int/float.
        session = FakeSession(rows=[{
            "provider": "Supercharger",
            "total_kwh": Decimal("240.50"),
            "total_amount": Decimal("1200"),
            "avg_price_per_kwh": Decimal("4.9896"),
        }])
        body = client_for(session).get("/api/tesla/charging/providers").json()
        assert body == [{
            "provider": "Supercharger",
            "total_kwh": 240.5,
            "total_amount": 1200,       # integral Decimal -> int
            "avg_price_per_kwh": 4.99,  # rounded to 2 decimals
        }]

    def test_sessions_serialize_dates(self, client_for):
        session = FakeSession(rows=[
            {"charge_date": date(2026, 7, 1), "provider": "Home", "amount": 90, "kwh": 22.0},
        ])
        body = client_for(session).get("/api/tesla/charging/sessions").json()
        assert body == [
            {"charge_date": "2026-07-01", "provider": "Home", "amount": 90, "kwh": 22.0},
        ]

    def test_odometer_current_returns_latest_reading(self, client_for):
        body = client_for(FakeSession(scalar_value=24123)).get(
            "/api/tesla/odometer/current"
        ).json()
        assert body == {"odometer_km": 24123}
