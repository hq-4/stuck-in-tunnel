# Changelog

All notable changes follow [Conventional Commits](https://www.conventionalcommits.org/).

---

## [0.1.0] — 2026-03-08

### feat: Full Python rewrite of stuck-in-tunnel [CA][REH][PA][RM][CMV][SFT][IV][CSD][KBT]

Complete rewrite from Scala (Cats Effect / FS2 / JTS) to Python (asyncio /
shapely / pydantic), preserving the original domain logic while replacing the
infrastructure layer.

#### Added

**Bootstrap**
- `pyproject.toml` — uv-managed project, Python 3.12+, all dependencies pinned
- `utils/logging_setup.py` — dual-sink logging (Rich console + JSONL file) with
  startup enforcer per CLAUDE.md §13
- `src/framework/config.py` — `AppConfig` via pydantic-settings (`STUNNEL_` prefix)
- `src/framework/constants.py` — all magic values extracted (poll interval, gate
  dimensions, cache sizes, retention, etc.)
- `.env.example` — documented env var template

**Domain layer** (`src/domain/`)
- `models.py` — `VehicleLocation`, `Pattern`, `Point`, `PointType`, `Route`,
  `VehicleMovement`, `BusArrival` — all frozen Pydantic v2 models
- `geometry.py` — Web Mercator projection + virtual gate algorithm; exact port of
  `stunnel.geometry.GeoUtils` (Scala/JTS) using shapely

**Use-case layer** (`src/usecases/`)
- `tracking.py` — `track_movement()`, `track_bus_stop_arrivals()`,
  `producer_pipeline()` — port of `stunnel.Ops` (Scala/FS2)
- `pattern_cache.py` — async TTL+LRU cache with dog-pile prevention; port of
  `stunnel.concurrent.{Cached,KeyedCache}`

**Adapter layer** (`src/adapters/`)
- `gtfs_rt_client.py` — async httpx client for GTFS-RT protobuf feed (NJ Transit
  Developer Portal); replaces BusTime `/getvehicles` + HMAC-SHA256 signing
- `gtfs_static_loader.py` — downloads GTFS static zip and parses `routes.txt`,
  `trips.txt`, `stops.txt`, `stop_times.txt`, `shapes.txt` into domain objects;
  replaces BusTime `/getpatterns`
- `sqlite_repo.py` — aiosqlite hot store with schema migrations, bulk inserts,
  7-day rolling window pruning
- `parquet_exporter.py` — nightly Hive-partitioned Parquet export via pyarrow;
  replaces Apache Arrow IPC + S3 upload

**Entrypoint** (`src/framework/main.py`)
- Five concurrent asyncio tasks: producer, consumer_locations, consumer_arrivals,
  gtfs_static_refresh, nightly_export
- Graceful SIGINT/SIGTERM shutdown with in-flight batch flushing
- Offline bootstrap: seeds pattern map from SQLite on startup

**Scripts**
- `utils/verify_gtfs.py` — one-off GTFS-RT + static connectivity check; run once
  API credentials arrive from `developer.njtransit.com`

**Tests** (`tests/`)
- `tests/unit/test_models.py` — domain model validation, frozen invariants
- `tests/unit/test_geometry.py` — Mercator projection round-trip, gate geometry,
  `points_crossed` against fixture patterns
- `tests/unit/test_tracking.py` — LRU cache eviction, `track_movement` semantics
- `tests/unit/test_logging_setup.py` — dual handler verification, JSONL field
  schema, idempotency
- `tests/integration/test_sqlite_repo.py` — full round-trip: upsert, fetch, prune
- `tests/integration/test_parquet_exporter.py` — pyarrow write, Hive path layout
- `tests/integration/test_gtfs_rt_client.py` — protobuf parsing, route filter,
  error paths

**Documentation**
- `docs/ARCHITECTURE.md` — layer map, pipeline diagram, data sources, persistence
- `docs/LOGGING.md` — dual-sink spec, JSONL schema, subsystem tags, query examples
- `docs/SECURITY.md` — threat model, secrets policy, input validation, audit
- `docs/CHANGELOG.md` — this file
- `docs/TASK_LIST.md` — current status, TODOs, blockers

#### Changed

- Replaced BusTime private API (`mybusnow.njtransit.com/bustime/api/v3/`) with
  official GTFS-RT from `developer.njtransit.com` (open standard, no HMAC signing)
- Replaced Apache Arrow IPC + S3 cold storage with local Hive-partitioned Parquet
  queryable via DuckDB (no cloud dependency)
- Replaced Logback with Rich + RotatingFileHandler dual-sink strategy

#### Preserved (unchanged)

- Scala source tree (`src/main/scala/`, `src/test/scala/`) — left in place for
  reference; can be archived separately
- Core geometry algorithm — virtual gate logic is a faithful port of
  `GeoUtils.scala` with identical parameters (fraction=0.995, width=100m, π gate)

---

## Pre-rewrite history

The original Scala implementation was last committed ~February 2023. See git log
for `src/main/scala/` history.
