"""Multi-route behaviour of TrafficPlugin.

The ``routes`` array is addressed positionally in templates
(``{{traffic.routes.1.duration_minutes}}``), so which route ends up at which
index is part of the plugin's contract, not an implementation detail.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from plugins.traffic import TrafficPlugin

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"


def _manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _response(payload, status_code=200):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _ok(duration_s, static_s=None):
    static_s = duration_s if static_s is None else static_s
    return _response({"routes": [{"duration": f"{duration_s}s", "staticDuration": f"{static_s}s"}]})


def _fail(status_code=500):
    return _response(None, status_code=status_code)


def _config(*names):
    return {
        "api_key": "k",
        "routes": [{"origin": "H", "destination": n, "destination_name": n} for n in names],
    }


@pytest.fixture
def plugin():
    return TrafficPlugin(_manifest())


class TestRouteOrdering:
    def test_routes_keep_configuration_order(self, plugin):
        plugin.config = _config("WORK", "GYM", "SCHOOL")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(600), _ok(1200), _ok(1800)]):
            data = plugin.fetch_data().data

        assert [r["destination_name"] for r in data["routes"]] == ["WORK", "GYM", "SCHOOL"]
        assert [r["duration_minutes"] for r in data["routes"]] == [10, 20, 30]

    def test_each_route_gets_its_own_request(self, plugin):
        plugin.config = _config("WORK", "GYM")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(600), _ok(1200)]) as post:
            plugin.fetch_data()

        destinations = [call.kwargs["json"]["destination"]["address"] for call in post.call_args_list]
        assert destinations == ["WORK", "GYM"]

    def test_primary_variables_mirror_route_zero(self, plugin):
        plugin.config = _config("WORK", "GYM")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(600), _ok(1200)]):
            data = plugin.fetch_data().data

        first = data["routes"][0]
        for key in ("duration_minutes", "delay_minutes", "traffic_status", "traffic_color", "formatted"):
            assert data[key] == first[key]


class TestAggregates:
    def test_worst_delay_scans_every_route(self, plugin):
        plugin.config = _config("WORK", "GYM", "SCHOOL")
        responses = [_ok(600, 600), _ok(2400, 1800), _ok(1200, 1080)]
        with patch("plugins.traffic.requests.post", side_effect=responses):
            data = plugin.fetch_data().data

        assert data["worst_delay"] == 10  # GYM: 2400s vs 1800s
        assert data["delay_minutes"] == 0  # ...but the primary route is clear

    def test_worst_delay_is_zero_when_nothing_is_delayed(self, plugin):
        plugin.config = _config("WORK", "GYM")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(600), _ok(1200)]):
            data = plugin.fetch_data().data
        assert data["worst_delay"] == 0

    def test_route_count_matches_the_routes_array(self, plugin):
        plugin.config = _config("A", "B", "C")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(600), _ok(600), _ok(600)]):
            data = plugin.fetch_data().data
        assert data["route_count"] == len(data["routes"]) == 3


class TestSingleRoute:
    """The common case: one route configured, primary variables are all you need."""

    def test_one_route(self, plugin):
        plugin.config = _config("WORK")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(1500, 1200)]):
            data = plugin.fetch_data().data

        assert data["route_count"] == 1
        assert data["destination_name"] == "WORK"
        assert data["duration_minutes"] == 25
        assert data["delay_minutes"] == 5
        assert data["worst_delay"] == 5
        assert data["formatted"] == "WORK: 25m (+5m)"


class TestPartialFailure:
    """One route failing must not corrupt the others."""

    def test_a_failing_route_does_not_break_the_healthy_ones(self, plugin):
        plugin.config = _config("WORK", "GYM", "SCHOOL")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(600), _fail(), _ok(1800)]):
            result = plugin.fetch_data()

        assert result.available
        names = [r["destination_name"] for r in result.data["routes"]]
        assert "WORK" in names and "SCHOOL" in names

    def test_a_failing_route_does_not_renumber_the_others(self, plugin):
        """`{{traffic.routes.2.x}}` must keep meaning SCHOOL.

        The array is positional, so compacting out a failed route silently
        shows the user a different commute. See tests/test_travel_mode.py for
        the transit case that makes this a routine occurrence rather than an
        error path.
        """
        plugin.config = _config("WORK", "GYM", "SCHOOL")
        with patch("plugins.traffic.requests.post", side_effect=[_ok(600), _fail(), _ok(1800)]):
            routes = plugin.fetch_data().data["routes"]

        assert [r["destination_name"] for r in routes] == ["WORK", "GYM", "SCHOOL"]
        assert [r["available"] for r in routes] == [True, False, True]
        assert routes[2]["duration_minutes"] == 30

    def test_every_route_failing_is_reported_as_unavailable(self, plugin):
        plugin.config = _config("WORK", "GYM")
        with patch("plugins.traffic.requests.post", side_effect=[_fail(403), _fail(403)]):
            result = plugin.fetch_data()

        assert not result.available
        assert result.data is None
        assert "Failed to fetch any route data" in result.error
