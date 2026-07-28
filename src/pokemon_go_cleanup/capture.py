"""Persist screenshots and their local metadata sidecars."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import uuid4

from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import LocalStorageError
from pokemon_go_cleanup.models import (
    CaptureMetadata,
    CaptureResult,
    Device,
    ScreenResolution,
)

logger = logging.getLogger(__name__)


class AdbGateway(Protocol):
    """ADB behavior required by the capture service."""

    def resolve_device(self, serial_number: str | None = None) -> Device: ...

    def get_resolution(self, serial_number: str) -> ScreenResolution: ...

    def capture_screen(self, serial_number: str) -> bytes: ...


def _current_local_time() -> datetime:
    return datetime.now().astimezone()


def _safe_filename_component(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", value).strip(" .")
    return sanitized or "device"


class CaptureService:
    """Coordinate device selection, ADB capture, and local persistence."""

    def __init__(
        self,
        config: AppConfig,
        adb: AdbGateway,
        clock: Callable[[], datetime] = _current_local_time,
    ) -> None:
        self._config = config
        self._adb = adb
        self._clock = clock

    def capture(self, serial_number: str | None = None) -> CaptureResult:
        """Capture one display and save a PNG plus a UTF-8 JSON sidecar."""

        device = self._adb.resolve_device(serial_number)
        resolution = self._adb.get_resolution(device.serial_number)
        png_bytes = self._adb.capture_screen(device.serial_number)
        captured_at = self._clock()
        if captured_at.tzinfo is None:
            captured_at = captured_at.astimezone()

        day_directory = self._config.screenshot_root / captured_at.strftime("%Y-%m-%d")
        unique_suffix = uuid4().hex[:8]
        timestamp = captured_at.strftime("%Y%m%d_%H%M%S_%f")
        safe_serial = _safe_filename_component(device.serial_number)
        screenshot_path = day_directory / f"{timestamp}_{safe_serial}_{unique_suffix}.png"
        metadata_path = screenshot_path.with_suffix(".json")

        absolute_screenshot_path = screenshot_path.resolve()
        absolute_metadata_path = metadata_path.resolve()
        metadata = CaptureMetadata(
            captured_at=captured_at,
            serial_number=device.serial_number,
            resolution=resolution,
            screenshot_path=absolute_screenshot_path,
            metadata_path=absolute_metadata_path,
            file_size_bytes=len(png_bytes),
            sha256=hashlib.sha256(png_bytes).hexdigest(),
        )

        try:
            day_directory.mkdir(parents=True, exist_ok=True)
            screenshot_path.write_bytes(png_bytes)
            metadata_path.write_text(
                metadata.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except OSError as error:
            raise LocalStorageError(
                f"Could not save capture under '{day_directory}': {error}"
            ) from error

        logger.info(
            "screenshot_captured",
            extra={
                "serial_number": device.serial_number,
                "resolution": str(resolution),
                "screenshot_path": str(absolute_screenshot_path),
                "metadata_path": str(absolute_metadata_path),
                "file_size_bytes": len(png_bytes),
            },
        )
        return CaptureResult(
            screenshot_path=absolute_screenshot_path,
            metadata_path=absolute_metadata_path,
            metadata=metadata,
        )
