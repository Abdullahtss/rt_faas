from datetime import datetime, timedelta

from app.engine.geo import haversine_km, implied_speed_kmh
from app.engine.windows import Event, amount_points, risk_level, travel_points, velocity_points


def test_velocity_caps_at_40():
    pts, _ = velocity_points(5)
    assert pts == 0
    pts, _ = velocity_points(6)
    assert pts == 8
    pts, _ = velocity_points(20)
    assert pts == 40


def test_travel_impossible():
    now = datetime(2026, 10, 6, 12, 0, 0)
    prev = Event(now, 24.86, 67.00, 1000)  # Karachi
    pts, detail = travel_points(prev, 51.50, -0.12, now + timedelta(minutes=8))  # London
    assert pts == 50
    assert "900" in detail


def test_travel_normal():
    now = datetime(2026, 10, 6, 12, 0, 0)
    prev = Event(now, 24.86, 67.00, 1000)
    pts, _ = travel_points(prev, 24.87, 67.01, now + timedelta(hours=2))
    assert pts == 0


def test_amount_sigma():
    pts, _ = amount_points(10000, 20, 4000, 800)
    assert pts == 30
    pts, _ = amount_points(5800, 20, 4000, 800)
    assert pts == 15
    pts, _ = amount_points(4100, 20, 4000, 800)
    assert pts == 0
    pts, _ = amount_points(99999, 3, 4000, 800)
    assert pts == 0


def test_risk_levels():
    assert risk_level(0) == "Low"
    assert risk_level(39) == "Low"
    assert risk_level(40) == "Medium"
    assert risk_level(70) == "High"
    assert risk_level(100) == "High"


def test_karachi_london_distance():
    km = haversine_km(24.8607, 67.0011, 51.5074, -0.1278)
    assert 5500 < km < 7000
    speed = implied_speed_kmh(24.8607, 67.0011, 51.5074, -0.1278, 60)
    assert speed > 900
