"""ADB discovery, device selection, and screen capture."""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Final

from pokemon_go_cleanup.adb_runner import AdbRunner, SubprocessAdbRunner
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import (
    AdbCommandError,
    AdbNotInstalledError,
    DeviceNotFoundError,
    DeviceProtocolError,
    DeviceUnavailableError,
    MultipleConnectedDevicesError,
    NoConnectedDeviceError,
    ScreenshotCaptureError,
    UnauthorizedDeviceError,
)
from pokemon_go_cleanup.models import Device, DeviceInfo, ScreenResolution

logger = logging.getLogger(__name__)

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_SIZE_PATTERN: Final = re.compile(r"(\d+)\s*x\s*(\d+)")


def discover_adb(configured_path: Path | None = None) -> Path:
    """Return a usable ADB executable path or raise a clear error."""

    if configured_path is not None:
        candidate = configured_path.expanduser()
        if candidate.is_dir():
            candidate = candidate / ("adb.exe" if os.name == "nt" else "adb")
        if candidate.is_file():
            return candidate
        discovered_configured_path = shutil.which(str(candidate))
        if discovered_configured_path:
            return Path(discovered_configured_path)
        raise AdbNotInstalledError

    discovered_path = shutil.which("adb")
    if discovered_path is None:
        raise AdbNotInstalledError
    return Path(discovered_path)


def parse_device_list(output: str) -> list[Device]:
    """Parse the stable, line-oriented output from ``adb devices -l``."""

    devices: list[Device] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached"):
            continue

        columns = line.split()
        if len(columns) < 2:
            continue

        serial_number, state, *details = columns
        properties: dict[str, str] = {}
        for detail in details:
            key, separator, value = detail.partition(":")
            if separator:
                properties[key] = value
        devices.append(
            Device(
                serial_number=serial_number,
                state=state,
                properties=properties,
            )
        )
    return devices


def select_device(devices: list[Device], serial_number: str | None = None) -> Device:
    """Select exactly one usable device and explain ambiguous states."""

    if serial_number is not None:
        selected = next(
            (device for device in devices if device.serial_number == serial_number),
            None,
        )
        if selected is None:
            raise DeviceNotFoundError(serial_number)
        if selected.state == "unauthorized":
            raise UnauthorizedDeviceError([selected.serial_number])
        if not selected.is_ready:
            raise DeviceUnavailableError(selected.serial_number, selected.state)
        return selected

    ready_devices = [device for device in devices if device.is_ready]
    if len(ready_devices) > 1:
        raise MultipleConnectedDevicesError(
            [device.serial_number for device in ready_devices]
        )
    if len(ready_devices) == 1:
        return ready_devices[0]

    unauthorized_devices = [
        device.serial_number for device in devices if device.state == "unauthorized"
    ]
    if unauthorized_devices:
        raise UnauthorizedDeviceError(unauthorized_devices)
    raise NoConnectedDeviceError


class AdbClient:
    """Typed subprocess boundary for the small ADB surface this project uses."""

    def __init__(
        self,
        adb_path: Path,
        timeout_seconds: float = 15.0,
        runner: AdbRunner | None = None,
    ) -> None:
        self._adb_path = adb_path
        self._timeout_seconds = timeout_seconds
        self._runner = runner if runner is not None else SubprocessAdbRunner()

    @classmethod
    def from_config(cls, config: AppConfig) -> AdbClient:
        """Create a client after discovering the configured ADB executable."""

        return cls(
            adb_path=discover_adb(config.adb_path),
            timeout_seconds=config.adb_timeout_seconds,
        )

    def list_devices(self) -> list[Device]:
        """Return every device state reported by the ADB server."""

        result = self._run_text(["devices", "-l"])
        devices = parse_device_list(result)
        logger.info("device_listed", extra={"device_count": len(devices)})
        return devices

    def resolve_device(self, serial_number: str | None = None) -> Device:
        """Resolve a requested serial or the only ready device."""

        device = select_device(self.list_devices(), serial_number)
        logger.info("device_selected", extra={"serial_number": device.serial_number})
        return device

    def get_resolution(self, serial_number: str) -> ScreenResolution:
        """Read the current logical screen size using ``wm size``."""

        output = self._run_text(["-s", serial_number, "shell", "wm", "size"])
        matches = _SIZE_PATTERN.findall(output)
        if not matches:
            raise DeviceProtocolError(
                f"ADB returned an unrecognized screen resolution for '{serial_number}': "
                f"{output.strip() or '<empty output>'}"
            )
        width, height = matches[-1]
        return ScreenResolution(width=int(width), height=int(height))

    def get_device_info(self, serial_number: str | None = None) -> DeviceInfo:
        """Return ADB identity fields plus the current resolution."""

        device = self.resolve_device(serial_number)
        return DeviceInfo(
            serial_number=device.serial_number,
            state=device.state,
            model=device.properties.get("model"),
            product=device.properties.get("product"),
            device=device.properties.get("device"),
            transport_id=device.properties.get("transport_id"),
            resolution=self.get_resolution(device.serial_number),
        )

    def capture_screen(self, serial_number: str) -> bytes:
        """Return a PNG of the current display from ``adb exec-out screencap``."""

        try:
            png_bytes = self._run_bytes(
                ["-s", serial_number, "exec-out", "screencap", "-p"]
            )
        except AdbCommandError as error:
            raise ScreenshotCaptureError(
                f"Screenshot capture failed for '{serial_number}': {error}"
            ) from error

        if not png_bytes:
            raise ScreenshotCaptureError(
                f"Screenshot capture failed for '{serial_number}': ADB returned no data."
            )
        if not png_bytes.startswith(_PNG_SIGNATURE):
            raise ScreenshotCaptureError(
                f"Screenshot capture failed for '{serial_number}': "
                "ADB did not return a valid PNG."
            )
        return png_bytes

    def _command(self, arguments: list[str]) -> list[str]:
        return [str(self._adb_path), *arguments]

    def _run_text(self, arguments: list[str]) -> str:
        command = self._command(arguments)
        return self._runner.run_text(command, timeout_seconds=self._timeout_seconds)

    def _run_bytes(self, arguments: list[str]) -> bytes:
        command = self._command(arguments)
        return self._runner.run_bytes(command, timeout_seconds=self._timeout_seconds)
