"""Tests for dual-sink logging setup — enforcer + field validation. [REH]"""

import json
import logging

import pytest

pytestmark = pytest.mark.unit


def _reset_root_logger() -> logging.Logger:
    root = logging.getLogger()
    root.handlers = []
    root._configured = False  # type: ignore[attr-defined]
    return root


class TestDualHandlers:
    def test_dual_handlers_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_JSONL_PATH", str(tmp_path / "app.jsonl"))
        _reset_root_logger()

        from utils.logging_setup import setup_logging

        root = setup_logging(logging.DEBUG)

        names = sorted(h.get_name() for h in root.handlers)
        assert names == ["jsonl_handler", "pretty_handler"]

    def test_jsonl_file_created(self, tmp_path, monkeypatch):
        log_path = tmp_path / "app.jsonl"
        monkeypatch.setenv("APP_JSONL_PATH", str(log_path))
        _reset_root_logger()

        from utils.logging_setup import setup_logging

        setup_logging(logging.DEBUG)

        logging.getLogger("test.jsonl").info("hello", extra={"subsys": "x", "event": "unit"})

        assert log_path.exists()
        lines = log_path.read_text().splitlines()
        assert lines, "JSONL file should not be empty"

        obj = json.loads(lines[-1])
        assert obj["level"] == "INFO"
        assert obj["event"] == "unit"
        assert obj["message"] == "hello"
        assert obj["subsys"] == "x"

    def test_idempotent_second_call(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APP_JSONL_PATH", str(tmp_path / "app.jsonl"))
        _reset_root_logger()

        from utils.logging_setup import setup_logging

        root1 = setup_logging(logging.INFO)
        root2 = setup_logging(logging.DEBUG)  # should be no-op

        assert root1 is root2
        assert len(root1.handlers) == 2
