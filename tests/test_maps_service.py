"""Tests for the route-optimization service (mock nearest-neighbour path).

The real Google Routes API path needs a key + network, so it's not exercised
here; these lock the offline mock behaviour used on the dev machine.
"""

import pytest

from app.services.maps_service import MapsService
from app.tools.master_tools import _call_maps_route

# A kitchen and 3 deliveries laid out roughly west->east so the optimal visit
# order from the kitchen is unambiguous.
KITCHEN = {"lat": 19.1240, "lng": 73.0000}
STOPS = [
    {"lat": 19.1240, "lng": 73.0300, "id": "far"},     # farthest east
    {"lat": 19.1240, "lng": 73.0050, "id": "near"},     # nearest
    {"lat": 19.1240, "lng": 73.0150, "id": "mid"},      # middle
]


@pytest.mark.asyncio
async def test_mock_orders_nearest_first():
    svc = MapsService(api_key="")            # force mock
    res = await svc.optimize_route(origin=KITCHEN, stops=STOPS)
    assert res["mode"] == "MOCK"
    # nearest-neighbour from the kitchen -> near, mid, far
    assert [STOPS[i]["id"] for i in res["order"]] == ["near", "mid", "far"]
    assert res["total_distance_km"] > 0
    assert res["estimated_duration_mins"] >= 1


@pytest.mark.asyncio
async def test_mock_maps_url_has_origin_and_waypoints():
    svc = MapsService(api_key="")
    res = await svc.optimize_route(origin=KITCHEN, stops=STOPS)
    url = res["maps_url"]
    assert url.startswith("https://www.google.com/maps/dir/")
    assert f"origin={KITCHEN['lat']},{KITCHEN['lng']}" in url
    assert "destination=" in url and "waypoints=" in url


@pytest.mark.asyncio
async def test_empty_stops():
    svc = MapsService(api_key="")
    res = await svc.optimize_route(origin=KITCHEN, stops=[])
    assert res["mode"] == "EMPTY"
    assert res["order"] == []
    assert res["total_distance_km"] == 0.0


@pytest.mark.asyncio
async def test_call_maps_route_helper_delegates():
    res = await _call_maps_route(origin=KITCHEN, stops=STOPS)
    assert "order" in res and "maps_url" in res and "mode" in res
