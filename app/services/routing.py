"""Lightweight delivery routing.

At 100–500 orders/day a full VRP solver is overkill (per RESEARCH_NOTES), so we
use a greedy nearest-neighbour TSP over drop-off coordinates and emit a Google
Maps multi-destination navigation link for the rider's WhatsApp. Pure functions,
no external service — swappable for OSRM later behind the same interface.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import quote


@dataclass
class Stop:
    label: str
    gps: str  # "lat,lng"


def parse_gps(gps: str) -> tuple[float, float]:
    lat_s, lng_s = gps.split(",")
    return float(lat_s.strip()), float(lng_s.strip())


def haversine_km(a: str, b: str) -> float:
    lat1, lng1 = parse_gps(a)
    lat2, lng2 = parse_gps(b)
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(h))


def greedy_route(start_gps: str, stops: list[Stop]) -> list[Stop]:
    """Order stops by repeatedly hopping to the nearest unvisited drop-off."""
    remaining = list(stops)
    ordered: list[Stop] = []
    current = start_gps
    while remaining:
        nxt = min(remaining, key=lambda s: haversine_km(current, s.gps))
        ordered.append(nxt)
        current = nxt.gps
        remaining.remove(nxt)
    return ordered


def google_maps_route_url(start_gps: str, stops: list[Stop]) -> str:
    """https://www.google.com/maps/dir/<start>/<stop1>/<stop2>..."""
    segments = [start_gps] + [s.gps for s in stops]
    path = "/".join(quote(seg) for seg in segments)
    return f"https://www.google.com/maps/dir/{path}"


def build_dispatch(start_gps: str, stops: list[Stop]) -> dict:
    """Return an optimized route + navigation URL + total distance estimate."""
    ordered = greedy_route(start_gps, stops)
    url = google_maps_route_url(start_gps, ordered)
    total = 0.0
    cur = start_gps
    for s in ordered:
        total += haversine_km(cur, s.gps)
        cur = s.gps
    return {
        "ordered_stops": ordered,
        "route_url": url,
        "total_km": round(total, 2),
    }
