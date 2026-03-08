"""Virtual gate / stop crossing geometry logic.

Port of stunnel.geometry.GeoUtils (Scala / JTS) to Python / shapely.
All math is pure — no I/O. [CA][PA]

Algorithm overview
------------------
1. Project lat/lon to Web Mercator metres (x, y).
2. For each consecutive pair of pattern points, build a perpendicular
   "virtual gate" LineString at `fraction` along the segment.
3. Test whether the vehicle movement segment intersects each gate.
4. Yield the point at the *end* of each crossed segment (index + 1).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from shapely.geometry import LineString

from src.domain.models import Pattern, Point, VehicleMovement
from src.framework.constants import (
    GATE_CENTRAL_ANGLE,
    GATE_FRACTION,
    GATE_WIDTH_METERS,
    MERCATOR_RADIUS,
)

_RADIAN_TO_DEGREE = math.pi / 180.0


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def mercator_to_meters(lat: float, lon: float) -> tuple[float, float]:
    """Convert WGS-84 lat/lon to Web Mercator metres (x, y)."""
    x = lon * MERCATOR_RADIUS * _RADIAN_TO_DEGREE
    sin_lat = math.sin(lat * _RADIAN_TO_DEGREE)
    y = 0.5 * MERCATOR_RADIUS * math.log((1.0 + sin_lat) / (1.0 - sin_lat))
    return x, y


def inverse_mercator(x: float, y: float) -> tuple[float, float]:
    """Convert Web Mercator metres back to WGS-84 lat/lon."""
    lon = x / MERCATOR_RADIUS / _RADIAN_TO_DEGREE
    lat = (math.pi * 0.5 - 2.0 * math.atan(math.exp(y / -MERCATOR_RADIUS))) / _RADIAN_TO_DEGREE
    return lat, lon


# ---------------------------------------------------------------------------
# Virtual gate construction
# ---------------------------------------------------------------------------


def _rotate_unit(dx: float, dy: float, rad: float) -> tuple[float, float]:
    """Rotate vector (dx, dy) by `rad` radians, then normalise to unit length."""
    length = math.hypot(dx, dy)
    if length == 0.0:
        return 0.0, 0.0
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    rx = (dx * cos_r - dy * sin_r) / length
    ry = (dx * sin_r + dy * cos_r) / length
    return rx, ry


def virtual_gate_of(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    fraction: float = GATE_FRACTION,
    width: float = GATE_WIDTH_METERS,
    central_angle: float = GATE_CENTRAL_ANGLE,
) -> LineString:
    """Build a perpendicular gate LineString at `fraction` along segment (p1→p2).

    Returns a two-point LineString crossing the segment perpendicularly at the
    gate centre.  Intersecting this line with a movement segment detects crossing.

    Parameters
    ----------
    x1, y1, x2, y2 : segment endpoints in Mercator metres
    fraction       : where along the segment to place the gate centre (0–1)
    width          : half-width × 2, i.e. full gate span in metres
    central_angle  : π → full perpendicular; smaller → narrower cone
    """
    # Gate centre
    cx = x1 + fraction * (x2 - x1)
    cy = y1 + fraction * (y2 - y1)

    # Direction vector of segment
    dx, dy = x2 - x1, y2 - y1

    rot_rad = math.pi - central_angle / 2.0

    # Two arms of the gate (left and right)
    lx, ly = _rotate_unit(dx, dy, rot_rad)
    rx, ry = _rotate_unit(dx, dy, -rot_rad)

    half = width / 2.0
    return LineString(
        [
            (cx + lx * half, cy + ly * half),
            (cx + rx * half, cy + ry * half),
        ]
    )


# ---------------------------------------------------------------------------
# Pattern crossing detection
# ---------------------------------------------------------------------------


def points_crossed(
    movement: VehicleMovement,
    pattern: Pattern,
    fraction: float = GATE_FRACTION,
    width: float = GATE_WIDTH_METERS,
) -> list[Point]:
    """Return pattern points (stops + waypoints) passed during `movement`.

    Mirrors Pattern.pointsCrossed (Scala): the gate is built on segment
    points[i] → points[i+1], and if the movement crosses it the *end* point
    (points[i+1]) is yielded.
    """
    prev = movement.previous
    curr = movement.current

    # Project movement to Mercator
    mx1, my1 = mercator_to_meters(prev.latitude, prev.longitude)
    mx2, my2 = mercator_to_meters(curr.latitude, curr.longitude)
    movement_line = LineString([(mx1, my1), (mx2, my2)])

    # Skip trivial (zero-length) movements
    if mx1 == mx2 and my1 == my2:
        return []

    pts: Sequence[Point] = pattern.points
    crossed: list[Point] = []

    for i in range(len(pts) - 1):
        p1, p2 = pts[i], pts[i + 1]
        gx1, gy1 = mercator_to_meters(p1.latitude, p1.longitude)
        gx2, gy2 = mercator_to_meters(p2.latitude, p2.longitude)

        # Skip degenerate segments
        if gx1 == gx2 and gy1 == gy2:
            continue

        gate = virtual_gate_of(gx1, gy1, gx2, gy2, fraction=fraction, width=width)
        if gate.intersects(movement_line):
            crossed.append(p2)

    return crossed
