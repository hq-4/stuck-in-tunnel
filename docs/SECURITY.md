# Security

## Threat Model

`stuck-in-tunnel` is a read-only data collection daemon. It:
- Makes outbound HTTP GET requests to a single trusted API endpoint
- Writes data to local SQLite + Parquet files
- Accepts no inbound connections
- Runs as an unprivileged local user

Primary attack surface is **supply chain** (dependencies) and
**credential exposure** (API key).

---

## Secrets Policy

| Secret | Storage | Notes |
|---|---|---|
| `STUNNEL_GTFS_RT_KEY` | `.env` (local) / systemd env unit (prod) | Never logged, never committed |
| `STUNNEL_GTFS_RT_URL` | `.env` / env | May contain key in query param — treat as secret |
| `STUNNEL_GTFS_STATIC_URL` | `.env` / env | Same as above |

**Rules**:
- `.env` is in `.gitignore` — never committed
- `.env.example` contains only keys with empty values — safe to commit
- No secrets appear in log output (neither `pretty_handler` nor JSONL)
- API key is passed as an HTTP header (`Authorization: Bearer …`) — not in URL
  for GTFS-RT requests; static loader uses the same header pattern

**Production deployment**: use `systemd` `EnvironmentFile=` or a secrets
manager (e.g. pass, Vault) to inject env vars. Never hardcode in source.

---

## Input Validation

All external data is validated at adapter boundaries before entering the domain
layer. No raw dicts cross into `domain/` or `usecases/`.

| Boundary | Validation |
|---|---|
| GTFS-RT protobuf | `gtfs-realtime-bindings` parses; missing fields default to zero/empty |
| Domain model construction | Pydantic v2 — `extra="ignore"`, typed fields, `frozen=True` |
| GTFS static CSV rows | Per-row `contextlib.suppress(KeyError, ValueError)` — malformed rows skipped |
| Config / env vars | `pydantic-settings` + `field_validator` for routes and log level |
| SQLite queries | Parameterised queries only — no string interpolation in SQL |

**No user-supplied input** is accepted. All data originates from the NJ Transit
API over HTTPS.

---

## Network Security

- All API requests use `httpx.AsyncClient` over HTTPS
- Timeout: 10 s per request (configurable via `HTTP_TIMEOUT_SECONDS`)
- Static bundle download uses 6× timeout (60 s) for larger payload
- No certificate pinning — relies on system CA bundle (standard for this use case)
- No retries implemented at the HTTP layer; the poll loop retries naturally on
  the next interval

---

## File System

- SQLite database written to `data/stunnel.db` (path configurable)
- Parquet files written to `data/parquet/` (path configurable)
- Log files written to `logs/app.jsonl` (path configurable)
- All paths are gitignored
- No world-readable permissions enforced in code — deploy with restrictive umask

---

## Dependency Audit

Run periodically:
```bash
uv run bandit -q -r src
```

Current findings: 8 × `B101` (assert_used, Low severity) — intentional
internal guard assertions (`assert self._db`) in `SQLiteRepo`. These are
not exploitable since the module is never called from untrusted input.

No medium or high severity findings.

---

## Privacy

- Vehicle IDs and trip IDs are public GTFS data — no PII
- No user data is collected or stored
- Log files contain only operational data (counts, paths, timestamps)
- No hashing of IDs required (no PII present)

---

## Least Privilege

The NJ Transit API key only requires read access to the GTFS-RT and GTFS
static feeds. Do not request write or admin scopes.

The process requires:
- Read: GTFS-RT URL, GTFS static URL
- Write: `data/` and `logs/` directories only
- No network listen ports
- No root or elevated privileges
