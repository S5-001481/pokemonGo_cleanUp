"""CLI tests for dataset validation and status tables."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from pokemon_go_cleanup import cli
from pokemon_go_cleanup.models import GroundTruth, ScanManifest, ScreenResolution
from pokemon_go_cleanup.scan import SCAN_STEPS

runner = CliRunner()


def _write_scan(dataset_root: Path, scan_id: str) -> Path:
    scan_directory = dataset_root / "2026-07-28" / scan_id
    scan_directory.mkdir(parents=True)
    captured_at = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    for step in SCAN_STEPS:
        image = Image.new("RGB", (18, 24), color=(25, 50, 75))
        try:
            image.save(scan_directory / f"{step}.png", format="PNG")
        finally:
            image.close()
    manifest = ScanManifest(
        scan_id=scan_id,
        started_at=captured_at,
        capture_timestamps={step: captured_at for step in SCAN_STEPS},
        device_serial="SYNTHETIC",
        screen_resolution=ScreenResolution(width=18, height=24),
        screenshot_filenames={step: f"{step}.png" for step in SCAN_STEPS},
        workflow_mode="guided",
        application_version="0.1.0",
        scan_status="complete",
    )
    (scan_directory / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return scan_directory


def _write_annotation(scan_directory: Path) -> None:
    annotation = GroundTruth(
        pokemon_name="测试对象",
        cp=100,
        hp_current=20,
        hp_max=20,
        weight_kg=1.0,
        height_m=1.0,
        types=("测试",),
        fast_move="招式一",
        charged_move_1="招式二",
        attack_iv=1,
        defense_iv=2,
        hp_iv=3,
        favorite=False,
        shiny=False,
        shadow=False,
        purified=False,
        costume=False,
    )
    (scan_directory / "ground_truth.json").write_text(
        annotation.model_dump_json(indent=2),
        encoding="utf-8",
    )


def test_dataset_validate_reports_all_states_and_exits_for_invalid(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "custom dataset"
    complete = _write_scan(dataset_root, "scan-complete")
    _write_annotation(complete)
    incomplete = _write_scan(dataset_root, "scan-incomplete")
    (incomplete / "appraisal.png").unlink()
    invalid = _write_scan(dataset_root, "scan-invalid")
    (invalid / "moves.png").write_bytes(b"corrupt")

    result = runner.invoke(
        cli.app,
        ["dataset", "validate", "--output", str(dataset_root)],
    )

    assert result.exit_code == 1
    assert "SCAN ID" in result.output
    assert "scan-complete" in result.output
    assert "scan-incomplete" in result.output
    assert "scan-invalid" in result.output
    assert "Summary: complete=1, incomplete=1, invalid=1" in result.output


def test_dataset_status_has_required_columns_and_does_not_fail_for_invalid(
    tmp_path: Path,
) -> None:
    dataset_root = tmp_path / "custom dataset"
    scan_directory = _write_scan(dataset_root, "scan-status")
    _write_annotation(scan_directory)
    (scan_directory / "summary.png").write_bytes(b"corrupt")

    result = runner.invoke(
        cli.app,
        ["dataset", "status", "--output", str(dataset_root)],
    )

    assert result.exit_code == 0
    for heading in (
        "SCAN ID",
        "CAPTURE DATE",
        "SCREENSHOTS",
        "MANIFEST",
        "ANNOTATION",
        "STATUS",
    ):
        assert heading in result.output
    assert "scan-status" in result.output
    assert "yes" in result.output
    assert "invalid" in result.output
