"""Recursive validation for local guided-scan datasets."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Final

from PIL import Image
from pydantic import ValidationError

from pokemon_go_cleanup.exceptions import DatasetError
from pokemon_go_cleanup.models import (
    DatasetScanStatus,
    GroundTruth,
    ScanManifest,
    ScanStep,
    ScanValidationResult,
)
from pokemon_go_cleanup.scan import SCAN_STEPS

logger = logging.getLogger(__name__)

MANIFEST_FILENAME: Final = "manifest.json"
ANNOTATION_FILENAME: Final = "ground_truth.json"
REQUIRED_FILENAMES: Final[frozenset[str]] = frozenset(
    {MANIFEST_FILENAME, *(f"{step}.png" for step in SCAN_STEPS)}
)
KNOWN_FILENAMES: Final[frozenset[str]] = REQUIRED_FILENAMES | {
    ANNOTATION_FILENAME
}
_CAPTURE_DATE_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def discover_scan_directories(dataset_root: Path) -> tuple[Path, ...]:
    """Find scan directories below a dataset root without assuming one depth."""

    root = dataset_root.expanduser().resolve()
    if not root.exists():
        raise DatasetError(f"Dataset root does not exist: '{root}'.")
    if not root.is_dir():
        raise DatasetError(f"Dataset root is not a directory: '{root}'.")

    discovered: set[Path] = set()
    try:
        if _looks_like_scan_directory(root):
            discovered.add(root)
        for candidate in root.rglob("*"):
            if candidate.is_file() and candidate.name in KNOWN_FILENAMES:
                discovered.add(candidate.parent.resolve())
            elif (
                candidate.is_dir()
                and _CAPTURE_DATE_PATTERN.fullmatch(candidate.parent.name)
            ):
                discovered.add(candidate.resolve())
    except OSError as error:
        raise DatasetError(f"Could not traverse dataset root '{root}': {error}") from error

    return tuple(sorted(discovered, key=lambda path: str(path).casefold()))


def _looks_like_scan_directory(path: Path) -> bool:
    if _CAPTURE_DATE_PATTERN.fullmatch(path.parent.name):
        return True
    return any((path / filename).exists() for filename in KNOWN_FILENAMES)


def _capture_date_from_parent(scan_directory: Path) -> date | None:
    if not _CAPTURE_DATE_PATTERN.fullmatch(scan_directory.parent.name):
        return None
    try:
        return date.fromisoformat(scan_directory.parent.name)
    except ValueError:
        return None


def _decode_png_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError("file format is not PNG")
            image.load()
            return image.size
    except (OSError, ValueError) as error:
        raise ValueError(f"{path.name} cannot be decoded as PNG: {error}") from error


def _load_manifest(path: Path) -> ScanManifest:
    try:
        return ScanManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"manifest.json cannot be read: {error}") from error
    except ValidationError as error:
        raise ValueError(f"manifest.json is invalid: {error}") from error


def _load_annotation(path: Path) -> GroundTruth:
    try:
        return GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"ground_truth.json cannot be read: {error}") from error
    except ValidationError as error:
        raise ValueError(f"ground_truth.json is invalid: {error}") from error


def validate_scan_directory(scan_directory: Path) -> ScanValidationResult:
    """Validate required artifacts, typed JSON, image decoding, and dimensions."""

    directory = scan_directory.expanduser().resolve()
    if not directory.is_dir():
        raise DatasetError(f"Scan directory does not exist: '{directory}'.")

    issues: list[str] = []
    invalid = False
    incomplete = False

    screenshot_files_present: dict[ScanStep, bool] = {
        step: (directory / f"{step}.png").is_file() for step in SCAN_STEPS
    }
    missing_screenshots = [
        f"{step}.png"
        for step, present in screenshot_files_present.items()
        if not present
    ]
    if missing_screenshots:
        incomplete = True
        issues.append(f"missing screenshots: {', '.join(missing_screenshots)}")

    manifest_path = directory / MANIFEST_FILENAME
    manifest: ScanManifest | None = None
    manifest_valid = False
    if not manifest_path.is_file():
        incomplete = True
        issues.append("missing manifest.json")
    else:
        try:
            manifest = _load_manifest(manifest_path)
            manifest_valid = True
        except ValueError as error:
            invalid = True
            issues.append(str(error))

    dimensions: dict[ScanStep, tuple[int, int]] = {}
    for step, present in screenshot_files_present.items():
        if not present:
            continue
        try:
            dimensions[step] = _decode_png_dimensions(directory / f"{step}.png")
        except ValueError as error:
            invalid = True
            issues.append(str(error))

    if len(dimensions) == len(SCAN_STEPS) and len(set(dimensions.values())) != 1:
        invalid = True
        rendered_dimensions = ", ".join(
            f"{step}={width}x{height}"
            for step, (width, height) in dimensions.items()
        )
        issues.append(f"screenshot dimensions differ: {rendered_dimensions}")

    if manifest is not None and manifest.scan_status != "complete":
        incomplete = True
        issues.append(f"manifest scan_status is {manifest.scan_status}")

    annotation_path = directory / ANNOTATION_FILENAME
    annotation_present = annotation_path.is_file()
    annotation_valid: bool | None = None
    if annotation_present:
        try:
            _load_annotation(annotation_path)
            annotation_valid = True
        except ValueError as error:
            annotation_valid = False
            invalid = True
            issues.append(str(error))

    overall_status: DatasetScanStatus
    if invalid:
        overall_status = "invalid"
    elif incomplete:
        overall_status = "incomplete"
    else:
        overall_status = "complete"

    scan_id = manifest.scan_id if manifest is not None else directory.name
    capture_date = (
        manifest.started_at.date()
        if manifest is not None
        else _capture_date_from_parent(directory)
    )
    result = ScanValidationResult(
        scan_directory=directory,
        scan_id=scan_id,
        capture_date=capture_date,
        screenshot_files_present=screenshot_files_present,
        manifest_valid=manifest_valid,
        annotation_present=annotation_present,
        annotation_valid=annotation_valid,
        overall_status=overall_status,
        issues=tuple(issues),
    )
    logger.info(
        "scan_validated",
        extra={
            "scan_id": result.scan_id,
            "scan_directory": str(result.scan_directory),
            "overall_status": result.overall_status,
        },
    )
    return result


def validate_dataset(dataset_root: Path) -> tuple[ScanValidationResult, ...]:
    """Recursively validate every discovered scan directory."""

    return tuple(
        validate_scan_directory(scan_directory)
        for scan_directory in discover_scan_directories(dataset_root)
    )
