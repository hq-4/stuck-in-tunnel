"""Nightly Parquet cold-archive exporter.

Writes Hive-partitioned Parquet files from SQLite data using pyarrow.
Queryable via DuckDB. [CA][PA][RM]

Replaces fs2-aws-s3 / Arrow IPC upload in Scala.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.adapters.sqlite_repo import SQLiteRepo
from src.framework.constants import PARQUET_COMPRESSION

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Arrow schemas
# ---------------------------------------------------------------------------

_VEHICLE_LOCATIONS_SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64()),
        pa.field("vehicle_id", pa.string()),
        pa.field("timestamp", pa.string()),
        pa.field("latitude", pa.float64()),
        pa.field("longitude", pa.float64()),
        pa.field("pattern_id", pa.int64()),
        pa.field("route", pa.string()),
        pa.field("heading", pa.int32()),
        pa.field("speed", pa.float64()),
        pa.field("destination", pa.string()),
        pa.field("delayed", pa.int8()),
        pa.field("passenger_load", pa.string()),
        pa.field("trip_id", pa.string()),
        pa.field("scheduled_start_dt", pa.string()),
        pa.field("recorded_at", pa.string()),
    ]
)

_BUS_ARRIVALS_SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64()),
        pa.field("vehicle_id", pa.string()),
        pa.field("route", pa.string()),
        pa.field("pattern_id", pa.int64()),
        pa.field("stop_id", pa.string()),
        pa.field("stop_name", pa.string()),
        pa.field("stop_seq", pa.int32()),
        pa.field("arrival_timestamp", pa.string()),
        pa.field("latitude", pa.float64()),
        pa.field("longitude", pa.float64()),
        pa.field("recorded_at", pa.string()),
    ]
)


class ParquetExporter:
    """Exports yesterday's SQLite data to Hive-partitioned Parquet files. [RM]"""

    def __init__(self, parquet_dir: str, repo: SQLiteRepo) -> None:
        self._root = Path(parquet_dir)
        self._repo = repo

    async def export_yesterday(self) -> dict[str, int]:
        """Export yesterday's data. Returns {table: row_count}."""
        yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).date()
        return await self.export_date(yesterday)

    async def export_date(self, target_date: date) -> dict[str, int]:
        """Export all data for `target_date` to Parquet. [RM]"""
        date_str = target_date.strftime("%Y-%m-%d")
        year_str = target_date.strftime("%Y")
        month_str = target_date.strftime("%m")
        fname = target_date.strftime("%Y%m%d") + ".parquet"

        stats: dict[str, int] = {}

        # --- vehicle_locations ---
        vl_rows = await self._repo.fetch_locations_for_date(date_str)
        if vl_rows:
            vl_path = (
                self._root / "vehicle_locations" / f"year={year_str}" / f"month={month_str}" / fname
            )
            row_count = _write_parquet(vl_rows, _VEHICLE_LOCATIONS_SCHEMA, vl_path)
            stats["vehicle_locations"] = row_count
            logger.info(
                "Exported %d vehicle_locations rows → %s",
                row_count,
                vl_path,
                extra={"subsys": "parquet", "event": "export", "detail": str(vl_path)},
            )

        # --- bus_arrivals ---
        ba_rows = await self._repo.fetch_arrivals_for_date(date_str)
        if ba_rows:
            ba_path = (
                self._root / "bus_arrivals" / f"year={year_str}" / f"month={month_str}" / fname
            )
            row_count = _write_parquet(ba_rows, _BUS_ARRIVALS_SCHEMA, ba_path)
            stats["bus_arrivals"] = row_count
            logger.info(
                "Exported %d bus_arrivals rows → %s",
                row_count,
                ba_path,
                extra={"subsys": "parquet", "event": "export", "detail": str(ba_path)},
            )

        if not stats:
            logger.info(
                "No data to export for %s",
                date_str,
                extra={"subsys": "parquet", "event": "export_empty"},
            )

        return stats


def _write_parquet(
    rows: list[dict],
    schema: pa.Schema,
    output_path: Path,
) -> int:
    """Convert list of dicts to a Parquet file using pyarrow. Returns row count."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build columns from row dicts
    col_data: dict[str, list] = {field.name: [] for field in schema}
    for row in rows:
        for field in schema:
            col_data[field.name].append(row.get(field.name))

    arrays = [pa.array(col_data[field.name], type=field.type) for field in schema]
    table = pa.table(dict(zip([f.name for f in schema], arrays, strict=True)), schema=schema)

    pq.write_table(table, str(output_path), compression=PARQUET_COMPRESSION)
    return len(table)
