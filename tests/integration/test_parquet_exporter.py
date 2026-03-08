"""Integration tests for ParquetExporter — uses real pyarrow + temp files. [REH]"""

from datetime import date
from unittest.mock import AsyncMock

import pyarrow.parquet as pq
import pytest

from src.adapters.parquet_exporter import _VEHICLE_LOCATIONS_SCHEMA, ParquetExporter, _write_parquet

pytestmark = pytest.mark.integration


class TestWriteParquet:
    def test_write_creates_file(self, tmp_path):
        rows = [
            {
                "id": 1,
                "vehicle_id": "V1",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "latitude": 40.73,
                "longitude": -74.03,
                "pattern_id": None,
                "route": "126",
                "heading": None,
                "speed": 11.0,
                "destination": None,
                "delayed": 0,
                "passenger_load": None,
                "trip_id": "T1",
                "scheduled_start_dt": None,
                "recorded_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        out = tmp_path / "test.parquet"
        count = _write_parquet(rows, _VEHICLE_LOCATIONS_SCHEMA, out)
        assert count == 1
        assert out.exists()

    def test_written_parquet_readable(self, tmp_path):
        rows = [
            {
                "id": 1,
                "vehicle_id": "BUS99",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "latitude": 40.73,
                "longitude": -74.03,
                "pattern_id": None,
                "route": "23",
                "heading": 180,
                "speed": 25.0,
                "destination": None,
                "delayed": 0,
                "passenger_load": None,
                "trip_id": None,
                "scheduled_start_dt": None,
                "recorded_at": "2026-01-01T00:05:00+00:00",
            }
        ]
        out = tmp_path / "data.parquet"
        _write_parquet(rows, _VEHICLE_LOCATIONS_SCHEMA, out)

        table = pq.read_table(str(out))
        assert table.num_rows == 1
        assert table.column("vehicle_id")[0].as_py() == "BUS99"
        assert table.column("route")[0].as_py() == "23"


class TestParquetExporter:
    async def test_export_date_with_data(self, tmp_path):
        # Mock repo
        mock_repo = AsyncMock()
        mock_repo.fetch_locations_for_date.return_value = [
            {
                "id": 1,
                "vehicle_id": "V1",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "latitude": 40.73,
                "longitude": -74.03,
                "pattern_id": None,
                "route": "126",
                "heading": 90,
                "speed": 10.0,
                "destination": None,
                "delayed": 0,
                "passenger_load": None,
                "trip_id": "T1",
                "scheduled_start_dt": None,
                "recorded_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        mock_repo.fetch_arrivals_for_date.return_value = []

        exporter = ParquetExporter(parquet_dir=str(tmp_path), repo=mock_repo)
        stats = await exporter.export_date(date(2026, 1, 1))

        assert "vehicle_locations" in stats
        assert stats["vehicle_locations"] == 1

        # Verify file exists in Hive partition
        expected = tmp_path / "vehicle_locations" / "year=2026" / "month=01" / "20260101.parquet"
        assert expected.exists()

    async def test_export_date_no_data(self, tmp_path):
        mock_repo = AsyncMock()
        mock_repo.fetch_locations_for_date.return_value = []
        mock_repo.fetch_arrivals_for_date.return_value = []

        exporter = ParquetExporter(parquet_dir=str(tmp_path), repo=mock_repo)
        stats = await exporter.export_date(date(2026, 1, 1))

        assert stats == {}
