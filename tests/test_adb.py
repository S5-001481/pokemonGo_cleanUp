"""Tests for ADB discovery, parsing, selection, and capture errors."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from pokemon_go_cleanup.adb import (
    AdbClient,
    discover_adb,
    parse_device_list,
    select_device,
)
from pokemon_go_cleanup.exceptions import (
    AdbNotInstalledError,
    MultipleConnectedDevicesError,
    NoConnectedDeviceError,
    ScreenshotCaptureError,
    UnauthorizedDeviceError,
)
from pokemon_go_cleanup.models import Device


def test_parse_device_list_preserves_states_and_properties() -> None:
    output = """List of devices attached
ABC123 device product:unknown model:HUAWEI_Mate_30 device:HWLIO transport_id:1
XYZ987 unauthorized usb:1-2 transport_id:2
"""

    devices = parse_device_list(output)

    assert devices == [
        Device(
            serial_number="ABC123",
            state="device",
            properties={
                "product": "unknown",
                "model": "HUAWEI_Mate_30",
                "device": "HWLIO",
                "transport_id": "1",
            },
        ),
        Device(
            serial_number="XYZ987",
            state="unauthorized",
            properties={"usb": "1-2", "transport_id": "2"},
        ),
    ]


def test_discover_adb_reports_missing_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pokemon_go_cleanup.adb.shutil.which", lambda _: None)

    with pytest.raises(AdbNotInstalledError, match="not installed"):
        discover_adb()


def test_select_device_reports_no_connected_device() -> None:
    with pytest.raises(NoConnectedDeviceError):
        select_device([])


def test_select_device_reports_unauthorized_device() -> None:
    devices = [Device(serial_number="ABC123", state="unauthorized")]

    with pytest.raises(UnauthorizedDeviceError, match="ABC123"):
        select_device(devices)


def test_select_device_reports_multiple_ready_devices() -> None:
    devices = [
        Device(serial_number="ABC123", state="device"),
        Device(serial_number="XYZ987", state="device"),
    ]

    with pytest.raises(MultipleConnectedDevicesError, match="--serial"):
        select_device(devices)


def test_resolution_uses_current_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AdbClient(Path("adb"))
    monkeypatch.setattr(
        client,
        "_run_text",
        lambda _: "Physical size: 1080x2400\nOverride size: 720x1600\n",
    )

    resolution = client.get_resolution("ABC123")

    assert resolution.width == 720
    assert resolution.height == 1600


def test_capture_failure_when_adb_returns_no_png(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AdbClient(Path("adb"))

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=["adb"],
            returncode=0,
            stdout=b"not-a-png",
            stderr=b"",
        )

    monkeypatch.setattr("pokemon_go_cleanup.adb_runner.subprocess.run", fake_run)

    with pytest.raises(ScreenshotCaptureError, match="valid PNG"):
        client.capture_screen("ABC123")


def test_capture_failure_when_adb_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AdbClient(Path("adb"))

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=["adb"],
            returncode=1,
            stdout=b"",
            stderr=b"error: closed",
        )

    monkeypatch.setattr("pokemon_go_cleanup.adb_runner.subprocess.run", fake_run)

    with pytest.raises(ScreenshotCaptureError, match="error: closed"):
        client.capture_screen("ABC123")


def test_adb_input_wrappers_build_explicit_commands() -> None:
    class InputRunner:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def run_text(self, command: list[str], *, timeout_seconds: float) -> str:
            assert timeout_seconds == 15
            self.commands.append(command)
            return ""

        def run_bytes(self, command: list[str], *, timeout_seconds: float) -> bytes:
            raise AssertionError(f"Unexpected byte command: {command}")

    runner = InputRunner()
    client = AdbClient(Path("adb.exe"), runner=runner)

    client.tap("ABC123", 100, 200)
    client.swipe("ABC123", 10, 20, 30, 40, 650)
    client.press_back("ABC123")

    assert runner.commands == [
        ["adb.exe", "-s", "ABC123", "shell", "input", "tap", "100", "200"],
        [
            "adb.exe", "-s", "ABC123", "shell", "input", "swipe",
            "10", "20", "30", "40", "650",
        ],
        [
            "adb.exe", "-s", "ABC123", "shell", "input", "keyevent", "BACK"
        ],
    ]
