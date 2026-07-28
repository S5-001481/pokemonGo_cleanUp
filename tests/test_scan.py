"""Tests for the guided three-screenshot scan workflow."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from pokemon_go_cleanup.adb import AdbClient
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import (
    GuidedScanStepError,
    LocalStorageError,
    ScreenshotCaptureError,
)
from pokemon_go_cleanup.models import Device, ScanStep, ScreenResolution
from pokemon_go_cleanup.scan import (
    SCAN_STEPS,
    GuidedScanService,
    _atomic_write_bytes,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\nguided-scan-test"
JST = timezone(timedelta(hours=9))


class FakeAdbGateway:
    """Deterministic scan gateway with an optional capture failure."""

    def __init__(self, fail_on_capture: int | None = None) -> None:
        self.fail_on_capture = fail_on_capture
        self.capture_count = 0
        self.resolved_serial: str | None = None

    def resolve_device(self, serial_number: str | None = None) -> Device:
        self.resolved_serial = serial_number
        return Device(
            serial_number="设备:ABC",
            state="device",
            properties={"model": "HUAWEI_Mate_30"},
        )

    def get_resolution(self, serial_number: str) -> ScreenResolution:
        assert serial_number == "设备:ABC"
        return ScreenResolution(width=1080, height=2400)

    def capture_screen(self, serial_number: str) -> bytes:
        assert serial_number == "设备:ABC"
        self.capture_count += 1
        if self.capture_count == self.fail_on_capture:
            raise ScreenshotCaptureError("simulated ADB screenshot failure")
        return PNG_BYTES + str(self.capture_count).encode()


class FakeAdbRunner:
    """Command-level fake used to exercise AdbClient and the scan service together."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.capture_count = 0

    def run_text(self, command: list[str], *, timeout_seconds: float) -> str:
        assert timeout_seconds == 15
        self.commands.append(command)
        arguments = command[1:]
        if arguments == ["devices", "-l"]:
            return (
                "List of devices attached\n"
                "ABC123 device product:unknown model:HUAWEI_Mate_30 "
                "device:HWLIO transport_id:1\n"
            )
        if arguments == ["-s", "ABC123", "shell", "wm", "size"]:
            return "Physical size: 1080x2400\n"
        raise AssertionError(f"Unexpected text command: {command}")

    def run_bytes(self, command: list[str], *, timeout_seconds: float) -> bytes:
        assert timeout_seconds == 15
        self.commands.append(command)
        assert command[1:] == ["-s", "ABC123", "exec-out", "screencap", "-p"]
        self.capture_count += 1
        return PNG_BYTES + str(self.capture_count).encode()


def _scan_times() -> list[datetime]:
    return [
        datetime(2026, 7, 28, 19, 0, 0, tzinfo=JST),
        datetime(2026, 7, 28, 19, 0, 5, tzinfo=JST),
        datetime(2026, 7, 28, 19, 0, 10, tzinfo=JST),
        datetime(2026, 7, 28, 19, 0, 15, tzinfo=JST),
    ]


def test_guided_scan_saves_three_named_screenshots_and_manifest(
    tmp_path: Path,
) -> None:
    times = iter(_scan_times())
    prepared_steps: list[ScanStep] = []
    gateway = FakeAdbGateway()
    service = GuidedScanService(
        config=AppConfig(data_dir=tmp_path / "本地数据"),
        adb=gateway,
        clock=lambda: next(times),
        token_factory=lambda: "deadbeef",
        application_version="1.2.3",
    )

    result = service.scan_one(
        prepare_step=prepared_steps.append,
        serial_number="设备:ABC",
        notes="保留这个宝可梦",
    )

    assert prepared_steps == list(SCAN_STEPS)
    assert gateway.resolved_serial == "设备:ABC"
    assert result.scan_directory == (
        tmp_path
        / "本地数据"
        / "scans"
        / "2026-07-28"
        / "20260728_190000_000000_deadbeef"
    ).resolve()
    assert {
        path.name for path in result.scan_directory.iterdir()
    } == {"summary.png", "moves.png", "appraisal.png", "manifest.json"}
    assert result.manifest.scan_status == "complete"
    assert result.manifest.failed_step is None
    assert result.manifest.screenshot_filenames == {
        "summary": "summary.png",
        "moves": "moves.png",
        "appraisal": "appraisal.png",
    }

    manifest: dict[str, Any] = json.loads(
        result.manifest_path.read_text(encoding="utf-8")
    )
    assert manifest["scan_id"] == "20260728_190000_000000_deadbeef"
    assert manifest["device_serial"] == "设备:ABC"
    assert manifest["device_model"] == "HUAWEI_Mate_30"
    assert manifest["screen_resolution"] == {"width": 1080, "height": 2400}
    assert manifest["workflow_mode"] == "guided"
    assert manifest["application_version"] == "1.2.3"
    assert manifest["scan_status"] == "complete"
    assert manifest["notes"] == "保留这个宝可梦"
    assert manifest["capture_timestamps"] == {
        "summary": "2026-07-28T19:00:05+09:00",
        "moves": "2026-07-28T19:00:10+09:00",
        "appraisal": "2026-07-28T19:00:15+09:00",
    }
    assert not list(result.scan_directory.glob("*.tmp"))


def test_guided_scan_preserves_success_and_marks_midway_failure(
    tmp_path: Path,
) -> None:
    times = iter(_scan_times())
    service = GuidedScanService(
        config=AppConfig(data_dir=tmp_path),
        adb=FakeAdbGateway(fail_on_capture=2),
        clock=lambda: next(times),
        token_factory=lambda: "feedface",
    )

    with pytest.raises(GuidedScanStepError, match="'moves'") as captured_error:
        service.scan_one(prepare_step=lambda _: None)

    scan_directory = (
        tmp_path
        / "scans"
        / "2026-07-28"
        / "20260728_190000_000000_feedface"
    )
    assert captured_error.value.exit_code == ScreenshotCaptureError.exit_code
    assert (scan_directory / "summary.png").is_file()
    assert not (scan_directory / "moves.png").exists()
    assert not (scan_directory / "appraisal.png").exists()
    manifest: dict[str, Any] = json.loads(
        (scan_directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["scan_status"] == "incomplete"
    assert manifest["failed_step"] == "moves"
    assert manifest["screenshot_filenames"] == {"summary": "summary.png"}
    assert set(manifest["capture_timestamps"]) == {"summary"}
    assert not list(scan_directory.glob("*.tmp"))


def test_atomic_screenshot_write_removes_temporary_file_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / "summary.png"

    def fail_replace(self: Path, target: object) -> Path:
        raise OSError(f"cannot replace {target}")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(LocalStorageError, match="atomically save"):
        _atomic_write_bytes(destination, PNG_BYTES)

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_guided_scan_integration_with_fake_adb_runner(tmp_path: Path) -> None:
    fake_runner = FakeAdbRunner()
    adb = AdbClient(Path("adb"), runner=fake_runner)
    times = iter(_scan_times())
    prepared_steps: list[ScanStep] = []
    service = GuidedScanService(
        config=AppConfig(data_dir=tmp_path),
        adb=adb,
        clock=lambda: next(times),
        token_factory=lambda: "1234abcd",
    )

    result = service.scan_one(
        prepare_step=prepared_steps.append,
        notes="integration fake",
    )

    assert result.manifest.scan_status == "complete"
    assert result.manifest.device_serial == "ABC123"
    assert result.manifest.device_model == "HUAWEI_Mate_30"
    assert prepared_steps == ["summary", "moves", "appraisal"]
    assert len(fake_runner.commands) == 5
    assert all(
        "tap" not in command and "swipe" not in command
        for command in fake_runner.commands
    )
