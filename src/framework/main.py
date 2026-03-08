"""Application entrypoint.

Wires together the async pipeline:
  - producer_task: polls GTFS-RT, computes movements + arrivals
  - gtfs_static_refresh_task: downloads + parses GTFS static daily
  - consumer_task: drains queues, bulk-inserts to SQLite
  - nightly_export_task: Parquet export + pruning at STUNNEL_EXPORT_HOUR UTC

Run: uv run python src/framework/main.py  [CA][REH][PA][RM]
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure repo root on path when running as script
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.adapters.gtfs_rt_client import GTFSRTClient
from src.adapters.gtfs_static_loader import GTFSStaticLoader
from src.adapters.parquet_exporter import ParquetExporter
from src.adapters.sqlite_repo import SQLiteRepo
from src.domain.models import BusArrival, Pattern, VehicleLocation
from src.framework.config import AppConfig
from src.framework.constants import (
    CONSUMER_BATCH_SIZE,
    CONSUMER_FLUSH_INTERVAL_SECONDS,
    GTFS_STATIC_REFRESH_INTERVAL_SECONDS,
)
from src.usecases.pattern_cache import PatternCache
from src.usecases.tracking import producer_pipeline
from utils.logging_setup import setup_logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Consumer task (drains queue → SQLite)
# ---------------------------------------------------------------------------


async def _consumer_locations(
    queue: asyncio.Queue[VehicleLocation],
    repo: SQLiteRepo,
) -> None:
    """Drain vehicle_location queue in batches. [RM][PA]"""
    batch: list[VehicleLocation] = []
    while True:
        try:
            loc = await asyncio.wait_for(queue.get(), timeout=CONSUMER_FLUSH_INTERVAL_SECONDS)
            batch.append(loc)
            if len(batch) >= CONSUMER_BATCH_SIZE:
                await repo.bulk_insert_locations(batch)
                batch.clear()
        except TimeoutError:
            if batch:
                await repo.bulk_insert_locations(batch)
                batch.clear()
        except asyncio.CancelledError:
            if batch:
                await repo.bulk_insert_locations(batch)
            raise


async def _consumer_arrivals(
    queue: asyncio.Queue[BusArrival],
    repo: SQLiteRepo,
) -> None:
    """Drain bus_arrival queue in batches. [RM][PA]"""
    batch: list[BusArrival] = []
    while True:
        try:
            arrival = await asyncio.wait_for(queue.get(), timeout=CONSUMER_FLUSH_INTERVAL_SECONDS)
            batch.append(arrival)
            if len(batch) >= CONSUMER_BATCH_SIZE:
                await repo.bulk_insert_arrivals(batch)
                batch.clear()
        except TimeoutError:
            if batch:
                await repo.bulk_insert_arrivals(batch)
                batch.clear()
        except asyncio.CancelledError:
            if batch:
                await repo.bulk_insert_arrivals(batch)
            raise


# ---------------------------------------------------------------------------
# GTFS static refresh task
# ---------------------------------------------------------------------------


async def _gtfs_static_refresh_task(
    loader: GTFSStaticLoader,
    repo: SQLiteRepo,
    pattern_cache: PatternCache,
    route_pattern_map: dict[str, list[Pattern]],
) -> None:
    """Download + parse GTFS static on startup and every 24 h. [RM]"""
    while True:
        try:
            routes, patterns = await loader.load()
            await repo.upsert_routes(routes)
            await repo.upsert_patterns(patterns)

            # Rebuild in-memory pattern lookup: route → list[Pattern]
            route_pattern_map.clear()
            for p in patterns:
                route_pattern_map.setdefault(p.route, []).append(p)

            pattern_cache.clear()
            logger.info(
                "GTFS static refresh complete — %d routes, %d patterns",
                len(routes),
                len(patterns),
                extra={"subsys": "gtfs_static", "event": "refresh_complete"},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "GTFS static refresh failed (will retry next cycle)",
                extra={"subsys": "gtfs_static", "event": "refresh_error"},
            )
        await asyncio.sleep(GTFS_STATIC_REFRESH_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Nightly export task
# ---------------------------------------------------------------------------


async def _nightly_export_task(
    exporter: ParquetExporter,
    repo: SQLiteRepo,
    export_hour: int,
    retention_days: int,
) -> None:
    """Sleep until STUNNEL_EXPORT_HOUR UTC then export + prune. [RM][PA]"""
    while True:
        now = datetime.now(tz=UTC)
        next_run = now.replace(hour=export_hour, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run.replace(day=next_run.day + 1)
        wait_seconds = (next_run - now).total_seconds()
        logger.info(
            "Nightly export scheduled in %.0f s (at %s UTC)",
            wait_seconds,
            next_run.isoformat(),
            extra={"subsys": "parquet", "event": "scheduled"},
        )
        await asyncio.sleep(wait_seconds)

        try:
            stats = await exporter.export_yesterday()
            await repo.prune_old_rows(retention_days)
            logger.info(
                "Nightly export done: %s",
                stats,
                extra={"subsys": "parquet", "event": "nightly_done", "detail": str(stats)},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Nightly export failed",
                extra={"subsys": "parquet", "event": "nightly_error"},
            )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    cfg = AppConfig()
    setup_logging(cfg.log_level_int)

    logger.info(
        "✔ stuck-in-tunnel starting — routes: %s",
        cfg.routes,
        extra={"subsys": "bootstrap", "event": "startup"},
    )

    # Shared state
    pattern_cache = PatternCache(maxsize=cfg.max_cached_patterns)
    route_pattern_map: dict[str, list[Pattern]] = {}

    location_queue: asyncio.Queue[VehicleLocation] = asyncio.Queue()
    arrival_queue: asyncio.Queue[BusArrival] = asyncio.Queue()

    loader = GTFSStaticLoader(
        static_url=cfg.gtfs_static_url,
        api_key=cfg.gtfs_rt_key,
    )

    async with SQLiteRepo(cfg.db_path) as repo:
        # Try to seed pattern map from SQLite (offline bootstrap)
        for route in cfg.routes:
            stored = await repo.get_patterns_for_route(route)
            for p in stored:
                route_pattern_map.setdefault(p.route, []).append(p)
        if route_pattern_map:
            logger.info(
                "Seeded pattern map from SQLite (%d routes)",
                len(route_pattern_map),
                extra={"subsys": "bootstrap", "event": "seed_patterns"},
            )

        exporter = ParquetExporter(parquet_dir=cfg.parquet_dir, repo=repo)

        # Pattern getter used by producer pipeline
        async def get_pattern(route: str, pattern_id: int) -> Pattern | None:
            async def loader_fn(pid: int) -> Pattern | None:
                candidates = route_pattern_map.get(route, [])
                return next((p for p in candidates if p.pattern_id == pid), None)

            return await pattern_cache.get_or_load(pattern_id, loader_fn)

        # Vehicle fetcher
        async with GTFSRTClient(
            feed_url=cfg.gtfs_rt_url,
            api_key=cfg.gtfs_rt_key,
            routes=cfg.routes,
        ) as gtfs_client:

            async def fetch_vehicles() -> list[VehicleLocation]:
                return await gtfs_client.get_vehicle_positions()

            tasks = [
                asyncio.create_task(
                    _gtfs_static_refresh_task(loader, repo, pattern_cache, route_pattern_map),
                    name="gtfs_static_refresh",
                ),
                asyncio.create_task(
                    producer_pipeline(
                        fetch_vehicles=fetch_vehicles,
                        get_pattern=get_pattern,
                        location_queue=location_queue,
                        arrival_queue=arrival_queue,
                        poll_interval=cfg.poll_interval,
                        max_trips=cfg.max_cached_patterns,
                    ),
                    name="producer",
                ),
                asyncio.create_task(
                    _consumer_locations(location_queue, repo),
                    name="consumer_locations",
                ),
                asyncio.create_task(
                    _consumer_arrivals(arrival_queue, repo),
                    name="consumer_arrivals",
                ),
                asyncio.create_task(
                    _nightly_export_task(exporter, repo, cfg.export_hour, cfg.hot_retention_days),
                    name="nightly_export",
                ),
            ]

            loop = asyncio.get_running_loop()

            def _shutdown(sig_name: str) -> None:
                logger.info(
                    "Received %s — shutting down",
                    sig_name,
                    extra={"subsys": "main", "event": "shutdown"},
                )
                for t in tasks:
                    t.cancel()

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _shutdown, sig.name)

            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                logger.info(
                    "✔ stuck-in-tunnel stopped cleanly",
                    extra={"subsys": "main", "event": "exit"},
                )


if __name__ == "__main__":
    asyncio.run(main())
