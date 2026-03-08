# Logging

## Dual-Sink Strategy

All logging goes through two sinks simultaneously, configured in
`utils/logging_setup.py`. The startup enforcer aborts (`os._exit(2)`) if
both sinks are not active.

---

## Sink 1 — Pretty Console (RichHandler)

- **Output**: stderr
- **Library**: `rich.logging.RichHandler`
- **Timestamps**: local time, millisecond precision (`%Y-%m-%d %H:%M:%S.%f`)
- **Tracebacks**: Rich tracebacks enabled; locals shown at `DEBUG` level
- **Markup**: enabled for colour/bold in log messages

**Level colour palette**

| Level | Symbol | Colour |
|---|---|---|
| DEBUG | ℹ | blue-grey |
| INFO | ✔ | green |
| WARNING | ⚠ | yellow |
| ERROR | ✖ | red |
| CRITICAL | ✖ | red |

---

## Sink 2 — Structured JSONL (RotatingFileHandler)

- **Path**: `logs/app.jsonl` (overridden by `APP_JSONL_PATH`)
- **Rotation**: 10 MB × 5 backups
- **Format**: one JSON object per line

**Standard fields**

| Field | Type | Description |
|---|---|---|
| `ts` | string | `YYYY-MM-DD HH:MM:SS.mmm` (local time) |
| `level` | string | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `name` | string | Logger name (module path) |
| `message` | string | Formatted log message |
| `subsys` | string | Subsystem tag (e.g. `gtfs_rt`, `sqlite`, `tracking`) |
| `event` | string | Event key (e.g. `poll`, `stop_crossing`, `export`) |
| `detail` | string | Optional extra detail |
| `guild_id` | string | Reserved (Discord context, unused) |
| `user_id` | string | Reserved |
| `msg_id` | string | Reserved |

Only non-null fields are emitted. Fields follow the key order defined in
`JSONLFormatter._JSONL_KEYS`.

**Example line**
```json
{"ts":"2026-03-08 02:00:01.042","level":"INFO","name":"src.adapters.parquet_exporter","subsys":"parquet","event":"export","message":"Exported 4821 vehicle_locations rows → data/parquet/vehicle_locations/year=2026/month=03/20260307.parquet","detail":"data/parquet/vehicle_locations/year=2026/month=03/20260307.parquet"}
```

---

## Subsystem Tags

| `subsys` | Source file |
|---|---|
| `bootstrap` | `src/framework/main.py` (startup/shutdown) |
| `gtfs_rt` | `src/adapters/gtfs_rt_client.py` |
| `gtfs_static` | `src/adapters/gtfs_static_loader.py` |
| `sqlite` | `src/adapters/sqlite_repo.py` |
| `parquet` | `src/adapters/parquet_exporter.py` |
| `tracking` | `src/usecases/tracking.py` |
| `producer` | `src/usecases/tracking.py` (pipeline loop) |
| `verify` | `utils/verify_gtfs.py` |

---

## Usage Pattern

```python
import logging
logger = logging.getLogger(__name__)

# Minimal
logger.info("message")

# With subsystem and event
logger.info(
    "Fetched %d vehicle locations", count,
    extra={"subsys": "producer", "event": "poll"},
)

# With detail
logger.info(
    "Exported %d rows → %s", count, path,
    extra={"subsys": "parquet", "event": "export", "detail": str(path)},
)

# Exception (Rich traceback + locals at DEBUG)
logger.exception("Unexpected error", extra={"subsys": "main", "event": "error"})
```

**Setup at entrypoint**

```python
from utils.logging_setup import setup_logging
from src.framework.config import AppConfig

cfg = AppConfig()
setup_logging(cfg.log_level_int)  # call once before any logging
```

`setup_logging()` is idempotent — safe to call multiple times.

---

## Enforcer

On startup, `setup_logging()` verifies:

```python
names = sorted(h.get_name() for h in root.handlers)
assert names == ["jsonl_handler", "pretty_handler"]
```

If either sink is missing or misconfigured, it writes to stderr and calls
`os._exit(2)` (immediate, non-zero exit, bypasses `atexit`).

---

## Log Levels

| Level | When to use |
|---|---|
| `DEBUG` | Per-vehicle parse details, cache hits, gate intersection internals |
| `INFO` | Poll results, GTFS refresh, Parquet exports, startup/shutdown |
| `WARNING` | Missing GTFS files, empty feed responses, non-fatal skips |
| `ERROR` | HTTP errors, DB errors, pattern load failures |
| `CRITICAL` | (reserved — use `logger.exception` + re-raise for fatal errors) |

Default production level: `INFO`. Set `LOG_LEVEL=DEBUG` for verbose output.

---

## Querying JSONL Logs

```bash
# Last 50 stop crossing events
grep '"event":"stop_crossing"' logs/app.jsonl | tail -50 | jq .

# All errors in the last hour
jq 'select(.level == "ERROR")' logs/app.jsonl

# Poll event timing
jq 'select(.event == "poll") | .ts' logs/app.jsonl
```
