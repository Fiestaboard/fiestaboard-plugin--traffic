"""Per-route travel mode.

Three things make this more than "pass a string through":

1. ``routingPreference`` is only valid for DRIVE and TWO_WHEELER. Sending it
   with WALK/BICYCLE/TRANSIT is a 400 from Google, so the gate is correctness,
   not tuning.
2. Without ``routingPreference``, Google returns ``duration == staticDuration``,
   so those modes always read as LIGHT with zero delay. That is deliberate and
   is asserted here so nobody "fixes" it into fabricated numbers.
3. TRANSIT can legitimately return an empty ``routes`` array (no service at
   this hour). ``routes`` is addressed positionally in templates, so that route
   has to keep its index.
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from plugins.traffic import TrafficPlugin

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"

NON_TRAFFIC_MODES = ["BICYCLE", "TRANSIT", "WALK"]
TRAFFIC_MODES = ["DRIVE", "TWO_WHEELER"]
ALL_MODES = TRAFFIC_MODES + NON_TRAFFIC_MODES


def _manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def _response(payload, status_code=200):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


def _routes_payload(duration="1800s", static_duration="1800s"):
    return {"routes": [{"duration": duration, "staticDuration": static_duration}]}


@pytest.fixture
def plugin():
    p = TrafficPlugin(_manifest())
    p.config = {"api_key": "test_key"}
    return p


def _body_for(plugin, **kwargs):
    """Run one fetch and hand back the JSON body that went to Google."""
    with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())) as post:
        plugin._fetch_single_route("Home", "Work", "WORK", **kwargs)
    return post.call_args.kwargs["json"]


class TestTravelModeReachesTheRequest:
    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_mode_is_sent_verbatim(self, plugin, mode):
        assert _body_for(plugin, travel_mode=mode)["travelMode"] == mode

    def test_defaults_to_drive(self, plugin):
        assert _body_for(plugin)["travelMode"] == "DRIVE"

    @pytest.mark.parametrize("given", ["drive", "  Transit  ", "wAlK"])
    def test_case_and_whitespace_are_normalised(self, plugin, given):
        assert _body_for(plugin, travel_mode=given)["travelMode"] == given.strip().upper()

    @pytest.mark.parametrize("given", ["SUBMARINE", "", None, "DRIVE ", 7])
    def test_unrecognised_values_fall_back_to_drive(self, plugin, given):
        """A bad value in a hand-edited config degrades the route, not the board.

        ``"DRIVE "`` is in this list because it *does* normalise to DRIVE — the
        point is that nothing here reaches Google as an invalid travelMode.
        """
        assert _body_for(plugin, travel_mode=given)["travelMode"] == "DRIVE"


class TestRoutingPreferenceGate:
    """Gotcha 1: sending routingPreference with the wrong mode is a 400."""

    @pytest.mark.parametrize("mode", TRAFFIC_MODES)
    def test_traffic_aware_modes_ask_for_live_traffic(self, plugin, mode):
        body = _body_for(plugin, travel_mode=mode)
        assert body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"

    @pytest.mark.parametrize("mode", NON_TRAFFIC_MODES)
    def test_other_modes_omit_routing_preference_entirely(self, plugin, mode):
        """Not set to something else — absent. Google rejects the key itself."""
        assert "routingPreference" not in _body_for(plugin, travel_mode=mode)

    def test_gate_matches_the_declared_constant(self, plugin):
        assert set(plugin.TRAFFIC_AWARE_MODES) == {"DRIVE", "TWO_WHEELER"}
        assert set(plugin.TRAFFIC_AWARE_MODES) <= set(plugin.VALID_TRAVEL_MODES)

    def test_invalid_mode_still_produces_a_legal_request(self, plugin):
        """Fallback is DRIVE, so the preference comes back with it."""
        body = _body_for(plugin, travel_mode="TELEPORT")
        assert body["travelMode"] == "DRIVE"
        assert body["routingPreference"] == "TRAFFIC_AWARE_OPTIMAL"


class TestFlatTrafficForNonDriveModes:
    """Gotcha 2: no routingPreference means no traffic signal. Left as-is."""

    @pytest.mark.parametrize("mode", NON_TRAFFIC_MODES)
    def test_walk_bike_transit_report_no_delay(self, plugin, mode):
        # Google echoes duration == staticDuration when not asked for traffic.
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload("1200s", "1200s"))):
            result = plugin._fetch_single_route("H", "W", "WORK", travel_mode=mode)

        assert result["duration_minutes"] == 20
        assert result["delay_minutes"] == 0
        assert result["traffic_status"] == "LIGHT"
        assert result["traffic_color"] == "{66}"
        assert result["formatted"] == "WORK: 20m"

    def test_drive_still_reports_delay(self, plugin):
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload("2700s", "1800s"))):
            result = plugin._fetch_single_route("H", "W", "WORK", travel_mode="DRIVE")
        assert result["delay_minutes"] == 15
        assert result["traffic_status"] == "MODERATE"


class TestTravelModeInTheResult:
    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_result_reports_the_mode_used(self, plugin, mode):
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            result = plugin._fetch_single_route("H", "W", "WORK", travel_mode=mode)
        assert result["travel_mode"] == mode
        assert result["available"] is True

    def test_result_reports_the_fallback_not_the_input(self, plugin):
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            result = plugin._fetch_single_route("H", "W", "WORK", travel_mode="HOVERBOARD")
        assert result["travel_mode"] == "DRIVE"


class TestPerRouteModes:
    """Each configured route carries its own mode."""

    def test_modes_are_not_shared_between_routes(self, plugin):
        plugin.config = {
            "api_key": "k",
            "routes": [
                {"origin": "H", "destination": "W", "destination_name": "DRIVE", "travel_mode": "DRIVE"},
                {"origin": "H", "destination": "W", "destination_name": "BIKE", "travel_mode": "BICYCLE"},
                {"origin": "H", "destination": "W", "destination_name": "MUNI", "travel_mode": "TRANSIT"},
            ],
        }
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())) as post:
            data = plugin.fetch_data().data

        bodies = [call.kwargs["json"] for call in post.call_args_list]
        assert [b["travelMode"] for b in bodies] == ["DRIVE", "BICYCLE", "TRANSIT"]
        assert [("routingPreference" in b) for b in bodies] == [True, False, False]
        assert [r["travel_mode"] for r in data["routes"]] == ["DRIVE", "BICYCLE", "TRANSIT"]

    def test_route_without_travel_mode_still_works(self, plugin):
        """Backward compatibility: configs saved before this field existed."""
        plugin.config = {
            "api_key": "k",
            "routes": [{"origin": "H", "destination": "W", "destination_name": "WORK"}],
        }
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())) as post:
            data = plugin.fetch_data().data

        assert post.call_args.kwargs["json"]["travelMode"] == "DRIVE"
        assert data["travel_mode"] == "DRIVE"

    def test_primary_travel_mode_mirrors_route_zero(self, plugin):
        plugin.config = {
            "api_key": "k",
            "routes": [
                {"origin": "H", "destination": "W", "destination_name": "BIKE", "travel_mode": "BICYCLE"},
                {"origin": "H", "destination": "W", "destination_name": "WORK", "travel_mode": "DRIVE"},
            ],
        }
        with patch("plugins.traffic.requests.post", return_value=_response(_routes_payload())):
            data = plugin.fetch_data().data
        assert data["travel_mode"] == "BICYCLE"


class TestValidateConfigTravelMode:
    def test_valid_modes_accepted(self, plugin):
        for mode in ALL_MODES:
            config = {"api_key": "k", "routes": [{"origin": "A", "destination": "B", "travel_mode": mode}]}
            assert plugin.validate_config(config) == []

    def test_absent_mode_accepted(self, plugin):
        config = {"api_key": "k", "routes": [{"origin": "A", "destination": "B"}]}
        assert plugin.validate_config(config) == []

    def test_empty_mode_accepted(self, plugin):
        config = {"api_key": "k", "routes": [{"origin": "A", "destination": "B", "travel_mode": ""}]}
        assert plugin.validate_config(config) == []

    def test_lowercase_accepted(self, plugin):
        config = {"api_key": "k", "routes": [{"origin": "A", "destination": "B", "travel_mode": "walk"}]}
        assert plugin.validate_config(config) == []

    def test_invalid_mode_reported_with_its_route_number(self, plugin):
        config = {
            "api_key": "k",
            "routes": [
                {"origin": "A", "destination": "B", "travel_mode": "DRIVE"},
                {"origin": "A", "destination": "B", "travel_mode": "TELEPORT"},
            ],
        }
        errors = plugin.validate_config(config)
        assert len(errors) == 1
        assert "Route 2" in errors[0] and "TELEPORT" in errors[0]

    def test_non_dict_route_is_skipped_not_crashed_on(self, plugin):
        errors = plugin.validate_config({"api_key": "k", "routes": ["nonsense"]})
        assert errors == []


class TestUnavailableRoutesKeepTheirPosition:
    """Gotcha 3: dropping a route renumbers every route after it."""

    def _three_routes(self, plugin):
        plugin.config = {
            "api_key": "k",
            "routes": [
                {"origin": "H", "destination": "W", "destination_name": "WORK", "travel_mode": "DRIVE"},
                {"origin": "H", "destination": "W", "destination_name": "MUNI", "travel_mode": "TRANSIT"},
                {"origin": "H", "destination": "W", "destination_name": "BIKE", "travel_mode": "BICYCLE"},
            ],
        }

    def test_transit_with_no_service_does_not_shift_later_routes(self, plugin):
        self._three_routes(plugin)
        responses = [
            _response(_routes_payload("1800s", "1500s")),
            _response({"routes": []}),  # no transit service at this hour
            _response(_routes_payload("2400s", "2400s")),
        ]
        with patch("plugins.traffic.requests.post", side_effect=responses):
            data = plugin.fetch_data().data

        assert [r["destination_name"] for r in data["routes"]] == ["WORK", "MUNI", "BIKE"]
        # The bike route is still at index 2, where the user's template put it.
        assert data["routes"][2]["duration_minutes"] == 40
        assert data["route_count"] == 3

    def test_placeholder_reports_no_numbers(self, plugin):
        self._three_routes(plugin)
        responses = [
            _response(_routes_payload()),
            _response({"routes": []}),
            _response(_routes_payload()),
        ]
        with patch("plugins.traffic.requests.post", side_effect=responses):
            data = plugin.fetch_data().data

        missing = data["routes"][1]
        assert missing["available"] is False
        # None, not 0. The template engine renders None as "???"; 0 would be a
        # lie ("this trip takes no time").
        assert missing["duration_minutes"] is None
        assert missing["delay_minutes"] is None
        assert missing["destination_name"] == "MUNI"
        assert missing["travel_mode"] == "TRANSIT"
        assert missing["formatted"] == "MUNI: NO DATA"

    def test_placeholder_carries_no_traffic_colour(self, plugin):
        """`UNKNOWN` matches no rule in color_rules_schema, so no tile is drawn."""
        self._three_routes(plugin)
        with patch("plugins.traffic.requests.post", side_effect=[
            _response(_routes_payload()), _response({"routes": []}), _response(_routes_payload())
        ]):
            data = plugin.fetch_data().data

        assert data["routes"][1]["traffic_status"] == "UNKNOWN"
        assert data["routes"][1]["traffic_color"] == ""

        rules = _manifest()["color_rules_schema"]["traffic_status"]["default_rules"]
        assert "UNKNOWN" not in {r["value"] for r in rules}

    def test_an_http_failure_also_keeps_its_slot(self, plugin):
        self._three_routes(plugin)
        with patch("plugins.traffic.requests.post", side_effect=[
            _response(_routes_payload()), _response(None, status_code=500), _response(_routes_payload())
        ]):
            data = plugin.fetch_data().data
        assert [r["available"] for r in data["routes"]] == [True, False, True]

    def test_worst_delay_ignores_unavailable_routes(self, plugin):
        """`max()` over a None delay would raise; over placeholders it would lie."""
        self._three_routes(plugin)
        with patch("plugins.traffic.requests.post", side_effect=[
            _response(_routes_payload("1800s", "1500s")),
            _response({"routes": []}),
            _response(_routes_payload("2400s", "2400s")),
        ]):
            data = plugin.fetch_data().data
        assert data["worst_delay"] == 5

    def test_primary_route_unavailable_still_reports_the_others(self, plugin):
        self._three_routes(plugin)
        with patch("plugins.traffic.requests.post", side_effect=[
            _response({"routes": []}), _response(_routes_payload()), _response(_routes_payload())
        ]):
            result = plugin.fetch_data()

        assert result.available is True
        assert result.data["available"] is False
        assert result.data["duration_minutes"] is None
        assert result.data["destination_name"] == "WORK"

    def test_every_route_unavailable_is_still_a_failure(self, plugin):
        self._three_routes(plugin)
        with patch("plugins.traffic.requests.post", return_value=_response({"routes": []})):
            result = plugin.fetch_data()
        assert result.available is False
        assert "Failed to fetch any route data" in result.error

    def test_formatted_display_renders_the_placeholder(self, plugin):
        self._three_routes(plugin)
        with patch("plugins.traffic.requests.post", side_effect=[
            _response(_routes_payload()), _response({"routes": []}), _response(_routes_payload())
        ]):
            lines = plugin.get_formatted_display()

        assert lines[3] == "MUNI: NO DATA"
        assert all(len(line) <= 22 for line in lines)

    def test_placeholder_line_fits_the_board(self, plugin):
        """`destination_name` is capped at 10 in the manifest; 10 + ": NO DATA" = 19."""
        placeholder = plugin._unavailable_route("A" * 10, "TRANSIT")
        assert len(placeholder["formatted"]) <= 22


class TestManifestDeclaresTravelMode:
    """The settings form is generated from the manifest, so this is the feature."""

    @pytest.fixture(autouse=True)
    def load(self):
        self.manifest = _manifest()
        self.prop = self.manifest["settings_schema"]["properties"]["routes"]["items"]["properties"]["travel_mode"]

    def test_enum_matches_the_code(self, plugin):
        assert self.prop["enum"] == list(plugin.VALID_TRAVEL_MODES)

    def test_enum_names_are_index_parallel(self):
        assert len(self.prop["enumNames"]) == len(self.prop["enum"])

    def test_two_wheeler_has_a_readable_label(self):
        """The renderer only capitalises the first character, so TWO_WHEELER
        would otherwise render verbatim."""
        labels = dict(zip(self.prop["enum"], self.prop["enumNames"]))
        assert labels["TWO_WHEELER"] == "Motorcycle / Scooter"
        assert "_" not in "".join(self.prop["enumNames"])

    def test_defaults_to_drive(self, plugin):
        assert self.prop["default"] == plugin.DEFAULT_TRAVEL_MODE == "DRIVE"

    def test_not_required(self):
        """Existing saved configs have no travel_mode and must keep loading."""
        required = self.manifest["settings_schema"]["properties"]["routes"]["items"]["required"]
        assert "travel_mode" not in required

    def test_uses_plain_enum_not_oneof_or_a_widget(self):
        """House pattern: core's schema-form builds the Select from `enum`."""
        assert "oneOf" not in self.prop
        assert "ui:widget" not in self.prop
