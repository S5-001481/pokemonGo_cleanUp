"""Guided, local-first multi-screenshot scan workflow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Final, NoReturn, Protocol
from uuid import uuid4

from pokemon_go_cleanup import __version__
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import (
    GuidedScanStepError,
    LocalStorageError,
    PokemonGoCleanupError,
)
from pokemon_go_cleanup.models import (
    Device,
    GuidedScanResult,
    ScanManifest,
    ScanStatus,
    ScanStep,
    ScreenResolution,
)
from pokemon_go_cleanup.storage import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

SCAN_STEPS: Final[tuple[ScanStep, ...]] = ("summary", "moves", "appraisal")


class ScanAdbGateway(Protocol):
    """ADB behavior required by the guided scan service."""

    def resolve_device(self, serial_number: str | None = None) -> Device: ...

    def get_resolution(self, serial_number: str) -> ScreenResolution: ...

    def capture_screen(self, serial_number: str) -> bytes: ...


def _current_local_time() -> datetime:
    return datetime.now().astimezone()


def _new_scan_token() -> str:
    return uuid4().hex


def _localize(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.astimezone()


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    atomic_write_bytes(path, content)


def _atomic_write_text(path: Path, content: str) -> None:
    atomic_write_text(path, content)


class GuidedScanService:
    """Capture the three user-prepared views for one Pokémon."""

    def __init__(
        self,
        config: AppConfig,
        adb: ScanAdbGateway,
        clock: Callable[[], datetime] = _current_local_time,
        token_factory: Callable[[], str] = _new_scan_token,
        application_version: str = __version__,
    ) -> None:
        self._config = config
        self._adb = adb
        self._clock = clock
        self._token_factory = token_factory
        self._application_version = application_version

    def scan_one(
        self,
        *,
        prepare_step: Callable[[ScanStep], None],
        serial_number: str | None = None,
        notes: str | None = None,
    ) -> GuidedScanResult:
        """Run the guided workflow and persist progress after every screenshot."""

        device = self._adb.resolve_device(serial_number)
        resolution = self._adb.get_resolution(device.serial_number)
        started_at = _localize(self._clock())
        scan_id = (
            f"{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{self._token_factory()}"
        )
        scan_directory = (
            self._config.scan_root / started_at.strftime("%Y-%m-%d") / scan_id
        )
        manifest_path = scan_directory / "manifest.json"

        try:
            scan_directory.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            raise LocalStorageError(
                f"Could not create scan directory '{scan_directory}': {error}"
            ) from error

        manifest = ScanManifest(
            scan_id=scan_id,
            started_at=started_at,
            device_serial=device.serial_number,
            device_model=device.model_name,
            screen_resolution=resolution,
            workflow_mode="guided",
            application_version=self._application_version,
            scan_status="in_progress",
            notes=notes,
        )
        self._write_manifest(manifest_path, manifest)

        for step in SCAN_STEPS:
            prepare_step(step)
            try:
                png_bytes = self._adb.capture_screen(device.serial_number)
                captured_at = _localize(self._clock())
                screenshot_path = scan_directory / f"{step}.png"
                _atomic_write_bytes(screenshot_path, png_bytes)

                capture_timestamps = dict(manifest.capture_timestamps)
                capture_timestamps[step] = captured_at
                screenshot_filenames = dict(manifest.screenshot_filenames)
                screenshot_filenames[step] = screenshot_path.name
                next_status: ScanStatus = (
                    "complete" if step == SCAN_STEPS[-1] else "in_progress"
                )
                manifest = manifest.model_copy(
                    update={
                        "capture_timestamps": capture_timestamps,
                        "screenshot_filenames": screenshot_filenames,
                        "scan_status": next_status,
                    }
                )
                self._write_manifest(manifest_path, manifest)
            except PokemonGoCleanupError as error:
                self._raise_incomplete(
                    manifest_path=manifest_path,
                    manifest=manifest,
                    failed_step=step,
                    cause=error,
                )

        absolute_scan_directory = scan_directory.resolve()
        absolute_manifest_path = manifest_path.resolve()
        logger.info(
            "guided_scan_completed",
            extra={
                "scan_id": manifest.scan_id,
                "serial_number": manifest.device_serial,
                "scan_directory": str(absolute_scan_directory),
                "manifest_path": str(absolute_manifest_path),
            },
        )
        return GuidedScanResult(
            scan_directory=absolute_scan_directory,
            manifest_path=absolute_manifest_path,
            manifest=manifest,
        )

    @staticmethod
    def _write_manifest(path: Path, manifest: ScanManifest) -> None:
        _atomic_write_text(path, manifest.model_dump_json(indent=2) + "\n")

    def _raise_incomplete(
        self,
        *,
        manifest_path: Path,
        manifest: ScanManifest,
        failed_step: ScanStep,
        cause: PokemonGoCleanupError,
    ) -> NoReturn:
        incomplete_manifest = manifest.model_copy(
            update={
                "scan_status": "incomplete",
                "failed_step": failed_step,
            }
        )
        try:
            self._write_manifest(manifest_path, incomplete_manifest)
        except LocalStorageError as manifest_error:
            raise GuidedScanStepError(
                failed_step=failed_step,
                manifest_path=manifest_path.resolve(),
                cause=cause,
                manifest_error=manifest_error,
            ) from cause

        logger.error(
            "guided_scan_incomplete",
            extra={
                "scan_id": manifest.scan_id,
                "failed_step": failed_step,
                "manifest_path": str(manifest_path.resolve()),
                "cause": str(cause),
            },
        )
        raise GuidedScanStepError(
            failed_step=failed_step,
            manifest_path=manifest_path.resolve(),
            cause=cause,
        ) from cause
