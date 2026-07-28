"""Smoke tests for the public Typer command tree."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from pokemon_go_cleanup import cli
from pokemon_go_cleanup.exceptions import AdbNotInstalledError
from pokemon_go_cleanup.models import Device

runner = CliRunner()


class FakeClient:
    """Minimal client for the device list command."""

    def list_devices(self) -> list[Device]:
        return [
            Device(
                serial_number="ABC123",
                state="device",
                properties={"model": "HUAWEI_Mate_30"},
            )
        ]


def test_device_list_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_build_client", lambda _: FakeClient())

    result = runner.invoke(cli.app, ["device", "list"])

    assert result.exit_code == 0
    assert "ABC123" in result.stdout
    assert "HUAWEI_Mate_30" in result.stdout


def test_device_list_reports_adb_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_build_client(*args: Any, **kwargs: Any) -> FakeClient:
        raise AdbNotInstalledError

    monkeypatch.setattr(cli, "_build_client", fail_to_build_client)

    result = runner.invoke(cli.app, ["device", "list"])

    assert result.exit_code == AdbNotInstalledError.exit_code
    assert "ADB is not installed" in result.output


def test_root_help_includes_required_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "capture" in result.stdout
    assert "device" in result.stdout
