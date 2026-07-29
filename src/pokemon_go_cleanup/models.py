"""Pydantic models used by the CLI and metadata files."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    field_validator,
    model_validator,
)

ScanStep = Literal["summary", "moves", "appraisal"]
ScanStatus = Literal["in_progress", "complete", "incomplete"]
DatasetScanStatus = Literal["complete", "incomplete", "invalid"]


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

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    scan_id: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    started_at: datetime
    capture_timestamps: dict[ScanStep, datetime] = Field(default_factory=dict)
    device_serial: str
    device_model: str | None = None
    screen_resolution: ScreenResolution
    screenshot_filenames: dict[ScanStep, str] = Field(default_factory=dict)
    workflow_mode: Literal["guided", "automatic"] = "guided"
    application_version: str
    scan_status: ScanStatus
    failed_step: str | None = None
    notes: str | None = None


class GuidedScanResult(BaseModel):
    """Successful result returned by the guided scan service."""

    model_config = ConfigDict(frozen=True)

    scan_directory: Path
    manifest_path: Path
    manifest: ScanManifest


class GroundTruth(BaseModel):
    """Human-provided facts for one guided Pokémon scan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pokemon_name: str = Field(min_length=1)
    cp: int = Field(gt=0, strict=True)
    hp_current: int = Field(ge=0, strict=True)
    hp_max: int = Field(gt=0, strict=True)
    weight_kg: float = Field(gt=0, allow_inf_nan=False, strict=True)
    height_m: float = Field(gt=0, allow_inf_nan=False, strict=True)
    types: tuple[str, ...] = Field(min_length=1, max_length=2)
    fast_move: str = Field(min_length=1)
    charged_move_1: str = Field(min_length=1)
    charged_move_2: str | None = None
    attack_iv: int = Field(ge=0, le=15, strict=True)
    defense_iv: int = Field(ge=0, le=15, strict=True)
    hp_iv: int = Field(ge=0, le=15, strict=True)
    favorite: bool = Field(strict=True)
    shiny: bool = Field(strict=True)
    shadow: bool = Field(strict=True)
    purified: bool = Field(strict=True)
    costume: bool = Field(strict=True)
    notes: str | None = None

    @field_validator(
        "pokemon_name",
        "fast_move",
        "charged_move_1",
        mode="before",
    )
    @classmethod
    def _strip_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("charged_move_2", "notes", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("types")
    @classmethod
    def _normalize_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("types cannot contain an empty value")
        if len(set(normalized)) != len(normalized):
            raise ValueError("types must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def _validate_hp(self) -> GroundTruth:
        if self.hp_current > self.hp_max:
            raise ValueError("hp_current cannot be greater than hp_max")
        return self


class ScanValidationResult(BaseModel):
    """Validation outcome for one recursively discovered scan directory."""

    model_config = ConfigDict(frozen=True)

    scan_directory: Path
    scan_id: str
    capture_date: date | None
    screenshot_files_present: dict[ScanStep, bool]
    manifest_valid: bool
    annotation_present: bool
    annotation_valid: bool | None = None
    overall_status: DatasetScanStatus
    issues: tuple[str, ...] = ()

    @property
    def screenshot_count(self) -> int:
        """Number of required screenshots found on disk."""

        return sum(self.screenshot_files_present.values())
