"""Tests for recursive local dataset validation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from pokemon_go_cleanup.dataset import (
    discover_scan_directories,
    validate_dataset,
    validate_scan_directory,
)
from pokemon_go_cleanup.exceptions import DatasetError
from pokemon_go_cleanup.models import ScanManifest, ScanStatus, ScreenResolution
from pokemon_go_cleanup.scan import SCAN_STEPS


def _write_png(path: Path, size: tuple[int, int] = (24, 32)) -> None:
    image = Image.new("RGB", size, color=(12, 34, 56))
    try:
        image.save(path, format="PNG")
    finally:
        image.close()


def _create_scan(
    dataset_root: Path,
    scan_id: str,
    *,
    status: ScanStatus = "complete",
    size: tuple[int, int] = (24, 32),
) -> Path:
    scan_directory = dataset_root / "nested" / "2026-07-28" / scan_id
    scan_directory.mkdir(parents=True)
    captured_at = datetime(2026, 7, 28, 20, 30, tzinfo=UTC)
    for step in SCAN_STEPS:
        _write_png(scan_directory / f"{step}.png", size)

    manifest = ScanManifest(
        scan_id=scan_id,
        started_at=captured_at,
        capture_timestamps={step: captured_at for step in SCAN_STEPS},
        device_serial="SYNTHETIC",
        device_model="Synthetic Device",
        screen_resolution=ScreenResolution(width=size[0], height=size[1]),
        screenshot_filenames={step: f"{step}.png" for step in SCAN_STEPS},
        workflow_mode="guided",
        application_version="0.1.0",
        scan_status=status,
        failed_step="moves" if status == "incomplete" else None,
    )
    (scan_directory / "manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return scan_directory


def test_recursively_discovers_and_validates_complete_scan(tmp_path: Path) -> None:
    dataset_root = tmp_path / "数据集"
    scan_directory = _create_scan(dataset_root, "scan-complete")

    assert discover_scan_directories(dataset_root) == (scan_directory.resolve(),)

    result = validate_dataset(dataset_root)[0]
    assert result.scan_id == "scan-complete"
    assert result.capture_date is not None
    assert result.capture_date.isoformat() == "2026-07-28"
    assert result.screenshot_count == 3
    assert result.manifest_valid is True
    assert result.annotation_present is False
    assert result.overall_status == "complete"
    assert result.issues == ()


def test_missing_screenshot_is_incomplete(tmp_path: Path) -> None:
    scan_directory = _create_scan(tmp_path / "scans", "scan-missing")
    (scan_directory / "moves.png").unlink()

    result = validate_scan_directory(scan_directory)

    assert result.screenshot_count == 2
    assert result.overall_status == "incomplete"
    assert "missing screenshots: moves.png" in result.issues


def test_incomplete_manifest_status_is_reported(tmp_path: Path) -> None:
    scan_directory = _create_scan(
        tmp_path / "scans",
        "scan-incomplete",
        status="incomplete",
    )

    result = validate_scan_directory(scan_directory)

    assert result.overall_status == "incomplete"
    assert "manifest scan_status is incomplete" in result.issues


def test_corrupt_png_is_invalid(tmp_path: Path) -> None:
    scan_directory = _create_scan(tmp_path / "scans", "scan-corrupt")
    (scan_directory / "summary.png").write_bytes(b"not a PNG")

    result = validate_scan_directory(scan_directory)

    assert result.overall_status == "invalid"
    assert any("summary.png cannot be decoded as PNG" in issue for issue in result.issues)


def test_different_png_dimensions_are_invalid(tmp_path: Path) -> None:
    scan_directory = _create_scan(tmp_path / "scans", "scan-dimensions")
    _write_png(scan_directory / "moves.png", size=(12, 18))

    result = validate_scan_directory(scan_directory)

    assert result.overall_status == "invalid"
    assert any("screenshot dimensions differ" in issue for issue in result.issues)


def test_invalid_typed_manifest_is_invalid(tmp_path: Path) -> None:
    scan_directory = _create_scan(tmp_path / "scans", "scan-manifest")
    (scan_directory / "manifest.json").write_text(
        '{"scan_id": "scan-manifest", "unexpected": true}',
        encoding="utf-8",
    )

    result = validate_scan_directory(scan_directory)

    assert result.manifest_valid is False
    assert result.overall_status == "invalid"
    assert any("manifest.json is invalid" in issue for issue in result.issues)


def test_invalid_existing_annotation_is_invalid(tmp_path: Path) -> None:
    scan_directory = _create_scan(tmp_path / "scans", "scan-annotation")
    (scan_directory / "ground_truth.json").write_text(
        '{"pokemon_name": "测试", "attack_iv": 99}',
        encoding="utf-8",
    )

    result = validate_scan_directory(scan_directory)

    assert result.annotation_present is True
    assert result.annotation_valid is False
    assert result.overall_status == "invalid"


def test_missing_dataset_root_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="does not exist"):
        validate_dataset(tmp_path / "missing")
