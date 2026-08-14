"""Route optimization service (dual-mode: real Google Routes API / mock nearest-neighbour).

Same swappable-seam pattern as payment_service: with a `GOOGLE_MAPS_API_KEY` set
it calls the live Google **Routes API v2** (`computeRoutes`, traffic-aware,
`optimizeWaypointOrder`); with no key it runs a deterministic **nearest-neighbour**
ordering offline using our own Haversine. Either way it returns the same shape, so
callers (run_cutoff_batch) don't care which mode ran.

Contract:
    optimize_route(origin, stops) -> {
        mode, order, total_distance_km, estimated_duration_mins, maps_url
    }
where `origin` = {"lat", "lng"} (the kitchen), `stops` = [{"lat","lng", ...}] (the
deliveries), and `order` = the stops' original indices in optimized visit order.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any

from backend.app.core.config import settings
from backend.app.core.geo import haversine_km

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
AVG_CITY_SPEED_KMPH = 25.0   # rough duration estimate for the mock


class MapsService:
    """Optimize a kitchen->deliveries route. Real Google Routes API, or a mock fallback."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.google_maps_api_key

    async def optimize_route(self, *, origin: dict[str, float], stops: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the optimized visit order + distance/duration + a clickable maps link."""
        if not stops:
            return {"mode": "EMPTY", "order": [], "total_distance_km": 0.0,
                    "estimated_duration_mins": 0, "maps_url": self._maps_url(origin, [])}
        if self.api_key:
            try:
                return await self._real(origin, stops)
            except Exception as e:  # network/key/quota -> degrade gracefully to the mock
                res = self._mock(origin, stops)
                res["fallback_error"] = str(e)[:100]
                return res
        return self._mock(origin, stops)

    # ---- mock (offline, deterministic) --------------------------------------
    def _mock(self, origin: dict[str, float], stops: list[dict[str, Any]]) -> dict[str, Any]:
        """Nearest-neighbour from the kitchen: repeatedly hop to the closest unvisited stop."""
        remaining = list(range(len(stops)))
        order: list[int] = []
        cur_lat, cur_lng = origin["lat"], origin["lng"]
        total = 0.0
        while remaining:
            nxt = min(remaining, key=lambda i: haversine_km(cur_lat, cur_lng, stops[i]["lat"], stops[i]["lng"]))
            total += haversine_km(cur_lat, cur_lng, stops[nxt]["lat"], stops[nxt]["lng"])
            order.append(nxt)
            cur_lat, cur_lng = stops[nxt]["lat"], stops[nxt]["lng"]
            remaining.remove(nxt)
        duration = max(1, int(round(total / AVG_CITY_SPEED_KMPH * 60)))
        return {
            "mode": "MOCK",
            "order": order,
            "total_distance_km": round(total, 2),
            "estimated_duration_mins": duration,
            "maps_url": self._maps_url(origin, [stops[i] for i in order]),
        }

    # ---- real (Google Routes API v2) ----------------------------------------
    async def _real(self, origin: dict[str, float], stops: list[dict[str, Any]]) -> dict[str, Any]:
        """Live traffic-aware optimization. Round-trip (destination = kitchen) so ALL
        deliveries are optimizable intermediates; we drop the return leg from the order."""
        def latlng(p: dict) -> dict:
            return {"location": {"latLng": {"latitude": float(p["lat"]), "longitude": float(p["lng"])}}}

        body = {
            "origin": latlng(origin),
            "destination": latlng(origin),
            "intermediates": [latlng(s) for s in stops],
            "travelMode": "DRIVE",
            "optimizeWaypointOrder": True,
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.optimizedIntermediateWaypointIndex",
        }
        res = await asyncio.to_thread(self._post, body, headers)
        route = res["routes"][0]
        order = route.get("optimizedIntermediateWaypointIndex") or list(range(len(stops)))
        dist_km = round(route.get("distanceMeters", 0) / 1000.0, 2)
        dur_mins = int(round(int(str(route.get("duration", "0s")).replace("s", "")) / 60.0))
        return {
            "mode": "REAL",
            "order": order,
            "total_distance_km": dist_km,
            "estimated_duration_mins": dur_mins,
            "maps_url": self._maps_url(origin, [stops[i] for i in order]),
        }

    def _post(self, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(ROUTES_URL, data=json.dumps(body).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ---- shared: a clickable Google Maps directions link (needs no API key) --
    def _maps_url(self, origin: dict[str, float], ordered_stops: list[dict[str, Any]]) -> str:
        base = f"https://www.google.com/maps/dir/?api=1&origin={origin['lat']},{origin['lng']}&travelmode=driving"
        if not ordered_stops:
            return base
        dest = ordered_stops[-1]
        url = base + f"&destination={dest['lat']},{dest['lng']}"
        waypoints = "|".join(f"{s['lat']},{s['lng']}" for s in ordered_stops[:-1])
        if waypoints:
            url += f"&waypoints={waypoints}"
        return url


# Singleton (mode decided by settings.google_maps_api_key at import time).
maps_service = MapsService()
