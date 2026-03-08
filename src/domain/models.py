"""Domain models — pure data, no I/O. [CA][IV]

Corresponds to stunnel.njtransit.models (Scala).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------


class _StrictBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


class Route(_StrictBase):
    route: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    color: str = ""


# ---------------------------------------------------------------------------
# Pattern geometry
# ---------------------------------------------------------------------------


class PointType(StrEnum):
    STOP = "S"
    WAYPOINT = "W"


class Point(_StrictBase):
    seq_num: int
    latitude: float
    longitude: float
    point_type: PointType = PointType.WAYPOINT
    stop_id: str | None = None
    stop_name: str | None = None
    distance: float = 0.0

    @property
    def is_stop(self) -> bool:
        return self.point_type == PointType.STOP


class Pattern(_StrictBase):
    pattern_id: int
    route: str
    route_direction: str
    points: tuple[Point, ...]

    @property
    def stops(self) -> list[Point]:
        return [p for p in self.points if p.is_stop]


# ---------------------------------------------------------------------------
# Vehicle location (one GPS ping)
# ---------------------------------------------------------------------------


class VehicleLocation(_StrictBase):
    vehicle_id: str
    timestamp: datetime
    latitude: float
    longitude: float
    pattern_id: int | None = None
    route: str | None = None
    heading: int | None = None
    speed: float | None = None
    destination: str | None = None
    delayed: bool = False
    passenger_load: str | None = None
    trip_id: str | None = None
    scheduled_start_dt: datetime | None = None
    recorded_at: datetime | None = None

    def same_location_as(self, other: VehicleLocation) -> bool:
        return self.latitude == other.latitude and self.longitude == other.longitude


# ---------------------------------------------------------------------------
# Movement (two consecutive locations for the same vehicle/trip)
# ---------------------------------------------------------------------------


class VehicleMovement(_StrictBase):
    """Two consecutive GPS pings for the same trip. [CA]"""

    previous: VehicleLocation
    current: VehicleLocation


# ---------------------------------------------------------------------------
# Bus arrival (a crossing event at a stop)
# ---------------------------------------------------------------------------


class BusArrival(_StrictBase):
    vehicle_id: str
    route: str | None
    pattern_id: int | None
    stop_id: str | None
    stop_name: str | None
    stop_seq: int | None
    arrival_timestamp: datetime
    latitude: float | None = None
    longitude: float | None = None
    recorded_at: datetime | None = None

    @classmethod
    def from_movement_and_stop(
        cls, movement: VehicleMovement, stop: Point, recorded_at: datetime | None = None
    ) -> BusArrival:
        loc = movement.current
        return cls(
            vehicle_id=loc.vehicle_id,
            route=loc.route,
            pattern_id=loc.pattern_id,
            stop_id=stop.stop_id,
            stop_name=stop.stop_name,
            stop_seq=stop.seq_num,
            arrival_timestamp=loc.timestamp,
            latitude=loc.latitude,
            longitude=loc.longitude,
            recorded_at=recorded_at,
        )
