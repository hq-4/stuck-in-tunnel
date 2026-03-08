"""Integration tests for SQLiteRepo — uses a real temporary SQLite DB. [REH]"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from src.adapters.sqlite_repo import SQLiteRepo
from src.domain.models import BusArrival, Pattern, Point, PointType, Route, VehicleLocation

pytestmark = pytest.mark.integration

NOW = datetime.now(tz=UTC)


@pytest_asyncio.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    async with SQLiteRepo(db_path) as r:
        yield r


class TestRoutes:
    async def test_upsert_and_roundtrip(self, repo):
        routes = [Route(route="126", name="126 HOBOKEN-PATH", color="#ff0000")]
        await repo.upsert_routes(routes)

        # Verify via raw SQL
        rows = await repo._db.execute_fetchall("SELECT route, name FROM routes")
        assert len(rows) == 1
        assert rows[0][0] == "126"
        assert rows[0][1] == "126 HOBOKEN-PATH"

    async def test_upsert_is_idempotent(self, repo):
        routes = [Route(route="126", name="126 HOBOKEN-PATH")]
        await repo.upsert_routes(routes)
        await repo.upsert_routes(routes)

        rows = await repo._db.execute_fetchall("SELECT route FROM routes")
        assert len(rows) == 1


class TestPatterns:
    def _make_pattern(self) -> Pattern:
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
        )
        return Pattern(pattern_id=99, route="126", route_direction="OUTBOUND", points=pts)

    async def test_upsert_and_fetch(self, repo):
        pattern = self._make_pattern()
        await repo.upsert_patterns([pattern])

        fetched = await repo.get_patterns_for_route("126")
        assert len(fetched) == 1
        assert fetched[0].pattern_id == 99
        assert len(fetched[0].points) == 2
        assert fetched[0].points[1].is_stop


class TestVehicleLocations:
    def _make_loc(self) -> VehicleLocation:
        return VehicleLocation(
            vehicle_id="V1",
            timestamp=NOW,
            latitude=40.73,
            longitude=-74.03,
            route="126",
            pattern_id=1,
            trip_id="T1",
            recorded_at=NOW,
        )

    async def test_bulk_insert(self, repo):
        locs = [self._make_loc(), self._make_loc()]
        await repo.bulk_insert_locations(locs)

        rows = await repo._db.execute_fetchall("SELECT vehicle_id FROM vehicle_locations")
        assert len(rows) == 2

    async def test_fetch_by_date(self, repo):
        loc = self._make_loc()
        await repo.bulk_insert_locations([loc])

        date_str = NOW.strftime("%Y-%m-%d")
        fetched = await repo.fetch_locations_for_date(date_str)
        assert len(fetched) == 1
        assert fetched[0]["vehicle_id"] == "V1"


class TestBusArrivals:
    def _make_arrival(self) -> BusArrival:
        return BusArrival(
            vehicle_id="V1",
            route="126",
            pattern_id=1,
            stop_id="S1",
            stop_name="HOBOKEN TERMINAL",
            stop_seq=3,
            arrival_timestamp=NOW,
            latitude=40.73,
            longitude=-74.03,
            recorded_at=NOW,
        )

    async def test_bulk_insert(self, repo):
        arrivals = [self._make_arrival()]
        await repo.bulk_insert_arrivals(arrivals)

        rows = await repo._db.execute_fetchall("SELECT stop_name FROM bus_arrivals")
        assert len(rows) == 1
        assert rows[0][0] == "HOBOKEN TERMINAL"

    async def test_fetch_by_date(self, repo):
        arrival = self._make_arrival()
        await repo.bulk_insert_arrivals([arrival])

        date_str = NOW.strftime("%Y-%m-%d")
        fetched = await repo.fetch_arrivals_for_date(date_str)
        assert len(fetched) == 1
        assert fetched[0]["stop_id"] == "S1"


class TestPruning:
    async def test_prune_removes_old_rows(self, repo):
        from datetime import timedelta

        old_time = (NOW - timedelta(days=10)).isoformat()

        await repo._db.execute(
            """INSERT INTO vehicle_locations
               (vehicle_id, timestamp, latitude, longitude, recorded_at)
               VALUES ('OLD', ?, 40.0, -74.0, ?)""",
            (old_time, old_time),
        )
        await repo._db.commit()

        await repo.prune_old_rows(retention_days=7)

        rows = await repo._db.execute_fetchall(
            "SELECT vehicle_id FROM vehicle_locations WHERE vehicle_id='OLD'"
        )
        assert len(rows) == 0
