"""Core tracking use-cases.

Port of stunnel.Ops.trackMovement + trackBusStopArrivals (Scala / FS2).
Runs as plain asyncio coroutines operating on asyncio.Queue. [CA][PA]
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime

from src.domain.geometry import points_crossed
from src.domain.models import BusArrival, Pattern, VehicleLocation, VehicleMovement
from src.framework.constants import MAX_CACHED_PATTERNS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LRU dict for trip-keyed previous locations (mirrors Ops.LRUCache in Scala)
# ---------------------------------------------------------------------------


class _LRUDict(OrderedDict[str, VehicleLocation]):
    """OrderedDict with bounded capacity and LRU eviction."""

    def __init__(self, maxsize: int) -> None:
        super().__init__()
        self._maxsize = maxsize

    def __setitem__(self, key: str, value: VehicleLocation) -> None:  # type: ignore[override]
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self._maxsize:
            self.popitem(last=False)


# ---------------------------------------------------------------------------
# trackMovement
# ---------------------------------------------------------------------------


def track_movement(
    current: VehicleLocation,
    previous_locations: _LRUDict,
) -> VehicleMovement | None:
    """Given the current vehicle location and the LRU store of previous ones,
    emit a VehicleMovement if we have a prior position for the same trip.

    Updates `previous_locations` in-place.

    Returns None on first observation of a trip (no previous location).
    """
    key = current.trip_id or current.vehicle_id  # fall back to vehicle if no trip
    prev = previous_locations.get(key)
    previous_locations[key] = current

    if prev is None:
        return None

    # Skip duplicates (no movement)
    if current.same_location_as(prev):
        return None

    return VehicleMovement(previous=prev, current=current)


# ---------------------------------------------------------------------------
# trackBusStopArrivals
# ---------------------------------------------------------------------------


async def track_bus_stop_arrivals(
    movement: VehicleMovement,
    get_pattern: Callable[[str, int], asyncio.coroutines.Coroutine[None, None, Pattern | None]],
) -> list[BusArrival]:
    """Given a vehicle movement, look up its pattern and find which stops were
    crossed.  Returns BusArrival objects for each stop crossing.

    Port of Ops.trackBusStopArrivals (Scala).
    """
    loc = movement.current
    if loc.route is None or loc.pattern_id is None:
        return []

    pattern = await get_pattern(loc.route, loc.pattern_id)
    if pattern is None:
        return []

    crossed = points_crossed(movement, pattern)
    now = datetime.now(tz=UTC)
    arrivals: list[BusArrival] = []
    for stop in crossed:
        if stop.is_stop:
            arrivals.append(BusArrival.from_movement_and_stop(movement, stop, recorded_at=now))

    if arrivals:
        logger.debug(
            "[%s] Vehicle %s (trip %s) passed %d stop(s): %s",
            loc.route,
            loc.vehicle_id,
            loc.trip_id,
            len(arrivals),
            [a.stop_name for a in arrivals],
            extra={"subsys": "tracking", "event": "stop_crossing"},
        )

    return arrivals


# ---------------------------------------------------------------------------
# Producer pipeline (single coroutine processing all routes)
# ---------------------------------------------------------------------------


async def producer_pipeline(
    fetch_vehicles: Callable[[], asyncio.coroutines.Coroutine[None, None, list[VehicleLocation]]],
    get_pattern: Callable[[str, int], asyncio.coroutines.Coroutine[None, None, Pattern | None]],
    location_queue: asyncio.Queue[VehicleLocation],
    arrival_queue: asyncio.Queue[BusArrival],
    poll_interval: int,
    max_trips: int = MAX_CACHED_PATTERNS,
) -> None:
    """Continuously poll vehicle positions, compute movements and arrivals,
    and push results onto the provided queues.

    Intended to run as a long-lived asyncio task. [PA][REH]
    """
    previous_locations: _LRUDict = _LRUDict(maxsize=max_trips)

    while True:
        try:
            vehicles = await fetch_vehicles()
            logger.info(
                "Fetched %d vehicle locations",
                len(vehicles),
                extra={"subsys": "producer", "event": "poll"},
            )

            for loc in vehicles:
                # Stamp recorded_at if missing
                if loc.recorded_at is None:
                    loc = loc.model_copy(update={"recorded_at": datetime.now(tz=UTC)})
                await location_queue.put(loc)

                movement = track_movement(loc, previous_locations)
                if movement is None:
                    continue

                arrivals = await track_bus_stop_arrivals(movement, get_pattern)
                for arrival in arrivals:
                    await arrival_queue.put(arrival)

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Producer pipeline error (will retry next poll)",
                extra={"subsys": "producer", "event": "error"},
            )

        await asyncio.sleep(poll_interval)
