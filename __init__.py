"""Traffic plugin for FiestaBoard.

Displays commute times using Google Routes API.
"""

from typing import Any, Dict, List, Optional, Tuple
import logging
import re
import requests

from src.devices import BoardContext
from src.plugins.base import PluginBase, PluginResult
from src.text_to_board import count_tiles, take_tiles

logger = logging.getLogger(__name__)

#: Fallback geometry used when ``self.board`` is unbound (legacy callers, unit
#: tests, and anything rendered outside a board-scoped ``get_data()`` call).
#: A Flagship, because that has always been this plugin's assumed shape.
_DEFAULT_BOARD = BoardContext(device_type="flagship", rows=6, cols=22)

#: Trailing "(+Xm)" delay annotation, dropped first when a formatted route
#: line has to shrink to fit a narrower board.
_DELAY_SUFFIX_RE = re.compile(r"\s*\(\+\d+m\)$")


class TrafficPlugin(PluginBase):
    """Traffic and commute time plugin.
    
    Fetches route times from Google Routes API.
    """
    
    TRAFFIC_INDEX_YELLOW = 1.2
    TRAFFIC_INDEX_RED = 1.5

    #: Travel modes the Routes API accepts on ``computeRoutes``.
    VALID_TRAVEL_MODES = ("DRIVE", "BICYCLE", "TRANSIT", "WALK", "TWO_WHEELER")

    DEFAULT_TRAVEL_MODE = "DRIVE"

    #: Modes that accept ``routingPreference``.  Sending it with any other mode
    #: is a 400 from Google, not a soft ignore -- so this is a hard gate, not an
    #: optimisation.  Their upside is that they are also the only modes with a
    #: live traffic model; see ``_fetch_single_route``.
    TRAFFIC_AWARE_MODES = ("DRIVE", "TWO_WHEELER")

    def __init__(self, manifest: Dict[str, Any]):
        """Initialize the traffic plugin."""
        super().__init__(manifest)
        self._cache: Optional[Dict[str, Any]] = None
    
    @property
    def plugin_id(self) -> str:
        return "traffic"
    
    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate traffic configuration."""
        errors = []

        if not config.get("api_key"):
            errors.append("Google Routes API key is required")

        routes = config.get("routes", [])
        if not routes:
            errors.append("At least one route is required")

        for index, route in enumerate(routes):
            if not isinstance(route, dict):
                continue
            mode = route.get("travel_mode")
            # Absent is fine -- travel_mode is optional and defaults to DRIVE,
            # so configs saved before this field existed stay valid.
            if mode in (None, ""):
                continue
            if str(mode).strip().upper() not in self.VALID_TRAVEL_MODES:
                errors.append(
                    f"Route {index + 1}: invalid travel mode '{mode}' "
                    f"(expected one of {', '.join(self.VALID_TRAVEL_MODES)})"
                )

        return errors

    def _normalize_travel_mode(self, travel_mode: Optional[str]) -> str:
        """Coerce a configured travel mode to one the Routes API accepts.

        Anything unrecognised falls back to DRIVE with a warning rather than
        failing the route: a bad value in a hand-edited config should degrade
        the board, not blank it.  ``validate_config`` reports the same value as
        an error so it is visible at save time.
        """
        mode = str(travel_mode or "").strip().upper()
        if mode not in self.VALID_TRAVEL_MODES:
            if mode:
                logger.warning(
                    f"Invalid travel mode '{travel_mode}', falling back to {self.DEFAULT_TRAVEL_MODE}"
                )
            return self.DEFAULT_TRAVEL_MODE
        return mode

    def _unavailable_route(self, destination_name: str, travel_mode: str) -> Dict[str, Any]:
        """A placeholder that holds a route's slot in the ``routes`` array.

        ``routes`` is addressed positionally in templates
        (``{{traffic.routes.2.duration_minutes}}``), so dropping a route that
        returned nothing would silently slide every later route up an index and
        show the user the wrong commute.  A route that has no answer right now
        keeps its position and reports no numbers: the template engine renders
        ``None`` as ``???``, which is its established "no value" output.

        ``formatted`` is left at its natural length here -- it is raw route
        data, not board output.  Sizing it to a specific board is the
        formatter's job (:meth:`_format_route_line`), not this method's; a
        placeholder that pre-truncated to one board's width would render
        wrong on every other board.
        """
        return {
            "duration_minutes": None,
            "delay_minutes": None,
            "traffic_status": "UNKNOWN",
            "traffic_color": "",
            "destination_name": destination_name,
            "formatted": f"{destination_name}: NO DATA",
            "travel_mode": travel_mode,
            "available": False,
        }

    def _get_traffic_status(self, traffic_index: float) -> Tuple[str, str]:
        """Get traffic status and color."""
        if traffic_index > self.TRAFFIC_INDEX_RED:
            return "HEAVY", "{63}"  # red
        elif traffic_index > self.TRAFFIC_INDEX_YELLOW:
            return "MODERATE", "{65}"  # yellow
        else:
            return "LIGHT", "{66}"  # green
    
    def _parse_duration(self, duration_str: str) -> int:
        """Parse duration string (e.g., '1234s') to seconds."""
        if not duration_str:
            return 0
        return int(duration_str.rstrip('s'))
    
    def _build_waypoint(self, location: str) -> Dict:
        """Build waypoint for API request."""
        if "," in location:
            parts = location.split(",")
            if len(parts) == 2:
                try:
                    lat = float(parts[0].strip())
                    lng = float(parts[1].strip())
                    return {
                        "location": {
                            "latLng": {"latitude": lat, "longitude": lng}
                        }
                    }
                except ValueError:
                    pass
        return {"address": location}
    
    def _fetch_single_route(
        self,
        origin: str,
        destination: str,
        destination_name: str,
        travel_mode: str = DEFAULT_TRAVEL_MODE,
    ) -> Optional[Dict]:
        """Fetch traffic data for a single route."""
        api_key = self.config.get("api_key")
        mode = self._normalize_travel_mode(travel_mode)

        url = "https://routes.googleapis.com/directions/v2:computeRoutes"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "routes.duration,routes.staticDuration"
        }

        body = {
            "origin": self._build_waypoint(origin),
            "destination": self._build_waypoint(destination),
            "travelMode": mode,
        }

        # routingPreference is only valid for DRIVE and TWO_WHEELER; Google
        # rejects the whole request with a 400 for the other modes.  The cost of
        # leaving it off is that `duration` comes back equal to
        # `staticDuration`, so the traffic index is 1.0 and every walk/bike/
        # transit route reads as LIGHT with zero delay.  That is honest -- those
        # modes have no traffic model to report -- and is documented in
        # docs/SETUP.md rather than papered over with invented numbers.
        if mode in self.TRAFFIC_AWARE_MODES:
            body["routingPreference"] = "TRAFFIC_AWARE_OPTIMAL"

        try:
            response = requests.post(url, json=body, headers=headers, timeout=10)
            
            if response.status_code != 200:
                logger.error(f"Google Routes API error: {response.status_code}")
                return None
            
            data = response.json()
            if not data.get("routes"):
                return None
            
            route = data["routes"][0]
            duration = self._parse_duration(route.get("duration", "0s"))
            static_duration = self._parse_duration(route.get("staticDuration", "0s"))
            
            if static_duration == 0:
                static_duration = duration
            
            # Calculate traffic index
            traffic_index = duration / static_duration if static_duration > 0 else 1.0
            traffic_status, traffic_color = self._get_traffic_status(traffic_index)
            
            # Calculate delay
            delay_seconds = max(0, duration - static_duration)
            delay_minutes = round(delay_seconds / 60)
            duration_minutes = round(duration / 60)
            
            # Format message
            if delay_minutes > 0:
                formatted = f"{destination_name}: {duration_minutes}m (+{delay_minutes}m)"
            else:
                formatted = f"{destination_name}: {duration_minutes}m"
            
            return {
                "duration_minutes": duration_minutes,
                "delay_minutes": delay_minutes,
                "traffic_status": traffic_status,
                "traffic_color": traffic_color,
                "destination_name": destination_name,
                "formatted": formatted,
                "travel_mode": mode,
                "available": True,
            }

        except Exception as e:
            logger.error(f"Error fetching traffic for {destination_name}: {e}")
            return None
    
    def fetch_data(self) -> PluginResult:
        """Fetch traffic data for all configured routes."""
        routes_config = self.config.get("routes", [])
        if not routes_config:
            return PluginResult(
                available=False,
                error="No routes configured"
            )
        
        routes_data = []
        for route in routes_config[:4]:
            destination_name = route.get("destination_name", "DEST")
            travel_mode = self._normalize_travel_mode(route.get("travel_mode"))
            route_data = self._fetch_single_route(
                origin=route.get("origin", ""),
                destination=route.get("destination", ""),
                destination_name=destination_name,
                travel_mode=travel_mode,
            )
            # A route with no answer keeps its slot.  Transit in particular can
            # legitimately return an empty ``routes`` array when there is no
            # service at this hour, and dropping it would renumber every route
            # after it.
            routes_data.append(route_data or self._unavailable_route(destination_name, travel_mode))

        available_routes = [route for route in routes_data if route["available"]]
        if not available_routes:
            return PluginResult(
                available=False,
                error="Failed to fetch any route data"
            )

        # Find worst delay (only routes that reported one can have a worst)
        worst = max(available_routes, key=lambda r: r["delay_minutes"])

        # Primary route
        primary = routes_data[0]

        data = {
            # Primary route
            "duration_minutes": primary["duration_minutes"],
            "delay_minutes": primary["delay_minutes"],
            "traffic_status": primary["traffic_status"],
            "traffic_color": primary["traffic_color"],
            "destination_name": primary["destination_name"],
            "formatted": primary["formatted"],
            "travel_mode": primary["travel_mode"],
            "available": primary["available"],
            # Aggregates
            "route_count": len(routes_data),
            "worst_delay": worst["delay_minutes"],
            # Array
            "routes": routes_data,
        }

        # `self._cache` holds these raw route facts only -- never rendered
        # lines. The facts are the same regardless of which board asked for
        # them; `formatted_lines` below is rendered fresh from `self.board`
        # every call, so there is nothing here to key by geometry.
        self._cache = data

        rows, cols = self._board_dims()
        formatted_lines = self._build_display_lines(routes_data, rows, cols)
        return PluginResult(available=True, data=data, formatted_lines=formatted_lines)
    
    def _board_dims(self) -> Tuple[int, int]:
        """Rows/cols of the board currently being rendered.

        ``self.board`` is unset outside a board-scoped render (unit tests,
        legacy callers); a Flagship is the historical assumption for those,
        so it is the fallback rather than a crash.
        """
        board = self.board
        if board is None:
            return _DEFAULT_BOARD.rows, _DEFAULT_BOARD.cols
        return board.rows, board.cols

    def _format_route_line(self, route: Dict[str, Any], cols: int) -> str:
        """Render one route's line, shrinking to fit *cols* before truncating.

        ``route["formatted"]`` is sized for nothing in particular -- it is
        raw route data, reused as-is for the ``{{traffic.formatted}}``
        template variable. A Note (15 cols) can be narrower than that
        string, so this reflows it: drop the "(+Xm)" delay note, then the
        ": " separator, before falling back to a hard tile-aware truncation
        that is at least guaranteed to fit.
        """
        text = str(route.get("formatted") or "")
        if count_tiles(text) <= cols:
            return text

        without_delay = _DELAY_SUFFIX_RE.sub("", text)
        if count_tiles(without_delay) <= cols:
            return without_delay

        compact = without_delay.replace(": ", " ", 1)
        if count_tiles(compact) <= cols:
            return compact

        head, _ = take_tiles(compact, cols)
        return head

    def _build_display_lines(self, routes: List[Dict[str, Any]], rows: int, cols: int) -> List[str]:
        """Lay ``routes`` out on a ``rows`` x ``cols`` board.

        A title, then one line per route -- reflowed in both axes rather than
        truncated: a taller board shows more routes (bounded only by how many
        exist; the ``[:4]`` here is a defensive no-op against the manifest's
        own ``maxItems`` cap, a config limit, not a board one -- see
        ``fetch_data``), and a narrower board shrinks each route's text
        (:meth:`_format_route_line`) instead of clipping it.

        Shared by :meth:`fetch_data` (the live path -- ``PluginResult.
        formatted_lines``, used when this plugin is placed as a raw display
        rather than through template variables) and :meth:`get_formatted_display`
        (the documented hook, currently uncalled by core but held to the same
        contract), so the two can never drift apart.
        """
        routes = routes[:4]

        header = "TRAFFIC".center(cols)
        available_rows = max(rows - 1, 0)
        shown_routes = routes[:available_rows]

        lines = [header] + [self._format_route_line(route, cols) for route in shown_routes]
        while len(lines) < rows:
            lines.append("")

        # Cosmetic spacer: when there is slack left over after the header and
        # every shown route, move one blank row to sit right after the title
        # instead of trailing at the end. Purely visual -- it never changes
        # row or column counts, and never happens when the board is full.
        if len(lines) > 1 + len(shown_routes) and lines[-1] == "":
            lines.insert(1, lines.pop())

        return lines[:rows]

    def get_formatted_display(self) -> Optional[List[str]]:
        """Return this plugin's own whole-board layout, sized to ``self.board``.

        ``self.board`` is ``None`` outside a board-scoped render (unit tests,
        legacy callers); that is treated as a Flagship, never a crash.
        """
        if not self._cache:
            result = self.fetch_data()
            if not result.available:
                return None

        data = self._cache
        if not data:
            return None

        rows, cols = self._board_dims()
        return self._build_display_lines(data.get("routes", []), rows, cols)


# Export the plugin class
Plugin = TrafficPlugin

