"""Integration-style tests for GTFSRTClient — uses httpx mocking. [REH][IV]"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.adapters.gtfs_rt_client import GTFSRTClient

pytestmark = pytest.mark.integration


def _build_feed_bytes(
    route_id: str = "126",
    vehicle_id: str = "V1",
    lat: float = 40.73,
    lon: float = -74.03,
    timestamp: int = 1700000000,
) -> bytes:
    """Build a minimal GTFS-RT FeedMessage protobuf."""
    from google.transit import gtfs_realtime_pb2

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp

    entity = feed.entity.add()
    entity.id = "e1"

    vp = entity.vehicle
    vp.timestamp = timestamp
    vp.trip.route_id = route_id
    vp.trip.trip_id = "TRIP1"
    vp.vehicle.id = vehicle_id
    vp.position.latitude = lat
    vp.position.longitude = lon
    vp.position.bearing = 180.0
    vp.position.speed = 11.0

    return feed.SerializeToString()


class TestGTFSRTClientParsing:
    async def test_parse_vehicle_positions(self):
        """Verify protobuf parsing yields correct VehicleLocation objects."""
        feed_bytes = _build_feed_bytes(route_id="126", vehicle_id="BUS1")

        mock_response = MagicMock()
        mock_response.content = feed_bytes
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        client = GTFSRTClient(
            feed_url="http://example.com/feed",
            api_key="test-key",
            routes=["126"],
        )
        client._client = mock_client

        locations = await client.get_vehicle_positions()

        assert len(locations) == 1
        loc = locations[0]
        assert loc.vehicle_id == "BUS1"
        assert abs(loc.latitude - 40.73) < 0.001
        assert abs(loc.longitude - (-74.03)) < 0.001
        assert loc.route == "126"
        assert loc.trip_id == "TRIP1"
        assert loc.timestamp.tzinfo is not None

    async def test_route_filter(self):
        """Only vehicles on configured routes should be returned."""
        feed_bytes = _build_feed_bytes(route_id="999")  # not in configured routes

        mock_response = MagicMock()
        mock_response.content = feed_bytes
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        client = GTFSRTClient(
            feed_url="http://example.com/feed",
            api_key="test-key",
            routes=["126", "23"],  # route "999" not included
        )
        client._client = mock_client

        locations = await client.get_vehicle_positions()
        assert len(locations) == 0

    async def test_raises_without_context_manager(self):
        client = GTFSRTClient(
            feed_url="http://example.com/feed",
            api_key="key",
            routes=["126"],
        )
        with pytest.raises(RuntimeError, match="context manager"):
            await client.get_vehicle_positions()

    def test_raises_on_missing_url(self):
        with pytest.raises(ValueError, match="URL"):
            GTFSRTClient(feed_url="", api_key="key", routes=["126"])

    def test_raises_on_missing_key(self):
        with pytest.raises(ValueError, match="key"):
            GTFSRTClient(feed_url="http://example.com", api_key="", routes=["126"])
