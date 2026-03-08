"""Unit tests for geometry — no I/O, no external dependencies. [CA]"""

import math
from datetime import UTC

import pytest
from shapely.geometry import LineString

from src.domain.geometry import (
    inverse_mercator,
    mercator_to_meters,
    points_crossed,
    virtual_gate_of,
)
from src.domain.models import Pattern, Point, PointType, VehicleLocation, VehicleMovement

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Projection round-trip
# ---------------------------------------------------------------------------


class TestMercatorProjection:
    def test_hoboken_terminal_roundtrip(self):
        lat, lon = 40.7356, -74.0291
        x, y = mercator_to_meters(lat, lon)
        lat2, lon2 = inverse_mercator(x, y)
        assert abs(lat2 - lat) < 1e-9
        assert abs(lon2 - lon) < 1e-9

    def test_equator_x_is_zero(self):
        x, _ = mercator_to_meters(0.0, 0.0)
        assert x == pytest.approx(0.0, abs=1e-6)

    def test_x_proportional_to_longitude(self):
        x1, _ = mercator_to_meters(40.0, -74.0)
        x2, _ = mercator_to_meters(40.0, -73.0)
        # 1 degree of longitude at this latitude
        assert x2 > x1
        assert abs((x2 - x1) - 6378137.0 * math.pi / 180.0) < 1.0


# ---------------------------------------------------------------------------
# Virtual gate construction
# ---------------------------------------------------------------------------


class TestVirtualGate:
    def test_gate_is_linestring(self):
        gate = virtual_gate_of(0.0, 0.0, 100.0, 0.0)
        assert isinstance(gate, LineString)

    def test_gate_has_two_points(self):
        gate = virtual_gate_of(0.0, 0.0, 100.0, 0.0)
        coords = list(gate.coords)
        assert len(coords) == 2

    def test_horizontal_segment_gives_vertical_gate(self):
        # Segment runs along x-axis → gate should be roughly vertical
        gate = virtual_gate_of(0.0, 0.0, 100.0, 0.0, fraction=0.5, width=30.0)
        coords = list(gate.coords)
        x0, y0 = coords[0]
        x1, y1 = coords[1]
        # Both endpoints should share approximately the same x (gate is perpendicular)
        assert abs(x0 - x1) < 1.0
        assert abs(y1 - y0) == pytest.approx(30.0, rel=0.05)

    def test_movement_crosses_gate(self):
        # Segment along x-axis, gate at midpoint
        gate = virtual_gate_of(0.0, 0.0, 100.0, 0.0, fraction=0.5, width=30.0)
        # Movement cuts across the gate from below to above y-axis
        movement = LineString([(50.0, -20.0), (50.0, 20.0)])
        assert gate.intersects(movement)

    def test_parallel_movement_misses_gate(self):
        # Gate perpendicular to x-axis; movement parallel to x-axis → no intersection
        gate = virtual_gate_of(0.0, 0.0, 100.0, 0.0, fraction=0.5, width=30.0)
        movement = LineString([(0.0, 50.0), (100.0, 50.0)])  # far from gate
        assert not gate.intersects(movement)


# ---------------------------------------------------------------------------
# points_crossed
# ---------------------------------------------------------------------------


def _make_point(seq: int, lat: float, lon: float, is_stop: bool = False) -> Point:
    return Point(
        seq_num=seq,
        latitude=lat,
        longitude=lon,
        point_type=PointType.STOP if is_stop else PointType.WAYPOINT,
        stop_id=f"S{seq}" if is_stop else None,
        stop_name=f"Stop {seq}" if is_stop else None,
    )


def _make_loc(lat: float, lon: float) -> VehicleLocation:
    from datetime import datetime

    return VehicleLocation(
        vehicle_id="V1",
        timestamp=datetime.now(tz=UTC),
        latitude=lat,
        longitude=lon,
        route="126",
        pattern_id=1,
        trip_id="T1",
    )


class TestPointsCrossed:
    def _make_linear_pattern(self) -> Pattern:
        """Pattern along a north-south line in Hoboken area."""
        return Pattern(
            pattern_id=1,
            route="126",
            route_direction="OUTBOUND",
            points=(
                _make_point(1, 40.730, -74.029),
                _make_point(2, 40.735, -74.029, is_stop=True),  # Stop
                _make_point(3, 40.740, -74.029),
                _make_point(4, 40.745, -74.029, is_stop=True),  # Stop
            ),
        )

    def test_no_movement_yields_nothing(self):
        pattern = self._make_linear_pattern()
        prev = _make_loc(40.730, -74.029)
        curr = _make_loc(40.730, -74.029)  # same position
        movement = VehicleMovement(previous=prev, current=curr)
        assert points_crossed(movement, pattern) == []

    def test_crossing_first_stop(self):
        pattern = self._make_linear_pattern()
        # Vehicle moves from just before stop 2 to just past it
        prev = _make_loc(40.7345, -74.029)
        curr = _make_loc(40.7355, -74.029)
        movement = VehicleMovement(previous=prev, current=curr)
        crossed = points_crossed(movement, pattern)
        # Should detect crossing of at least the stop point
        stop_names = [p.stop_name for p in crossed if p.is_stop]
        assert len(stop_names) >= 1

    def test_short_segment_far_from_pattern_yields_nothing(self):
        pattern = self._make_linear_pattern()
        # Movement far west from pattern
        prev = _make_loc(40.730, -74.100)
        curr = _make_loc(40.731, -74.100)
        movement = VehicleMovement(previous=prev, current=curr)
        assert points_crossed(movement, pattern) == []
