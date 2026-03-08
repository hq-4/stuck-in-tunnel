"""GTFS static feed loader.

Downloads the GTFS static zip and parses stops.txt, shapes.txt, routes.txt,
trips.txt to build Pattern + Route objects. [CA][IV][REH]

Replaces BusTime /getpatterns API calls (Scala).
"""

from __future__ import annotations

import contextlib
import csv
import io
import logging
import zipfile
from collections import defaultdict

import httpx

from src.domain.models import Pattern, Point, PointType, Route
from src.framework.constants import HTTP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class GTFSStaticLoader:
    """Downloads and parses the GTFS static bundle. [RM]"""

    def __init__(self, static_url: str, api_key: str = "") -> None:
        if not static_url:
            raise ValueError("GTFS static URL is required")
        self._url = static_url
        self._api_key = api_key

    async def load(self) -> tuple[list[Route], list[Pattern]]:
        """Download GTFS zip and return (routes, patterns). [REH]"""
        logger.info(
            "Downloading GTFS static bundle from %s",
            self._url,
            extra={"subsys": "gtfs_static", "event": "download_start"},
        )
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        async with httpx.AsyncClient(
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS * 6,  # larger payload
        ) as client:
            response = await client.get(self._url)
            response.raise_for_status()

        raw_bytes = response.content
        logger.info(
            "Downloaded GTFS static bundle (%.1f KB)",
            len(raw_bytes) / 1024,
            extra={"subsys": "gtfs_static", "event": "download_complete"},
        )

        routes, patterns = _parse_gtfs_zip(raw_bytes)
        logger.info(
            "Parsed %d routes and %d patterns from GTFS static",
            len(routes),
            len(patterns),
            extra={"subsys": "gtfs_static", "event": "parse_complete"},
        )
        return routes, patterns


def _read_csv(zf: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    """Read a CSV file from the GTFS zip, return list of row dicts."""
    try:
        with zf.open(filename) as f:
            content = f.read().decode("utf-8-sig")  # handle BOM
        reader = csv.DictReader(io.StringIO(content))
        return list(reader)
    except KeyError:
        logger.warning(
            "GTFS zip missing file: %s",
            filename,
            extra={"subsys": "gtfs_static", "event": "missing_file"},
        )
        return []


def _parse_gtfs_zip(raw: bytes) -> tuple[list[Route], list[Pattern]]:
    """Parse GTFS zip bytes into Route and Pattern domain objects."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        routes_rows = _read_csv(zf, "routes.txt")
        trips_rows = _read_csv(zf, "trips.txt")
        stops_rows = _read_csv(zf, "stops.txt")
        stop_times_rows = _read_csv(zf, "stop_times.txt")
        shapes_rows = _read_csv(zf, "shapes.txt")

    # --- Routes ---
    routes: list[Route] = []
    for row in routes_rows:
        try:
            routes.append(
                Route(
                    route=row["route_id"],
                    name=row.get("route_long_name") or row.get("route_short_name", ""),
                    color="#" + row["route_color"] if row.get("route_color") else "",
                )
            )
        except Exception:
            logger.debug("Skipping malformed routes row: %s", row)

    # --- trip_id → (route_id, direction_id, shape_id) ---
    trip_info: dict[str, tuple[str, str, str]] = {}
    for row in trips_rows:
        trip_info[row["trip_id"]] = (
            row.get("route_id", ""),
            row.get("direction_id", "0"),
            row.get("shape_id", ""),
        )

    # --- stop_id → stop info ---
    stop_info: dict[str, tuple[str, float, float]] = {}
    for row in stops_rows:
        with contextlib.suppress(KeyError, ValueError):
            stop_info[row["stop_id"]] = (
                row.get("stop_name", ""),
                float(row["stop_lat"]),
                float(row["stop_lon"]),
            )

    # --- shape_id → ordered list of (lat, lon) waypoints ---
    shape_points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for row in shapes_rows:
        with contextlib.suppress(KeyError, ValueError):
            shape_points[row["shape_id"]].append(
                (
                    int(row["shape_pt_sequence"]),
                    float(row["shape_pt_lat"]),
                    float(row["shape_pt_lon"]),
                )
            )
    # Sort each shape by sequence
    for pts in shape_points.values():
        pts.sort(key=lambda t: t[0])

    # --- stop_times: trip_id → ordered list of (stop_sequence, stop_id) ---
    trip_stops: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in stop_times_rows:
        with contextlib.suppress(KeyError, ValueError):
            trip_stops[row["trip_id"]].append(
                (
                    int(row["stop_sequence"]),
                    row["stop_id"],
                )
            )
    for stops in trip_stops.values():
        stops.sort(key=lambda t: t[0])

    # --- Build patterns: one per (route_id, direction_id, shape_id) ---
    # Deduplicate: use (route_id, direction_id, shape_id) as pattern key
    seen: dict[tuple[str, str, str], int] = {}  # key → pattern_id
    pattern_map: dict[int, Pattern] = {}
    pattern_id_counter = 1

    for trip_id, (route_id, direction_id, shape_id) in trip_info.items():
        key = (route_id, direction_id, shape_id)
        if key in seen:
            continue  # already built this pattern

        # Build points: merge shape waypoints + stops from stop_times
        # Map stop_sequence → stop_id for this trip
        stops_for_trip = trip_stops.get(trip_id, [])

        shape_pts = shape_points.get(shape_id, [])
        if not shape_pts:
            continue

        points: list[Point] = []
        for seq_idx, (_seq, lat, lon) in enumerate(shape_pts):
            # Check if any stop in this trip is at approximately this waypoint
            # (GTFS doesn't always perfectly align shape sequences with stops)
            # Simple heuristic: no per-waypoint stop mapping from shape alone.
            # We'll add stops as separate points after shape waypoints.
            points.append(
                Point(
                    seq_num=seq_idx + 1,
                    latitude=lat,
                    longitude=lon,
                    point_type=PointType.WAYPOINT,
                )
            )

        # Append stops as STOP points after the shape (crude but functional;
        # better approach would interpolate stop positions along the shape)
        for stop_seq, stop_id in stops_for_trip:
            if stop_id in stop_info:
                sname, slat, slon = stop_info[stop_id]
                points.append(
                    Point(
                        seq_num=len(shape_pts) + stop_seq,
                        latitude=slat,
                        longitude=slon,
                        point_type=PointType.STOP,
                        stop_id=stop_id,
                        stop_name=sname,
                    )
                )

        # Sort all points by seq_num
        points.sort(key=lambda p: p.seq_num)

        pid = pattern_id_counter
        pattern_id_counter += 1
        seen[key] = pid

        route_direction = "INBOUND" if direction_id == "1" else "OUTBOUND"

        pattern_map[pid] = Pattern(
            pattern_id=pid,
            route=route_id,
            route_direction=route_direction,
            points=tuple(points),
        )

    return routes, list(pattern_map.values())
