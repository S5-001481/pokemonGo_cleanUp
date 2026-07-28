"""Pydantic models used by the CLI and metadata files."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

ScanStep = Literal["summary", "moves", "appraisal"]
ScanStatus = Literal["in_progress", "complete", "incomplete"]


class Device(BaseModel):
    """One row returned by ``adb devices -l``."""

    model_config = ConfigDict(frozen=True)

    serial_number: str = Field(min_length=1)
    state: str = Field(min_length=1)
    properties: dict[str, str] = Field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        """Whether ADB can execute commands on this device."""

        return self.state == "device"

    @property
    def model_name(self) -> str | None:
        """Human-friendly hardware model, when ADB reports one."""

        return self.properties.get("model")


class ScreenResolution(BaseModel):
    """Current logical device resolution in pixels."""

    model_config = ConfigDict(frozen=True)

    width: PositiveInt
    height: PositiveInt

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


class DeviceInfo(BaseModel):
    """Selected device details shown by the CLI."""

    model_config = ConfigDict(frozen=True)

    serial_number: str
    state: str
    model: str | None = None
    product: str | None = None
    device: str | None = None
    transport_id: str | None = None
    resolution: ScreenResolution


class CaptureMetadata(BaseModel):
    """UTF-8 JSON sidecar stored next to a captured screenshot."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    captured_at: datetime
    serial_number: str
    resolution: ScreenResolution
    screenshot_path: Path
    metadata_path: Path
    file_size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CaptureResult(BaseModel):
    """Result returned after both screenshot and metadata are saved."""

    model_config = ConfigDict(frozen=True)

    screenshot_path: Path
    metadata_path: Path
    metadata: CaptureMetadata


class ScanManifest(BaseModel):
    """Progress and device metadata for one guided three-screenshot scan."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    scan_id: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    started_at: datetime
    capture_timestamps: dict[ScanStep, datetime] = Field(default_factory=dict)
    device_serial: str
    device_model: str | None = None
    screen_resolution: ScreenResolution
    screenshot_filenames: dict[ScanStep, str] = Field(default_factory=dict)
    workflow_mode: Literal["guided"] = "guided"
    application_version: str
    scan_status: ScanStatus
    failed_step: ScanStep | None = None
    notes: str | None = None


class GuidedScanResult(BaseModel):
    """Successful result returned by the guided scan service."""

    model_config = ConfigDict(frozen=True)

    scan_directory: Path
    manifest_path: Path
    manifest: ScanManifest
