"""CLI tests for the bilingual guided scan command."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
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
    assert "scan-auto-one" in result.stdout
    assert "scan-batch" in result.stdout


def test_scan_batch_help_exposes_opt_in_rename() -> None:
    result = runner.invoke(cli.app, ["scan-batch", "--help"])

    assert result.exit_code == 0
    assert "--rename-with-iv" in result.stdout


def test_scan_one_requires_guided_flag() -> None:
    result = runner.invoke(
        cli.app,
        ["scan-one"],
        env={"COLUMNS": "20", "NO_COLOR": "1"},
    )

    assert result.exit_code == 2
    assert "--guided" in result.output
    assert "Error: scan-one requires --guided." in result.output


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
    manifest_paths = list((output_directory / "scans").glob("*/*/manifest.json"))
    assert len(manifest_paths) == 1
    manifest: dict[str, Any] = json.loads(manifest_paths[0].read_text(encoding="utf-8"))
    assert manifest["notes"] == "社区日保留"
    assert manifest["scan_status"] == "complete"


@pytest.mark.parametrize("outcome,exit_code", [("success", 0), ("failure", 15), ("stop", 130)])
def test_rename_iv_cli_routes_to_no_file_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, exit_code: int,
) -> None:
    from pokemon_go_cleanup.exceptions import AutomationError

    selected: list[str | None] = []

    class NamingService:
        def __init__(self, *args: object) -> None:
            pass

        def rename_iv_one(self, *, serial_number: str | None = None) -> SimpleNamespace:
            selected.append(serial_number)
            if outcome == "failure":
                raise AutomationError("Naming failed")
            if outcome == "stop":
                raise KeyboardInterrupt
            return SimpleNamespace(iv_suffix="⑮⑬⑪")

    monkeypatch.setattr(cli, "_build_client", lambda _: object())
    monkeypatch.setattr(cli, "RecognitionService", lambda: object())
    monkeypatch.setattr(cli, "HuaweiMate30PageDetector", lambda _: object())
    monkeypatch.setattr(cli, "AutoScanService", NamingService)

    result = runner.invoke(
        cli.app, ["--data-dir", str(tmp_path), "rename-iv-one", "--serial", "ABC123"]
    )

    assert result.exit_code == exit_code, result.output
    assert selected == ["ABC123"]
    if outcome == "success":
        assert "命名完成：已追加圈号 IV ⑮⑬⑪" in result.output
    assert "recognition.json" not in result.output
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("outcome,exit_code", [("success", 0), ("failure", 16), ("stop", 130)])
def test_rename_iv_batch_cli_reports_live_progress_without_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    exit_code: int,
) -> None:
    from pokemon_go_cleanup.exceptions import BatchAutomationError

    selected: list[tuple[int, float, str | None]] = []

    class NamingService:
        def __init__(self, *args: object) -> None:
            pass

        def rename(
            self,
            *,
            limit: int,
            delay_seconds: float,
            serial_number: str | None = None,
            on_success: object = None,
        ) -> SimpleNamespace:
            selected.append((limit, delay_seconds, serial_number))
            if outcome == "failure":
                raise BatchAutomationError(
                    "IV-only batch failed while processing item 2; "
                    "1 Pokemon were completed"
                )
            if outcome == "stop":
                raise KeyboardInterrupt
            assert callable(on_success)
            on_success(1, limit, SimpleNamespace(iv_suffix="⑮⑭⑬"))
            on_success(2, limit, SimpleNamespace(iv_suffix="⑮⑭⑬"))
            return SimpleNamespace(completed_count=2, stop_reason="limit_reached")

    monkeypatch.setattr(cli, "_build_client", lambda _: object())
    monkeypatch.setattr(cli, "RecognitionService", lambda: object())
    monkeypatch.setattr(cli, "HuaweiMate30PageDetector", lambda _: object())
    monkeypatch.setattr(cli, "AutoScanService", lambda *args: object())
    monkeypatch.setattr(cli, "IvOnlyBatchRenameService", NamingService)

    result = runner.invoke(
        cli.app,
        [
            "--data-dir",
            str(tmp_path),
            "rename-iv-batch",
            "--limit",
            "2",
            "--delay",
            "0.5",
            "--serial",
            "ABC123",
        ],
    )

    assert result.exit_code == exit_code, result.output
    assert selected == [(2, 0.5, "ABC123")]
    if outcome == "success":
        assert "IV_ONLY_PROGRESS 1/2 ⑮⑭⑬" in result.output
        assert "IV_ONLY_PROGRESS 2/2 ⑮⑭⑬" in result.output
        assert "成功 2 只" in result.output
    if outcome == "failure":
        assert "1 Pokemon were completed" in result.output
    assert list(tmp_path.iterdir()) == []
