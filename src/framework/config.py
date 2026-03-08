"""Application configuration loaded from environment variables. [IV][SFT]"""

from __future__ import annotations

import logging
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.framework import constants as C


class AppConfig(BaseSettings):
    """All configuration sourced from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_prefix="STUNNEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required
    routes: Annotated[list[str], Field(description="Comma-separated NJ Transit route IDs")] = []
    gtfs_rt_key: Annotated[str, Field(description="API key from developer.njtransit.com")] = ""
    gtfs_rt_url: Annotated[str, Field(description="GTFS-RT vehicle positions feed URL")] = ""
    gtfs_static_url: Annotated[str, Field(description="GTFS static zip URL")] = ""

    # Optional with defaults
    db_path: str = "data/stunnel.db"
    parquet_dir: str = "data/parquet"
    hot_retention_days: int = C.HOT_RETENTION_DAYS
    poll_interval: int = C.POLL_INTERVAL_SECONDS
    max_cached_patterns: int = C.MAX_CACHED_PATTERNS
    export_hour: int = C.NIGHTLY_EXPORT_HOUR_UTC

    # Logging (no STUNNEL_ prefix — shared convention)
    model_config = SettingsConfigDict(
        env_prefix="STUNNEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @field_validator("routes", mode="before")
    @classmethod
    def parse_routes(cls, v: object) -> list[str]:
        """Accept comma-separated string or list."""
        if isinstance(v, str):
            return [r.strip() for r in v.split(",") if r.strip()]
        return list(v)  # type: ignore[arg-type]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        numeric = getattr(logging, v.upper(), None)
        if not isinstance(numeric, int):
            raise ValueError(f"Invalid log level: {v!r}")
        return v.upper()

    @property
    def log_level_int(self) -> int:
        return getattr(logging, self.log_level)
