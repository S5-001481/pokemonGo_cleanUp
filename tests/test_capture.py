"""Tests for local PNG and UTF-8 metadata persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pokemon_go_cleanup.capture import CaptureService
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.models import Device, ScreenResolution

PNG_BYTES = b"\x89PNG\r\n\x1a\nlocal-test-data"


class FakeAdbGateway:
    """Deterministic ADB boundary used by capture service tests."""

    def resolve_device(self, serial_number: str | None = None) -> Device:
        assert serial_number in {None, "设备:ABC"}
        return Device(serial_number="设备:ABC", state="device")

    def get_resolution(self, serial_number: str) -> ScreenResolution:
        assert serial_number == "设备:ABC"
        return ScreenResolution(width=1080, height=2400)

    def capture_screen(self, serial_number: str) -> bytes:
        assert serial_number == "设备:ABC"
        return PNG_BYTES


def test_capture_saves_dated_png_and_utf8_json(tmp_path: Path) -> None:
    captured_at = datetime(
        2026,
        7,
        28,
        14,
        30,
        15,
        tzinfo=timezone(timedelta(hours=9)),
    )
    config = AppConfig(data_dir=tmp_path / "本地数据")
    service = CaptureService(
        config=config,
        adb=FakeAdbGateway(),
        clock=lambda: captured_at,
    )

    result = service.capture()

    expected_parent = (tmp_path / "本地数据" / "screenshots" / "2026-07-28").resolve()
    assert result.screenshot_path.parent == expected_parent
    assert result.screenshot_path.read_bytes() == PNG_BYTES
    assert result.metadata_path.suffix == ".json"

    metadata: dict[str, Any] = json.loads(
        result.metadata_path.read_text(encoding="utf-8")
    )
    assert metadata["serial_number"] == "设备:ABC"
    assert metadata["resolution"] == {"width": 1080, "height": 2400}
    assert metadata["captured_at"] == "2026-07-28T14:30:15+09:00"
    assert metadata["screenshot_path"] == str(result.screenshot_path)
    assert metadata["metadata_path"] == str(result.metadata_path)
    assert metadata["file_size_bytes"] == len(PNG_BYTES)
    assert metadata["sha256"] == hashlib.sha256(PNG_BYTES).hexdigest()


def test_capture_filename_is_windows_safe(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path)
    service = CaptureService(
        config=config,
        adb=FakeAdbGateway(),
        clock=lambda: datetime(2026, 7, 28, tzinfo=UTC),
    )

    result = service.capture("设备:ABC")

    assert ":" not in result.screenshot_path.name
    assert "设备" not in result.screenshot_path.name
