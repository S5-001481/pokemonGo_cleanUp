"""Tests for structured logging output."""

from __future__ import annotations

import json
import logging

from pokemon_go_cleanup.logging_config import JsonFormatter


def test_json_formatter_preserves_structured_fields_and_unicode() -> None:
    record = logging.makeLogRecord(
        {
            "name": "pokemon_go_cleanup.test",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "pathname": __file__,
            "lineno": 1,
            "msg": "截图已保存",
            "args": (),
            "exc_info": None,
            "serial_number": "设备ABC",
            "file_size_bytes": 123,
        }
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "截图已保存"
    assert payload["level"] == "INFO"
    assert payload["serial_number"] == "设备ABC"
    assert payload["file_size_bytes"] == 123
