"""Verify GTFS-RT API connectivity and field presence.

Run manually once credentials are issued from developer.njtransit.com:
    STUNNEL_GTFS_RT_KEY=xxx uv run python utils/verify_gtfs.py

Prints a summary of the first few vehicle positions and confirms required
fields are present. [KBT][IV]
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

from utils.logging_setup import setup_logging

logger = setup_logging(logging.DEBUG)


async def verify_gtfs_rt() -> None:
    from src.adapters.gtfs_rt_client import GTFSRTClient

    key = os.getenv("STUNNEL_GTFS_RT_KEY", "")
    url = os.getenv("STUNNEL_GTFS_RT_URL", "")
    routes_raw = os.getenv("STUNNEL_ROUTES", "126")
    routes = [r.strip() for r in routes_raw.split(",") if r.strip()]

    if not key or not url:
        logger.error(
            "✖ STUNNEL_GTFS_RT_KEY and STUNNEL_GTFS_RT_URL must be set",
            extra={"subsys": "verify", "event": "missing_config"},
        )
        sys.exit(1)

    logger.info(
        "Verifying GTFS-RT feed: url=%s routes=%s",
        url,
        routes,
        extra={"subsys": "verify", "event": "start"},
    )

    async with GTFSRTClient(feed_url=url, api_key=key, routes=routes) as client:
        locations = await client.get_vehicle_positions()

    if not locations:
        logger.warning(
            "⚠ No vehicle positions returned — check route IDs and API key",
            extra={"subsys": "verify", "event": "empty"},
        )
        sys.exit(1)

    logger.info(
        "✔ Received %d vehicle positions",
        len(locations),
        extra={"subsys": "verify", "event": "success"},
    )

    for loc in locations[:5]:
        logger.info(
            "  vehicle_id=%-8s route=%-5s lat=%.5f lon=%.5f speed=%s trip=%s",
            loc.vehicle_id,
            loc.route,
            loc.latitude,
            loc.longitude,
            loc.speed,
            loc.trip_id,
            extra={"subsys": "verify", "event": "sample"},
        )

    # Field presence checks
    required = ["vehicle_id", "timestamp", "latitude", "longitude", "route"]
    missing = [f for f in required if getattr(locations[0], f, None) is None]
    if missing:
        logger.error(
            "✖ Missing required fields: %s",
            missing,
            extra={"subsys": "verify", "event": "field_check_fail"},
        )
        sys.exit(1)

    logger.info(
        "✔ All required fields present",
        extra={"subsys": "verify", "event": "field_check_pass"},
    )


async def verify_gtfs_static() -> None:
    from src.adapters.gtfs_static_loader import GTFSStaticLoader

    key = os.getenv("STUNNEL_GTFS_RT_KEY", "")
    url = os.getenv("STUNNEL_GTFS_STATIC_URL", "")

    if not url:
        logger.warning(
            "⚠ STUNNEL_GTFS_STATIC_URL not set — skipping static feed check",
            extra={"subsys": "verify", "event": "skip_static"},
        )
        return

    loader = GTFSStaticLoader(static_url=url, api_key=key)
    routes, patterns = await loader.load()

    logger.info(
        "✔ GTFS static: %d routes, %d patterns",
        len(routes),
        len(patterns),
        extra={"subsys": "verify", "event": "static_ok"},
    )


async def main() -> None:
    await verify_gtfs_rt()
    await verify_gtfs_static()


if __name__ == "__main__":
    asyncio.run(main())
