# Architecture

## Overview

`stuck-in-tunnel` is a real-time NJ Transit bus tracker. It polls the official
GTFS-RT vehicle positions feed, computes bus stop crossing events via geometric
gate detection, and persists data to a hybrid store (SQLite hot + Parquet cold).

**Language**: Python 3.12+ (full asyncio rewrite of Scala/Cats Effect original)
**Runtime**: `uv run python src/framework/main.py`
**Deployment**: bare-metal systemd service, no cloud dependencies

---

## Layer Map (Clean Architecture)

```
┌────────────────────────────────────────────────────────────┐
│  src/framework/           Entrypoint + wiring              │
│    main.py                asyncio pipeline, signal handlers │
│    config.py              AppConfig (pydantic-settings)    │
│    constants.py           All magic values                  │
├────────────────────────────────────────────────────────────┤
│  src/usecases/            Application logic                 │
│    tracking.py            trackMovement, arrivals, pipeline │
│    pattern_cache.py       Async TTL+LRU cache               │
├────────────────────────────────────────────────────────────┤
│  src/domain/              Pure business logic — no I/O     │
│    models.py              Pydantic domain models            │
│    geometry.py            Virtual gate / stop crossing math │
├────────────────────────────────────────────────────────────┤
│  src/adapters/            External boundaries               │
│    gtfs_rt_client.py      GTFS-RT protobuf HTTP client      │
│    gtfs_static_loader.py  GTFS static zip parser            │
│    sqlite_repo.py         aiosqlite hot store               │
│    parquet_exporter.py    pyarrow Parquet cold archive      │
├────────────────────────────────────────────────────────────┤
│  utils/                   One-off scripts                   │
│    logging_setup.py       Dual-sink bootstrap               │
│    verify_gtfs.py         API connectivity check            │
└────────────────────────────────────────────────────────────┘
```

**Invariant**: inner layers never import outward. `domain` has no I/O.
`usecases` depends on `domain` + adapter *interfaces* injected at `main.py`.

---

## Async Pipeline

```
asyncio.gather(
  gtfs_static_refresh_task()   ─────────────────────────────────────┐
                                                                      │ populates
  producer_task()                                                     ▼
    every POLL_INTERVAL s:               route_pattern_map (dict)
      fetch GTFS-RT feed (1 HTTP req)    pattern_cache (TTLCache)
      parse protobuf → List[VehicleLocation]
      filter to STUNNEL_ROUTES
      for each loc:
        track_movement() → VehicleMovement?
        track_bus_stop_arrivals() → List[BusArrival]
        → location_queue.put(loc)
        → arrival_queue.put(arrival)

  consumer_locations_task()
    drain location_queue in batches ≤500
    flush on batch-full OR 5s timeout
    → SQLiteRepo.bulk_insert_locations()

  consumer_arrivals_task()
    drain arrival_queue in batches ≤500
    flush on batch-full OR 5s timeout
    → SQLiteRepo.bulk_insert_arrivals()

  nightly_export_task()
    sleep until STUNNEL_EXPORT_HOUR UTC (default 02:00)
    → ParquetExporter.export_yesterday()
    → SQLiteRepo.prune_old_rows(retention_days=7)
)
```

All tasks are long-lived. `SIGINT`/`SIGTERM` cancel all tasks gracefully,
flushing in-flight batches before exit.

---

## Data Sources

### GTFS-RT Vehicle Positions (real-time)
- **Provider**: NJ Transit Developer Portal (`developer.njtransit.com`)
- **Format**: protobuf `FeedMessage` (GTFS-RT v2.0)
- **Polling**: every `STUNNEL_POLL_INTERVAL` seconds (default 10 s)
- **Auth**: Bearer token in `Authorization` header
- **Cost**: ~8,640 req/day at 10 s interval — well within 100k/day quota
- **Parser**: `gtfs-realtime-bindings` → `src/adapters/gtfs_rt_client.py`

### GTFS Static Bundle (route geometry)
- **Format**: ZIP of CSV files (`routes.txt`, `trips.txt`, `stops.txt`,
  `stop_times.txt`, `shapes.txt`)
- **Refresh**: once on startup, then every 24 h
- **Parser**: `src/adapters/gtfs_static_loader.py`
- **Output**: `Route` + `Pattern` domain objects, persisted to SQLite for
  offline replay

---

## Persistence

### SQLite Hot Store (`data/stunnel.db`)
- **Tables**: `routes`, `patterns`, `vehicle_locations`, `bus_arrivals`
- **Retention**: 7 days rolling window (pruned nightly)
- **Purpose**: queryable real-time data, offline pattern replay on restart
- See full DDL in `src/adapters/sqlite_repo.py`

### Parquet Cold Archive (`data/parquet/`)
- **Layout**: Hive-partitioned — `{table}/year=YYYY/month=MM/YYYYMMDD.parquet`
- **Written**: nightly at `STUNNEL_EXPORT_HOUR` UTC from previous day's SQLite rows
- **Query**: DuckDB — `SELECT … FROM 'data/parquet/vehicle_locations/**/*.parquet'`
- **Compression**: Snappy

---

## Geometry: Virtual Gate Algorithm

Port of `stunnel.geometry.GeoUtils` (Scala/JTS) → `src/domain/geometry.py`
(shapely).

```
1. Project lat/lon → Web Mercator metres
     x = lon × R × π/180
     y = 0.5 × R × ln((1 + sin(lat × π/180)) / (1 − sin(lat × π/180)))
     where R = 6,378,137 m (WGS-84 equatorial radius)

2. For each consecutive pair of pattern points [i, i+1]:
     Build a perpendicular LineString (the "gate") at fraction=0.995
     along the segment, width=100 m.

3. Test: does the vehicle movement segment intersect the gate?
     If yes → point[i+1] was crossed.

4. Filter crossed points to is_stop=True → BusArrival events.
```

Gate construction uses vector rotation:
- direction vector `d = (x2-x1, y2-y1)`
- rotate `d` by `±(π − centralAngle/2)` → unit perpendicular arms
- gate = LineString from `center − arm×halfWidth` to `center + arm×halfWidth`

---

## Key Domain Models

```python
VehicleLocation   # one GPS ping from GTFS-RT
VehicleMovement   # (previous: VL, current: VL) — one poll delta
Pattern           # route shape + stops (from GTFS static)
Point             # waypoint or stop within a pattern
BusArrival        # detected stop crossing event
Route             # route metadata
```

All models are `frozen=True` Pydantic v2. `extra="ignore"` on all — unknown
API fields are silently dropped.

---

## Configuration

All config via environment variables (see `.env.example`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `STUNNEL_ROUTES` | ✅ | — | Comma-separated route IDs |
| `STUNNEL_GTFS_RT_KEY` | ✅ | — | NJ Transit API key |
| `STUNNEL_GTFS_RT_URL` | ✅ | — | GTFS-RT feed URL |
| `STUNNEL_GTFS_STATIC_URL` | ✅ | — | GTFS static zip URL |
| `STUNNEL_DB_PATH` | — | `data/stunnel.db` | SQLite path |
| `STUNNEL_PARQUET_DIR` | — | `data/parquet` | Parquet root |
| `STUNNEL_HOT_RETENTION_DAYS` | — | `7` | SQLite rolling window |
| `STUNNEL_POLL_INTERVAL` | — | `10` | Poll frequency (seconds) |
| `STUNNEL_MAX_CACHED_PATTERNS` | — | `500` | Pattern cache size |
| `STUNNEL_EXPORT_HOUR` | — | `2` | Nightly export hour (UTC) |
| `APP_JSONL_PATH` | — | `logs/app.jsonl` | Structured log path |
| `LOG_LEVEL` | — | `INFO` | Log verbosity |

---

## Scala → Python Mapping

| Scala | Python |
|---|---|
| Cats Effect `IO` / `IOApp` | `asyncio` / `asyncio.run()` |
| FS2 `Stream` | `async for` + `asyncio.Queue` |
| http4s `EmberClient` | `httpx.AsyncClient` |
| circe JSON decoders | `pydantic.BaseModel` |
| JTS geometry | `shapely` |
| Apache Arrow IPC | pyarrow (Parquet export only) |
| fs2-aws-s3 | removed — local disk Parquet |
| BusTime `/getvehicles` | GTFS-RT protobuf VehiclePositions |
| BusTime `/getpatterns` | GTFS static `stops.txt` + `shapes.txt` |
| HMAC-SHA256 signing | Bearer token |
| `Cached` / `KeyedCache` | `cachetools.TTLCache` + `asyncio.Lock` |
| Logback | Rich + `RotatingFileHandler` |
| refined `NonEmptyString` | `Annotated[str, Field(min_length=1)]` |

---

## File Size Reference (CSD gate: 300 SLOC)

| File | SLOC | Status |
|---|---|---|
| `src/adapters/sqlite_repo.py` | ~200 | ✅ |
| `src/adapters/gtfs_static_loader.py` | ~170 | ✅ |
| `src/adapters/parquet_exporter.py` | ~110 | ✅ |
| `src/adapters/gtfs_rt_client.py` | ~80 | ✅ |
| `src/framework/main.py` | ~210 | ✅ |
| `src/domain/geometry.py` | ~110 | ✅ |
| `src/domain/models.py` | ~100 | ✅ |
| `src/usecases/tracking.py` | ~130 | ✅ |
| `src/usecases/pattern_cache.py` | ~70 | ✅ |
| `utils/logging_setup.py` | ~80 | ✅ |
