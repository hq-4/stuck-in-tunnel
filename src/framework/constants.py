"""Application-wide constants. No magic values elsewhere. [CMV]"""

# Polling
POLL_INTERVAL_SECONDS: int = 10
CONSUMER_FLUSH_INTERVAL_SECONDS: float = 5.0
CONSUMER_BATCH_SIZE: int = 500

# Pattern cache
MAX_CACHED_PATTERNS: int = 500
PATTERN_CACHE_TTL_SECONDS: int = 86_400  # 24 hours

# Geometry / gate detection
MERCATOR_RADIUS: float = 6_378_137.0  # Earth radius in meters (Web Mercator)
GATE_FRACTION: float = 0.995  # fraction along segment where gate center sits
GATE_WIDTH_METERS: float = 100.0  # perpendicular gate half-width × 2
GATE_CENTRAL_ANGLE: float = 3.141592653589793  # π — full perpendicular gate

# Persistence
HOT_RETENTION_DAYS: int = 7
NIGHTLY_EXPORT_HOUR_UTC: int = 2  # hour at which nightly Parquet export runs
PARQUET_COMPRESSION: str = "snappy"

# HTTP client
HTTP_TIMEOUT_SECONDS: float = 10.0
GTFS_STATIC_REFRESH_INTERVAL_SECONDS: int = 86_400  # 24 hours

# GTFS-RT
GTFS_RT_BEARER_HEADER: str = "Authorization"
