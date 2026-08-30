"""Tests for TrafficPlugin — this repository's plugin class.

These tests exercise ``__init__.py`` in this repo (imported as
``plugins.traffic`` via the symlink CI creates). They deliberately do *not*
import ``src.utils.traffic.TrafficSource``: that class lives in
Fiestaboard/FiestaBoard, is checked out only so this plugin can import
``PluginBase``, and testing it here proves nothing about this repository.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from plugins.traffic import Plugin, TrafficPlugin

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _response(payload, status_code=200):
    """A stand-in for a ``requests`` response carrying ``payload``."""
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _routes_payload(duration="1800s", static_duration="1800s"):
    return {"routes": [{"duration": duration, "staticDuration": static_duration}]}


@pytest.fixture
def plugin():
    """A plugin instance built from the real manifest, with no config yet."""
    return TrafficPlugin(_manifest())


@pytest.fixture
def configured(plugin):
    """A plugin with an API key and one route configured."""
    plugin.config = {
        "api_key": "test_key",
        "routes": [{"origin": "Home", "destination": "Work", "destination_name": "WORK"}],
    }
    return plugin


class TestPluginIdentity:
    """The plugin has to line up with its own manifest to load at all."""

    def test_plugin_id(self, plugin):
        assert plugin.plugin_id == "traffic"

    def test_plugin_id_matches_manifest(self, plugin):
        assert plugin.plugin_id == _manifest()["id"]

    def test_module_exports_plugin_alias(self):
        """The loader imports the module and looks for ``Plugin``."""
        assert Plugin is TrafficPlugin

    def test_info_comes_from_manifest(self, plugin):
        manifest = _manifest()
        assert plugin.info.id == manifest["id"]
        assert plugin.info.version == manifest["version"]

    def test_refresh_seconds_defaults_from_manifest(self, plugin):
        assert plugin.refresh_seconds == 300

    def test_refresh_seconds_clamped_to_manifest_floor(self, plugin):
        """``min_refresh_seconds`` is a hard floor, not a suggestion."""
        plugin.config = {"refresh_seconds": 5}
        assert plugin.refresh_seconds == 60


class TestValidateConfig:
    def test_valid(self, plugin):
        config = {"api_key": "k", "routes": [{"origin": "A", "destination": "B"}]}
        assert plugin.validate_config(config) == []

    def test_missing_api_key(self, plugin):
        errors = plugin.validate_config({"routes": [{}]})
        assert any("API key" in e for e in errors)

    def test_missing_routes(self, plugin):
        errors = plugin.validate_config({"api_key": "k"})
        assert any("route" in e for e in errors)

    def test_empty_routes_list_is_an_error(self, plugin):
        errors = plugin.validate_config({"api_key": "k", "routes": []})
        assert any("route" in e for e in errors)

    def test_empty_config_reports_both(self, plugin):
        assert len(plugin.validate_config({})) == 2


class TestTrafficStatus:
    """Thresholds are ``>``, so the boundary values themselves stay green/yellow."""

    @pytest.mark.parametrize(
        "index,status,color",
        [
            (0.5, "LIGHT", "{66}"),
            (1.0, "LIGHT", "{66}"),
            (1.2, "LIGHT", "{66}"),
            (1.21, "MODERATE", "{65}"),
            (1.5, "MODERATE", "{65}"),
            (1.51, "HEAVY", "{63}"),
            (3.0, "HEAVY", "{63}"),
        ],
    )
    def test_status_and_color(self, plugin, index, status, color):
        assert plugin._get_traffic_status(index) == (status, color)


class TestParseDuration:
    @pytest.mark.parametrize(
        "raw,expected",
        [("1800s", 1800), ("0s", 0), ("3600", 3600), ("", 0), (None, 0)],
    )
    def test_parses(self, plugin, raw, expected):
        assert plugin._parse_duration(raw) == expected

    def test_non_numeric_raises(self, plugin):
        """Callers must handle this; ``_fetch_single_route`` catches it."""
        with pytest.raises(ValueError):
            plugin._parse_duration("abcs")


class TestBuildWaypoint:
    def test_address(self, plugin):
        assert plugin._build_waypoint("123 Main St, City, ST") == {"address": "123 Main St, City, ST"}

    def test_lat_lng(self, plugin):
        wp = plugin._build_waypoint("37.7749,-122.4194")
        assert wp == {"location": {"latLng": {"latitude": 37.7749, "longitude": -122.4194}}}

    def test_lat_lng_tolerates_spaces(self, plugin):
        wp = plugin._build_waypoint("37.7749, -122.4194")
        assert wp["location"]["latLng"]["latitude"] == 37.7749

    def test_unparseable_pair_falls_back_to_address(self, plugin):
        assert plugin._build_waypoint("abc, def") == {"address": "abc, def"}

    def test_address_with_extra_commas_is_an_address(self, plugin):
        """Three comma-separated parts is an address, not a coordinate pair."""
        assert plugin._build_waypoint("1 Main St, Springfield, IL") == {
            "address": "1 Main St, Springfield, IL"
        }


class TestFetchSingleRouteRequest:
    """What we actually put on the wire."""

    def test_request_shape(self, configured):
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())) as post:
            configured._fetch_single_route("Home", "Work", "WORK")

        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        headers = post.call_args.kwargs["headers"]

        assert url == ROUTES_URL
        assert headers["X-Goog-Api-Key"] == "test_key"
        assert headers["X-Goog-FieldMask"] == "routes.duration,routes.staticDuration"
        assert body["origin"] == {"address": "Home"}
        assert body["destination"] == {"address": "Work"}
        assert body["travelMode"] == "DRIVE"
        assert body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"

    def test_request_has_a_timeout(self, configured):
        """A hung Google request must not stall the render loop."""
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())) as post:
            configured._fetch_single_route("Home", "Work", "WORK")
        assert post.call_args.kwargs["timeout"] == 10


class TestFetchSingleRouteResponse:
    def test_delay_and_status(self, configured):
        payload = _routes_payload(duration="2700s", static_duration="1800s")
        with patch("plugins.traffic.requests.post", return_value=_response(payload)):
            result = configured._fetch_single_route("Home", "Work", "WORK")

        assert result["duration_minutes"] == 45
        assert result["delay_minutes"] == 15
        assert result["traffic_status"] == "MODERATE"
        assert result["traffic_color"] == "{65}"
        assert result["destination_name"] == "WORK"
        assert result["formatted"] == "WORK: 45m (+15m)"

    def test_no_delay_omits_the_delay_suffix(self, configured):
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            result = configured._fetch_single_route("A", "B", "DEST")
        assert result["delay_minutes"] == 0
        assert result["formatted"] == "DEST: 30m"

    def test_faster_than_static_never_reports_negative_delay(self, configured):
        """Google can return a live duration below the static one."""
        payload = _routes_payload(duration="1500s", static_duration="1800s")
        with patch("plugins.traffic.requests.post", return_value=_response(payload)):
            result = configured._fetch_single_route("A", "B", "DEST")
        assert result["delay_minutes"] == 0
        assert result["traffic_status"] == "LIGHT"

    def test_missing_static_duration_falls_back_to_duration(self, configured):
        payload = _routes_payload(duration="1800s", static_duration="0s")
        with patch("plugins.traffic.requests.post", return_value=_response(payload)):
            result = configured._fetch_single_route("A", "B", "DEST")
        assert result["duration_minutes"] == 30
        assert result["delay_minutes"] == 0

    def test_only_the_first_route_alternative_is_used(self, configured):
        payload = {
            "routes": [
                {"duration": "600s", "staticDuration": "600s"},
                {"duration": "9999s", "staticDuration": "9999s"},
            ]
        }
        with patch("plugins.traffic.requests.post", return_value=_response(payload)):
            result = configured._fetch_single_route("A", "B", "DEST")
        assert result["duration_minutes"] == 10

    @pytest.mark.parametrize("status_code", [400, 403, 429, 500])
    def test_non_200_returns_none(self, configured, status_code):
        resp = _response(None, status_code=status_code)
        with patch("plugins.traffic.requests.post", return_value=resp):
            assert configured._fetch_single_route("A", "B", "DEST") is None

    def test_empty_routes_array_returns_none(self, configured):
        with patch("plugins.traffic.requests.post", return_value=_response({"routes": []})):
            assert configured._fetch_single_route("A", "B", "DEST") is None

    def test_missing_routes_key_returns_none(self, configured):
        with patch("plugins.traffic.requests.post", return_value=_response({})):
            assert configured._fetch_single_route("A", "B", "DEST") is None

    def test_network_error_returns_none(self, configured):
        with patch("plugins.traffic.requests.post", side_effect=OSError("boom")):
            assert configured._fetch_single_route("A", "B", "DEST") is None

    def test_unparseable_duration_returns_none(self, configured):
        payload = _routes_payload(duration="not-a-duration", static_duration="1800s")
        with patch("plugins.traffic.requests.post", return_value=_response(payload)):
            assert configured._fetch_single_route("A", "B", "DEST") is None


class TestFetchData:
    def test_no_routes_configured(self, plugin):
        plugin.config = {"api_key": "k"}
        result = plugin.fetch_data()
        assert not result.available
        assert result.error == "No routes configured"

    def test_success_populates_primary_and_aggregates(self, plugin):
        plugin.config = {
            "api_key": "k",
            "routes": [
                {"origin": "H", "destination": "W", "destination_name": "WORK"},
                {"origin": "H", "destination": "G", "destination_name": "GYM"},
            ],
        }
        payloads = [
            _response(_routes_payload(duration="1800s", static_duration="1800s")),
            _response(_routes_payload(duration="2700s", static_duration="1800s")),
        ]
        with patch("plugins.traffic.requests.post", side_effect=payloads):
            result = plugin.fetch_data()

        assert result.available
        data = result.data
        # Primary is the first configured route, not the worst one.
        assert data["destination_name"] == "WORK"
        assert data["duration_minutes"] == 30
        assert data["delay_minutes"] == 0
        assert data["route_count"] == 2
        # ...but worst_delay looks across every route.
        assert data["worst_delay"] == 15
        assert [r["destination_name"] for r in data["routes"]] == ["WORK", "GYM"]

    def test_default_destination_name(self, plugin):
        plugin.config = {"api_key": "k", "routes": [{"origin": "H", "destination": "W"}]}
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            result = plugin.fetch_data()
        assert result.data["destination_name"] == "DEST"

    def test_caps_at_four_routes(self, plugin):
        """``maxItems`` is 4 in the manifest and the code slices ``[:4]``."""
        plugin.config = {
            "api_key": "k",
            "routes": [
                {"origin": "H", "destination": f"D{i}", "destination_name": f"D{i}"} for i in range(6)
            ],
        }
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())) as post:
            result = plugin.fetch_data()
        assert post.call_count == 4
        assert result.data["route_count"] == 4

    def test_all_routes_failing_is_unavailable(self, plugin):
        plugin.config = {
            "api_key": "k",
            "routes": [
                {"origin": "A", "destination": "B", "destination_name": "ONE"},
                {"origin": "C", "destination": "D", "destination_name": "TWO"},
            ],
        }
        with patch("plugins.traffic.requests.post", return_value=_response(None, status_code=403)):
            result = plugin.fetch_data()
        assert not result.available
        assert "Failed to fetch any route data" in result.error

    def test_caches_the_last_successful_payload(self, configured):
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            result = configured.fetch_data()
        assert configured._cache == result.data


class TestGetFormattedDisplay:
    def test_uses_cache_when_present(self, plugin):
        plugin._cache = {"routes": [{"formatted": "HOME: 30m (+5m)"}, {"formatted": "WORK: 25m"}]}
        lines = plugin.get_formatted_display()
        assert len(lines) == 6
        assert lines[0] == "TRAFFIC".center(22)
        assert lines[1] == ""
        assert lines[2] == "HOME: 30m (+5m)"
        assert lines[3] == "WORK: 25m"
        assert lines[4:] == ["", ""]

    def test_fetches_when_cache_is_empty(self, configured):
        configured._cache = None
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            lines = configured.get_formatted_display()
        assert len(lines) == 6
        assert lines[2] == "WORK: 30m"

    def test_returns_none_when_fetch_fails(self, plugin):
        plugin.config = {"api_key": "k", "routes": []}
        assert plugin.get_formatted_display() is None

    def test_truncates_to_board_width(self, plugin):
        plugin._cache = {"routes": [{"formatted": "X" * 40}]}
        lines = plugin.get_formatted_display()
        assert all(len(line) <= 22 for line in lines)

    def test_never_shows_more_than_four_routes(self, plugin):
        plugin._cache = {"routes": [{"formatted": f"R{i}"} for i in range(6)]}
        lines = plugin.get_formatted_display()
        assert len(lines) == 6
        assert lines[-1] == "R3"


class TestManifestMetadata:
    """The manifest is a contract with core's settings form and variable picker."""

    @pytest.fixture(autouse=True)
    def load_manifest(self):
        self.manifest = _manifest()
        self.variables = self.manifest["variables"]

    def test_required_top_level_fields(self):
        for field in ("id", "name", "version", "variables"):
            assert field in self.manifest, f"Missing required field: {field}"

    def test_version_is_semver(self):
        parts = self.manifest["version"].split(".")
        assert len(parts) == 3 and all(p.isdigit() for p in parts)

    def test_simple_variables_are_dicts(self):
        assert isinstance(self.variables["simple"], dict), "variables.simple must be a dict, not a list"

    def test_simple_variable_required_keys(self):
        required = {"description", "type", "max_length", "group", "example"}
        for var_name, meta in self.variables["simple"].items():
            missing = required - set(meta.keys())
            assert not missing, f"{var_name} missing keys: {missing}"

    def test_groups_defined(self):
        assert self.variables.get("groups")

    def test_simple_variables_reference_valid_groups(self):
        groups = set(self.variables["groups"])
        for var_name, meta in self.variables["simple"].items():
            assert meta["group"] in groups, f"{var_name} references unknown group '{meta['group']}'"

    def test_array_item_fields_reference_simple_vars(self):
        simple_keys = set(self.variables["simple"])
        for arr_name, arr_meta in self.variables.get("arrays", {}).items():
            for field in arr_meta.get("item_fields", []):
                assert field in simple_keys, f"arrays.{arr_name} references unknown field '{field}'"

    def test_variable_types_valid(self):
        valid_types = {"string", "number", "boolean"}
        for var_name, meta in self.variables["simple"].items():
            assert meta["type"] in valid_types, f"{var_name} has invalid type '{meta['type']}'"

    def test_max_length_positive(self):
        for var_name, meta in self.variables["simple"].items():
            assert meta["max_length"] > 0, f"{var_name} max_length must be positive"

    def test_example_values_present(self):
        for var_name, meta in self.variables["simple"].items():
            assert meta["example"], f"{var_name} must have a non-empty example"

    def test_declared_simple_variables_are_actually_produced(self, plugin):
        """Every ``variables.simple`` key must appear in ``fetch_data``'s payload.

        A variable the picker offers but the plugin never emits renders as
        ``???`` on the board.
        """
        plugin.config = {
            "api_key": "k",
            "routes": [{"origin": "H", "destination": "W", "destination_name": "WORK"}],
        }
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            result = plugin.fetch_data()

        missing = set(self.variables["simple"]) - set(result.data)
        assert not missing, f"manifest declares variables fetch_data never emits: {sorted(missing)}"

    def test_declared_route_item_fields_are_actually_produced(self, plugin):
        plugin.config = {
            "api_key": "k",
            "routes": [{"origin": "H", "destination": "W", "destination_name": "WORK"}],
        }
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            result = plugin.fetch_data()

        declared = set(self.manifest["variables"]["arrays"]["routes"]["item_fields"])
        produced = set(result.data["routes"][0])
        assert not declared - produced, f"routes items missing: {sorted(declared - produced)}"

    def test_color_rule_values_match_the_statuses_the_code_emits(self, plugin):
        """``color_rules_schema`` matches on exact strings from ``_get_traffic_status``."""
        rules = self.manifest["color_rules_schema"]["traffic_status"]["default_rules"]
        rule_values = {r["value"] for r in rules}
        emitted = {plugin._get_traffic_status(i)[0] for i in (1.0, 1.3, 2.0)}
        assert emitted <= rule_values, f"statuses with no colour rule: {sorted(emitted - rule_values)}"

    def test_max_lengths_keys_reference_real_route_fields(self):
        item_fields = set(self.manifest["variables"]["arrays"]["routes"]["item_fields"])
        for key in self.manifest["max_lengths"]:
            prefix, _, field = key.partition(".*.")
            assert prefix == "routes", f"max_lengths key '{key}' targets an unknown array"
            assert field in item_fields, f"max_lengths key '{key}' targets an unknown field"
