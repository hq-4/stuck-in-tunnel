"""Unit tests for domain models — no I/O. [CA][IV]"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.domain.models import (
    BusArrival,
    Pattern,
    Point,
    PointType,
    Route,
    VehicleLocation,
    VehicleMovement,
)

pytestmark = pytest.mark.unit

NOW = datetime.now(tz=UTC)


class TestRoute:
    def test_valid_route(self):
        r = Route(route="126", name="126 HOBOKEN-PATH", color="#ff3366")
        assert r.route == "126"
        assert r.name == "126 HOBOKEN-PATH"

    def test_empty_route_raises(self):
        with pytest.raises(PydanticValidationError):
            Route(route="", name="name")

    def test_extra_fields_ignored(self):
        r = Route(route="1", name="One", color="", unknown_field="ignored")
        assert not hasattr(r, "unknown_field")


class TestPoint:
    def test_stop_point(self):
        p = Point(
            seq_num=1,
            latitude=40.73,
            longitude=-74.03,
            point_type=PointType.STOP,
            stop_id="20496",
            stop_name="HOBOKEN TERMINAL",
        )
        assert p.is_stop

    def test_waypoint(self):
        p = Point(seq_num=2, latitude=40.73, longitude=-74.03)
        assert not p.is_stop

    def test_frozen(self):
        p = Point(seq_num=1, latitude=40.73, longitude=-74.03)
        with pytest.raises(PydanticValidationError):  # frozen Pydantic model raises ValidationError
            p.seq_num = 99  # type: ignore[misc]


class TestVehicleLocation:
    def test_same_location(self):
        a = VehicleLocation(vehicle_id="1", timestamp=NOW, latitude=40.0, longitude=-74.0)
        b = VehicleLocation(vehicle_id="1", timestamp=NOW, latitude=40.0, longitude=-74.0)
        assert a.same_location_as(b)

    def test_different_location(self):
        a = VehicleLocation(vehicle_id="1", timestamp=NOW, latitude=40.0, longitude=-74.0)
        b = VehicleLocation(vehicle_id="1", timestamp=NOW, latitude=40.1, longitude=-74.0)
        assert not a.same_location_as(b)

    def test_extra_fields_ignored(self):
        loc = VehicleLocation(
            vehicle_id="1",
            timestamp=NOW,
            latitude=40.0,
            longitude=-74.0,
            unknown="extra",
        )
        assert not hasattr(loc, "unknown")


class TestBusArrival:
    def _stop(self) -> Point:
        return Point(
            seq_num=3,
            latitude=40.73,
            longitude=-74.03,
            point_type=PointType.STOP,
            stop_id="S1",
            stop_name="MAIN ST",
        )

    def _movement(self) -> VehicleMovement:
        loc = VehicleLocation(
            vehicle_id="V1",
            timestamp=NOW,
            latitude=40.73,
            longitude=-74.03,
            route="126",
            pattern_id=42,
            trip_id="T99",
        )
        return VehicleMovement(previous=loc, current=loc)

    def test_from_movement_and_stop(self):
        movement = self._movement()
        stop = self._stop()
        arrival = BusArrival.from_movement_and_stop(movement, stop, recorded_at=NOW)
        assert arrival.vehicle_id == "V1"
        assert arrival.stop_id == "S1"
        assert arrival.stop_name == "MAIN ST"
        assert arrival.pattern_id == 42
        assert arrival.route == "126"
        assert arrival.recorded_at == NOW


class TestPattern:
    def test_stops_only(self):
        pts = (
            Point(seq_num=1, latitude=40.73, longitude=-74.03),
            Point(
                seq_num=2,
                latitude=40.74,
                longitude=-74.03,
                point_type=PointType.STOP,
                stop_id="S1",
                stop_name="Stop A",
            ),
            Point(seq_num=3, latitude=40.75, longitude=-74.03),
        )
        pattern = Pattern(pattern_id=1, route="126", route_direction="OUTBOUND", points=pts)
        assert len(pattern.stops) == 1
        assert pattern.stops[0].stop_id == "S1"
