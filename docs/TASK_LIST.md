# Task List

## Current Status — 2026-03-08

Python rewrite **complete and tested**. All 49 tests pass. Ruff clean. Bandit
clean. Awaiting NJ Transit API key approval before live run.

---

## Completed ✅

- [x] `pyproject.toml` — project bootstrap, deps, pytest/ruff config
- [x] `utils/logging_setup.py` — dual-sink logging with enforcer
- [x] `src/framework/config.py` — pydantic-settings AppConfig
- [x] `src/framework/constants.py` — all magic values
- [x] `src/domain/models.py` — all domain models (Pydantic v2, frozen)
- [x] `src/domain/geometry.py` — virtual gate algorithm (port of GeoUtils.scala)
- [x] `src/adapters/gtfs_rt_client.py` — GTFS-RT protobuf client
- [x] `src/adapters/gtfs_static_loader.py` — GTFS static zip parser
- [x] `src/adapters/sqlite_repo.py` — aiosqlite hot store + schema
- [x] `src/adapters/parquet_exporter.py` — nightly Parquet export
- [x] `src/usecases/tracking.py` — trackMovement + trackBusStopArrivals
- [x] `src/usecases/pattern_cache.py` — async TTL+LRU cache
- [x] `src/framework/main.py` — full asyncio pipeline wiring
- [x] `utils/verify_gtfs.py` — API connectivity check script
- [x] Unit tests: models, geometry, tracking, logging setup (49/49 passing)
- [x] Integration tests: SQLiteRepo, ParquetExporter, GTFSRTClient
- [x] Ruff lint + format (clean)
- [x] Bandit security audit (no medium/high findings)
- [x] `.env.example`, `.gitignore`
- [x] `docs/ARCHITECTURE.md`
- [x] `docs/LOGGING.md`
- [x] `docs/SECURITY.md`
- [x] `docs/CHANGELOG.md`
- [x] `docs/TASK_LIST.md`

---

## Blocked 🚧

- [ ] **Live end-to-end run** — blocked on NJ Transit API key approval
  (~5 business days from registration at `developer.njtransit.com`)
  - Keys needed: `STUNNEL_GTFS_RT_KEY`, `STUNNEL_GTFS_RT_URL`,
    `STUNNEL_GTFS_STATIC_URL`
  - Once received: run `utils/verify_gtfs.py`, then `src/framework/main.py`

---

## Pending / TODOs 📋

### High priority (before first live run)
- [ ] Verify GTFS static stop-to-shape alignment is sufficient for gate detection
  - Current: stops appended as separate `STOP` points after shape waypoints
  - Ideal: interpolate stop positions along the shape polyline
  - Risk: gate may not fire if stop coordinates are far from shape segments
- [ ] Test `pattern_id` matching with real GTFS data
  - GTFS-RT `trip.route_id` → pattern lookup uses route+direction+shape key
  - Confirm `VehicleLocation.pattern_id` is correctly set or can be derived
    from `trip_id → route_pattern_map`

### Medium priority
- [ ] systemd service unit file for bare-metal deployment
  - `[Service] ExecStart=uv run python src/framework/main.py`
  - `EnvironmentFile=/etc/stunnel/env`
  - `Restart=on-failure`
- [ ] Add `pytest-cov` and enforce ≥85% line coverage
- [ ] Property-based tests (Hypothesis) for geometry edge cases
  - e.g. zero-length segments, antipodal points, crossing at exact gate centre
- [ ] Integration test for `gtfs_static_loader` against a real (offline) GTFS zip
- [ ] Nightly export integration test covering the full date partition path

### Low priority / Nice to have
- [ ] DuckDB query examples in `docs/ARCHITECTURE.md` (after first export)
- [ ] Metrics / health endpoint (Prometheus? simple HTTP /health?) for systemd
  watchdog
- [ ] Alert on zero vehicles returned for 3 consecutive polls (potential API issue)
- [ ] GTFS static stop interpolation along shape (improve arrival accuracy)
- [ ] `make` targets: `run`, `test`, `lint`, `format`

---

## Known Limitations

| Area | Limitation | Impact |
|---|---|---|
| `pattern_id` | GTFS-RT does not provide a BusTime `pid` — pattern lookup is by `(route, direction, shape_id)` | Arrivals only fire if `loc.pattern_id` is matched in cache |
| Stop alignment | Stops appended after shape waypoints, not interpolated | Gate may miss stops whose coords are offset from the shape |
| `destination` | Not available in GTFS-RT | Low — not used in arrival detection |
| `delayed` flag | Not available in GTFS-RT | Low — derivable from schedule if needed |
| `passenger_load` | Not available in GTFS-RT | Not needed for tunnel prediction |

---

## Verification Checklist (run once API keys arrive)

```bash
# 1. Install deps
uv sync

# 2. Unit tests (no keys needed)
uv run -m pytest -q -m unit

# 3. Verify GTFS-RT connectivity
STUNNEL_GTFS_RT_KEY=xxx uv run python utils/verify_gtfs.py

# 4. Full pipeline
cp .env.example .env  # fill in keys + routes
uv run python src/framework/main.py

# 5. Inspect hot store
sqlite3 data/stunnel.db "SELECT COUNT(*) FROM vehicle_locations;"
sqlite3 data/stunnel.db "SELECT * FROM bus_arrivals ORDER BY arrival_timestamp DESC LIMIT 10;"

# 6. Query cold archive (after first nightly export)
uv run python -c "
import duckdb
duckdb.sql(\"SELECT route, AVG(speed) FROM 'data/parquet/vehicle_locations/**/*.parquet' GROUP BY route\").show()
"

# 7. Lint + security
uv run ruff check . && uv run ruff format --check . && uv run bandit -q -r src
```
