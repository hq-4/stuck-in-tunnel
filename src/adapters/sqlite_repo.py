"""SQLite hot store adapter.

Manages schema migrations, bulk inserts, and rolling-window pruning.
Uses aiosqlite for non-blocking async I/O. [CA][RM][REH]

Corresponds to the Arrow IPC persistence in Scala, replaced with SQLite.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import aiosqlite

from src.domain.models import BusArrival, Pattern, Route, VehicleLocation
from src.framework.constants import HOT_RETENTION_DAYS

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS routes (
    route       TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    color       TEXT,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patterns (
    pattern_id      INTEGER PRIMARY KEY,
    route           TEXT NOT NULL,
    route_direction TEXT NOT NULL,
    points_json     TEXT NOT NULL,
    fetched_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicle_locations (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id          TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    latitude            REAL NOT NULL,
    longitude           REAL NOT NULL,
    pattern_id          INTEGER,
    route               TEXT,
    heading             INTEGER,
    speed               REAL,
    destination         TEXT,
    delayed             INTEGER,
    passenger_load      TEXT,
    trip_id             TEXT,
    scheduled_start_dt  TEXT,
    recorded_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bus_arrivals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id        TEXT NOT NULL,
    route             TEXT,
    pattern_id        INTEGER,
    stop_id           TEXT,
    stop_name         TEXT,
    stop_seq          INTEGER,
    arrival_timestamp TEXT NOT NULL,
    latitude          REAL,
    longitude         REAL,
    recorded_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vl_vehicle_time ON vehicle_locations(vehicle_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_vl_route        ON vehicle_locations(route);
CREATE INDEX IF NOT EXISTS idx_ba_stop         ON bus_arrivals(stop_id, arrival_timestamp);
CREATE INDEX IF NOT EXISTS idx_ba_vehicle      ON bus_arrivals(vehicle_id, arrival_timestamp);
"""


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat()
    return dt.astimezone(UTC).isoformat()


class SQLiteRepo:
    """Async SQLite repository for hot-store data. [RM]"""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> SQLiteRepo:
        import os

        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(_DDL)
        await self._db.commit()
        logger.info(
            "SQLite repo ready at %s",
            self._path,
            extra={"subsys": "sqlite", "event": "open"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    async def upsert_routes(self, routes: Sequence[Route]) -> None:
        assert self._db
        now = datetime.now(tz=UTC).isoformat()
        rows = [(r.route, r.name, r.color, now) for r in routes]
        await self._db.executemany(
            "INSERT OR REPLACE INTO routes(route, name, color, fetched_at) VALUES (?,?,?,?)",
            rows,
        )
        await self._db.commit()
        logger.debug("Upserted %d routes", len(rows), extra={"subsys": "sqlite"})

    # ------------------------------------------------------------------
    # Patterns
    # ------------------------------------------------------------------

    async def upsert_patterns(self, patterns: Sequence[Pattern]) -> None:
        assert self._db
        now = datetime.now(tz=UTC).isoformat()
        rows = [
            (
                p.pattern_id,
                p.route,
                p.route_direction,
                json.dumps([pt.model_dump() for pt in p.points]),
                now,
            )
            for p in patterns
        ]
        await self._db.executemany(
            """INSERT OR REPLACE INTO patterns
               (pattern_id, route, route_direction, points_json, fetched_at)
               VALUES (?,?,?,?,?)""",
            rows,
        )
        await self._db.commit()
        logger.debug("Upserted %d patterns", len(rows), extra={"subsys": "sqlite"})

    async def get_patterns_for_route(self, route: str) -> list[Pattern]:
        """Load patterns for a route from SQLite (offline replay). [RM]"""
        assert self._db
        from src.domain.models import Point as DomainPoint

        rows = await self._db.execute_fetchall(
            "SELECT pattern_id, route, route_direction, points_json FROM patterns WHERE route=?",
            (route,),
        )
        result: list[Pattern] = []
        for row in rows:
            pid, rt, direction, pts_json = row
            pts = [DomainPoint(**d) for d in json.loads(pts_json)]
            result.append(
                Pattern(
                    pattern_id=pid,
                    route=rt,
                    route_direction=direction,
                    points=tuple(pts),
                )
            )
        return result

    # ------------------------------------------------------------------
    # Vehicle locations
    # ------------------------------------------------------------------

    async def bulk_insert_locations(self, locations: Sequence[VehicleLocation]) -> None:
        assert self._db
        rows = [
            (
                loc.vehicle_id,
                _iso(loc.timestamp),
                loc.latitude,
                loc.longitude,
                loc.pattern_id,
                loc.route,
                loc.heading,
                loc.speed,
                loc.destination,
                int(loc.delayed),
                loc.passenger_load,
                loc.trip_id,
                _iso(loc.scheduled_start_dt),
                _iso(loc.recorded_at) or datetime.now(tz=UTC).isoformat(),
            )
            for loc in locations
        ]
        await self._db.executemany(
            """INSERT INTO vehicle_locations
               (vehicle_id, timestamp, latitude, longitude, pattern_id, route,
                heading, speed, destination, delayed, passenger_load, trip_id,
                scheduled_start_dt, recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        await self._db.commit()
        logger.debug("Inserted %d vehicle locations", len(rows), extra={"subsys": "sqlite"})

    # ------------------------------------------------------------------
    # Bus arrivals
    # ------------------------------------------------------------------

    async def bulk_insert_arrivals(self, arrivals: Sequence[BusArrival]) -> None:
        assert self._db
        rows = [
            (
                a.vehicle_id,
                a.route,
                a.pattern_id,
                a.stop_id,
                a.stop_name,
                a.stop_seq,
                _iso(a.arrival_timestamp),
                a.latitude,
                a.longitude,
                _iso(a.recorded_at) or datetime.now(tz=UTC).isoformat(),
            )
            for a in arrivals
        ]
        await self._db.executemany(
            """INSERT INTO bus_arrivals
               (vehicle_id, route, pattern_id, stop_id, stop_name, stop_seq,
                arrival_timestamp, latitude, longitude, recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        await self._db.commit()
        logger.debug("Inserted %d bus arrivals", len(rows), extra={"subsys": "sqlite"})

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    async def prune_old_rows(self, retention_days: int = HOT_RETENTION_DAYS) -> None:
        """Delete rows older than `retention_days` from both hot tables. [RM]"""
        assert self._db
        cutoff = (datetime.now(tz=UTC) - timedelta(days=retention_days)).isoformat()

        await self._db.execute("DELETE FROM vehicle_locations WHERE recorded_at < ?", (cutoff,))
        await self._db.execute("DELETE FROM bus_arrivals WHERE recorded_at < ?", (cutoff,))
        await self._db.commit()
        logger.info(
            "Pruned rows older than %s (retention=%d days)",
            cutoff,
            retention_days,
            extra={"subsys": "sqlite", "event": "prune"},
        )

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    async def fetch_locations_for_date(self, date_str: str) -> list[dict]:
        """Return all vehicle_locations rows for a given date (YYYY-MM-DD). [RM]"""
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT * FROM vehicle_locations WHERE recorded_at LIKE ? ORDER BY id",
            (f"{date_str}%",),
        )
        cols = [
            "id",
            "vehicle_id",
            "timestamp",
            "latitude",
            "longitude",
            "pattern_id",
            "route",
            "heading",
            "speed",
            "destination",
            "delayed",
            "passenger_load",
            "trip_id",
            "scheduled_start_dt",
            "recorded_at",
        ]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    async def fetch_arrivals_for_date(self, date_str: str) -> list[dict]:
        """Return all bus_arrivals rows for a given date (YYYY-MM-DD). [RM]"""
        assert self._db
        rows = await self._db.execute_fetchall(
            "SELECT * FROM bus_arrivals WHERE recorded_at LIKE ? ORDER BY id",
            (f"{date_str}%",),
        )
        cols = [
            "id",
            "vehicle_id",
            "route",
            "pattern_id",
            "stop_id",
            "stop_name",
            "stop_seq",
            "arrival_timestamp",
            "latitude",
            "longitude",
            "recorded_at",
        ]
        return [dict(zip(cols, row, strict=True)) for row in rows]
