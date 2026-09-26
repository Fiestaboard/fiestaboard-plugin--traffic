"""Board-geometry conformance for the Traffic plugin.

Exercises the plugin against every board shape FiestaBoard supports (Flagship,
Note, and note-arrays from 15x3 up to 120x24 -- what a FiestaPanel is) using
the shared conformance suite core holds its own plugins to.
"""

import json
from itertools import cycle
from pathlib import Path
from unittest.mock import Mock

from plugins.traffic import TrafficPlugin
from src.plugins.geometry_conformance import assert_board_conformance

MANIFEST = json.loads((Path(__file__).parent.parent / "manifest.json").read_text())

# The manifest caps `routes` at 4 (`settings_schema.properties.routes.maxItems`),
# a plugin-configured limit, not a board one -- so 4 configured routes is the
# most content this plugin can ever have to lay out. Using all 4, each with a
# delay, is what makes the narrowest board (a Note, 2 route slots after the
# title) "full" and therefore what makes the growth check meaningful: the
# panel-shaped rungs of the growth ladder must show strictly more of them.
_ROUTE_NAMES = ["WORK", "GYM", "SCHOOL", "AIRPORT"]

# One duration/delay pair per route, cycled -- varied so no two rendered lines
# are accidentally identical, which would make a truncation bug (two routes
# colliding on the same clipped text) invisible.
_DURATIONS = [(1800, 1500), (2700, 1800), (900, 900), (3600, 3000)]


def _routes_payload(duration_s: int, static_s: int) -> dict:
    return {"routes": [{"duration": f"{duration_s}s", "staticDuration": f"{static_s}s"}]}


def make_plugin_factory(monkeypatch):
    """A `make_plugin` factory (per the shared suite's contract) with the
    network stubbed: every `computeRoutes` call gets the next duration/delay
    pair in `_DURATIONS`, cycling so the suite's many renders never run out.
    """

    def make_plugin() -> TrafficPlugin:
        pairs = cycle(_DURATIONS)

        def _post(*args, **kwargs):
            duration_s, static_s = next(pairs)
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = _routes_payload(duration_s, static_s)
            return resp

        monkeypatch.setattr("plugins.traffic.requests.post", _post)

        plugin = TrafficPlugin(MANIFEST)
        plugin.config = {
            "api_key": "test_key",
            "routes": [
                {"origin": "Home", "destination": name, "destination_name": name}
                for name in _ROUTE_NAMES
            ],
        }
        return plugin

    return make_plugin


def test_renders_on_every_board_shape(monkeypatch):
    assert_board_conformance(
        make_plugin_factory(monkeypatch),
        manifest=MANIFEST,
        strict_growth=True,
        require_note_array_preview=True,
    )
