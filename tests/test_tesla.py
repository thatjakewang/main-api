"""Tests for the Tesla router: /stats math, /monthly-summary aggregation, writes.

The interesting logic is pure-Python post-processing (odometer deltas, derived
per-km metrics); FakeSession supplies the query results in call order.
"""

from app.routers import tesla
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
