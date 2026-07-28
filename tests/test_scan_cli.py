"""CLI tests for the bilingual guided scan command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pokemon_go_cleanup import cli
from pokemon_go_cleanup.models import Device, ScreenResolution

runner = CliRunner()


class FakeScanClient:
    """ADB surface needed by the guided scan command."""

    def __init__(self) -> None:
        self.selected_serial: str | None = None
        self.capture_count = 0

    def resolve_device(self, serial_number: str | None = None) -> Device:
        self.selected_serial = serial_number
        return Device(
            serial_number="ABC123",
            state="device",
            properties={"model": "HUAWEI_Mate_30"},
        )

    def get_resolution(self, serial_number: str) -> ScreenResolution:
        assert serial_number == "ABC123"
        return ScreenResolution(width=1080, height=2400)

    def capture_screen(self, serial_number: str) -> bytes:
        assert serial_number == "ABC123"
        self.capture_count += 1
        return b"\x89PNG\r\n\x1a\ncli-test" + str(self.capture_count).encode()


def test_root_help_includes_scan_one() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "scan-one" in result.stdout


def test_scan_one_requires_guided_flag() -> None:
    result = runner.invoke(cli.app, ["scan-one"])

    assert result.exit_code == 2
    assert "--guided" in result.output


def test_scan_one_guides_user_and_honors_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_client = FakeScanClient()
    monkeypatch.setattr(cli, "_build_client", lambda _: fake_client)
    output_directory = tmp_path / "扫描数据"

    result = runner.invoke(
        cli.app,
        [
            "scan-one",
            "--guided",
            "--serial",
            "ABC123",
            "--notes",
            "社区日保留",
            "--output",
            str(output_directory),
        ],
        input="\n\n\n",
    )

    assert result.exit_code == 0
    assert "请打开目标宝可梦的详情页" in result.stdout
    assert "Scroll down until all moves are clearly visible." in result.stdout
    assert "攻击、防御和 HP 个体值条" in result.stdout
    assert fake_client.selected_serial == "ABC123"
    assert fake_client.capture_count == 3
    manifest_paths = list(
        (output_directory / "scans").glob("*/*/manifest.json")
    )
    assert len(manifest_paths) == 1
    manifest: dict[str, Any] = json.loads(
        manifest_paths[0].read_text(encoding="utf-8")
    )
    assert manifest["notes"] == "社区日保留"
    assert manifest["scan_status"] == "complete"
