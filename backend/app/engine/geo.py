"""Haversine distance and implied travel speed."""
from math import asin, cos, radians, sin, sqrt


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(min(1.0, a)))


def implied_speed_kmh(lat1: float, lon1: float, lat2: float, lon2: float, seconds: float) -> float:
    if seconds <= 0:
        seconds = 0.001
    return haversine_km(lat1, lon1, lat2, lon2) / (seconds / 3600.0)
