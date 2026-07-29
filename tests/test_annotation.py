"""Tests for typed manual ground-truth annotation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError
from typer.testing import CliRunner

from pokemon_go_cleanup import cli
from pokemon_go_cleanup.annotation import AnnotationService
from pokemon_go_cleanup.exceptions import AnnotationExistsError, LocalStorageError
from pokemon_go_cleanup.models import GroundTruth
from pokemon_go_cleanup.scan import SCAN_STEPS

runner = CliRunner()


def _create_scan_directory(tmp_path: Path) -> Path:
    scan_directory = tmp_path / "扫描数据" / "2026-07-28" / "scan-annotate"
    scan_directory.mkdir(parents=True)
    for index, step in enumerate(SCAN_STEPS):
        image = Image.new("RGB", (20, 30), color=(index * 20, 10, 30))
        try:
            image.save(scan_directory / f"{step}.png", format="PNG")
        finally:
            image.close()
    return scan_directory


def _annotation(**overrides: object) -> GroundTruth:
    values: dict[str, Any] = {
        "pokemon_name": "皮卡丘",
        "cp": 1234,
        "hp_current": 98,
        "hp_max": 100,
        "weight_kg": 6.0,
        "height_m": 0.4,
        "types": ["电"],
        "fast_move": "电光一闪",
        "charged_move_1": "疯狂伏特",
        "charged_move_2": None,
        "attack_iv": 15,
        "defense_iv": 14,
        "hp_iv": 13,
        "favorite": True,
        "shiny": False,
        "shadow": False,
        "purified": False,
        "costume": False,
        "notes": "中文备注",
    }
    values.update(overrides)
    return GroundTruth.model_validate(values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"attack_iv": 16},
        {"defense_iv": -1},
        {"hp_iv": "15"},
        {"cp": 0},
        {"weight_kg": -0.1},
        {"weight_kg": "6.0"},
        {"height_m": "0.4"},
        {"hp_current": 101, "hp_max": 100},
    ],
)
def test_ground_truth_rejects_invalid_numeric_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _annotation(**overrides)


def test_annotation_service_writes_readable_utf8_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    scan_directory = _create_scan_directory(tmp_path)
    annotation = _annotation()

    saved_path = AnnotationService.save(scan_directory, annotation)

    text = saved_path.read_text(encoding="utf-8")
    assert "皮卡丘" in text
    assert "中文备注" in text
    assert GroundTruth.model_validate_json(text) == annotation
    assert not list(scan_directory.glob("*.tmp"))

    with pytest.raises(AnnotationExistsError):
        AnnotationService.save(scan_directory, annotation)


def test_annotation_atomic_failure_cleans_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scan_directory = _create_scan_directory(tmp_path)

    def fail_replace(self: Path, target: object) -> Path:
        raise OSError(f"cannot replace {target}")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(LocalStorageError, match="atomically save"):
        AnnotationService.save(scan_directory, _annotation())

    assert not (scan_directory / "ground_truth.json").exists()
    assert not list(scan_directory.glob("*.tmp"))


def test_annotate_non_interactive_json_and_force(tmp_path: Path) -> None:
    scan_directory = _create_scan_directory(tmp_path)
    input_path = tmp_path / "输入标注.json"
    input_path.write_text(
        _annotation(notes="第一次").model_dump_json(indent=2),
        encoding="utf-8",
    )

    first = runner.invoke(
        cli.app,
        ["annotate", str(scan_directory), "--input-json", str(input_path)],
    )

    assert first.exit_code == 0
    assert str((scan_directory / "summary.png").resolve()) in first.output
    assert "Saved annotation:" in first.output
    saved_path = scan_directory / "ground_truth.json"
    assert GroundTruth.model_validate_json(
        saved_path.read_text(encoding="utf-8")
    ).notes == "第一次"

    refused = runner.invoke(
        cli.app,
        ["annotate", str(scan_directory), "--input-json", str(input_path)],
    )
    assert refused.exit_code == 13
    assert "--force" in refused.output

    input_path.write_text(
        _annotation(notes="强制更新").model_dump_json(indent=2),
        encoding="utf-8",
    )
    forced = runner.invoke(
        cli.app,
        [
            "annotate",
            str(scan_directory),
            "--input-json",
            str(input_path),
            "--force",
        ],
    )
    assert forced.exit_code == 0
    assert GroundTruth.model_validate_json(
        saved_path.read_text(encoding="utf-8")
    ).notes == "强制更新"


def test_interactive_enter_keeps_every_existing_value(tmp_path: Path) -> None:
    scan_directory = _create_scan_directory(tmp_path)
    existing = _annotation()
    AnnotationService.save(scan_directory, existing)

    result = runner.invoke(
        cli.app,
        ["annotate", str(scan_directory)],
        input="y\n" + "\n" * 19,
    )

    assert result.exit_code == 0
    reloaded = GroundTruth.model_validate_json(
        (scan_directory / "ground_truth.json").read_text(encoding="utf-8")
    )
    assert reloaded == existing
