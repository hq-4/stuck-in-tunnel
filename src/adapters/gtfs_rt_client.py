"""GTFS-RT vehicle positions adapter.

Fetches protobuf FeedMessage from the official NJ Transit developer portal
and parses it into domain VehicleLocation objects. [CA][IV][REH]

Replaces stunnel.njtransit.api.ApiClient + KeyProvider (Scala / BusTime).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from google.transit import gtfs_realtime_pb2  # type: ignore[import]

from src.domain.models import VehicleLocation
from src.framework.constants import GTFS_RT_BEARER_HEADER, HTTP_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class GTFSRTClient:
    """Async GTFS-RT client using httpx. [RM]"""

    def __init__(
        self,
        feed_url: str,
        api_key: str,
        routes: Sequence[str],
        timeout: float = HTTP_TIMEOUT_SECONDS,
    ) -> None:
        if not feed_url:
            raise ValueError("GTFS-RT feed URL is required")
        if not api_key:
            raise ValueError("GTFS-RT API key is required")
        self._url = feed_url
        self._key = api_key
        self._routes = set(routes)
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> GTFSRTClient:
        self._client = httpx.AsyncClient(
            headers={GTFS_RT_BEARER_HEADER: f"Bearer {self._key}"},
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_vehicle_positions(self) -> list[VehicleLocation]:
        """Fetch and parse GTFS-RT VehiclePositions feed.

        Returns only vehicles on configured routes.
        Raises httpx.HTTPError on network/HTTP errors. [REH]
        """
        if self._client is None:
            raise RuntimeError("GTFSRTClient used outside async context manager")

        response = await self._client.get(self._url)
        response.raise_for_status()

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        locations: list[VehicleLocation] = []
        now = datetime.now(tz=UTC)

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue

            vp = entity.vehicle
            pos = vp.position
            trip = vp.trip
            vehicle = vp.vehicle

            route_id = trip.route_id or ""
            if self._routes and route_id not in self._routes:
                continue

            # GTFS-RT timestamp is Unix epoch seconds
            ts = datetime.fromtimestamp(vp.timestamp, tz=UTC) if vp.timestamp else now

            locations.append(
                VehicleLocation(
                    vehicle_id=vehicle.id or entity.id,
                    timestamp=ts,
                    latitude=pos.latitude,
                    longitude=pos.longitude,
                    route=route_id or None,
                    heading=int(pos.bearing) if pos.HasField("bearing") else None,
                    speed=pos.speed if pos.HasField("speed") else None,
                    trip_id=trip.trip_id or None,
                    recorded_at=now,
                )
            )

        logger.debug(
            "Parsed %d vehicle positions from GTFS-RT feed",
            len(locations),
            extra={"subsys": "gtfs_rt", "event": "parse"},
        )
        return locations
