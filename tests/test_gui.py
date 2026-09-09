"""Focused tests for GUI progress helpers without opening a Tk window."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pokemon_go_cleanup.gui import (
    PokemonGoCleanupGui,
    count_csv_data_rows,
    format_elapsed_time,
    parse_iv_only_progress,
)


class FakeStringVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def test_count_csv_data_rows_ignores_header_and_handles_quoted_newline(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "inventory.csv"
    destination.write_text(
        'batch_index,pokemon_name,warnings\n1,古月鳥,"first line\nsecond line"\n2,咩利羊,\n',
        encoding="utf-8",
    )

    assert count_csv_data_rows(destination) == 2


def test_count_csv_data_rows_handles_missing_and_header_only_files(tmp_path: Path) -> None:
    destination = tmp_path / "inventory.csv"

    assert count_csv_data_rows(destination) is None
    destination.write_text("batch_index,pokemon_name\n", encoding="utf-8")
    assert count_csv_data_rows(destination) == 0


def test_format_elapsed_time_uses_hours_minutes_and_seconds() -> None:
    assert format_elapsed_time(-1) == "00:00:00"
    assert format_elapsed_time(0.9) == "00:00:00"
    assert format_elapsed_time(65.8) == "00:01:05"
    assert format_elapsed_time(3661) == "01:01:01"


def test_running_scan_timer_refreshes_from_monotonic_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui = object.__new__(PokemonGoCleanupGui)
    elapsed = FakeStringVar("00:00:00")
    gui._scan_started_at = 100.0
    gui._elapsed_time_var = elapsed  # type: ignore[assignment]
    monkeypatch.setattr("pokemon_go_cleanup.gui.time.monotonic", lambda: 3761.9)

    gui._refresh_scan_elapsed_time()

    assert elapsed.value == "01:01:01"

    gui._scan_started_at = None
    elapsed.value = "stopped"
    gui._refresh_scan_elapsed_time()
    assert elapsed.value == "stopped"


def test_iv_naming_button_ignores_all_batch_and_save_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui = object.__new__(PokemonGoCleanupGui)
    calls: list[tuple[list[str], str, Path | None]] = []

    def start(
        command: list[str], *, task: str, heading: str, progress_csv: Path | None = None,
    ) -> None:
        calls.append((command, task, progress_csv))

    # No CSV/debug/resume/rename variables exist: this button must never read them.
    monkeypatch.setattr(gui, "_start_command", start)
    gui._rename_iv_one()

    assert calls == [(
        [sys.executable, "-m", "pokemon_go_cleanup", "rename-iv-one"],
        "rename-iv-one",
        None,
    )]


@pytest.mark.parametrize("exit_code,expected_count", [(0, "1"), (15, "0"), (130, "0")])
def test_iv_naming_completion_counts_only_verified_success_and_freezes_timer(
    monkeypatch: pytest.MonkeyPatch, exit_code: int, expected_count: str,
) -> None:
    gui = object.__new__(PokemonGoCleanupGui)
    gui._current_task = "rename-iv-one"
    gui._batch_progress_csv = None
    gui._scan_started_at = 100.0
    elapsed = FakeStringVar("00:00:00")
    count = FakeStringVar("0")
    gui._elapsed_time_var = elapsed  # type: ignore[assignment]
    gui._successful_scans_var = count  # type: ignore[assignment]
    gui._status_var = FakeStringVar("")  # type: ignore[assignment]
    monkeypatch.setattr(gui, "_set_running", lambda _: None)
    monkeypatch.setattr(gui, "_append_log", lambda _: None)
    monkeypatch.setattr("pokemon_go_cleanup.gui.time.monotonic", lambda: 165.0)

    gui._handle_process_done(exit_code)

    assert count.value == expected_count
    assert elapsed.value == "00:01:05"
    assert gui._scan_started_at is None


@pytest.mark.parametrize(
    "line,expected",
    [
        ("IV_ONLY_PROGRESS 3/10 超梦⑭⑭⑮", 3),
        ("ordinary log line", None),
        ("IV_ONLY_PROGRESS bad", None),
    ],
)
def test_parse_iv_only_progress(line: str, expected: int | None) -> None:
    assert parse_iv_only_progress(line) == expected


def test_iv_batch_button_reuses_limit_and_delay_without_csv_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui = object.__new__(PokemonGoCleanupGui)
    gui._limit_var = FakeStringVar("7")  # type: ignore[assignment]
    gui._delay_var = FakeStringVar("0.5")  # type: ignore[assignment]
    calls: list[tuple[list[str], str, Path | None]] = []

    def start(
        command: list[str],
        *,
        task: str,
        heading: str,
        progress_csv: Path | None = None,
    ) -> None:
        calls.append((command, task, progress_csv))

    monkeypatch.setattr(gui, "_start_command", start)
    gui._rename_iv_batch()

    assert calls == [
        (
            [
                sys.executable,
                "-m",
                "pokemon_go_cleanup",
                "rename-iv-batch",
                "--limit",
                "7",
                "--delay",
                "0.5",
            ],
            "rename-iv-batch",
            None,
        )
    ]


def test_iv_batch_progress_is_kept_when_process_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui = object.__new__(PokemonGoCleanupGui)
    gui._current_task = "rename-iv-batch"
    gui._batch_progress_csv = None
    gui._scan_started_at = 100.0
    gui._process = object()  # type: ignore[assignment]
    gui._worker = object()  # type: ignore[assignment]
    gui._successful_scans_var = FakeStringVar("0")  # type: ignore[assignment]
    gui._elapsed_time_var = FakeStringVar("00:00:00")  # type: ignore[assignment]
    gui._status_var = FakeStringVar("")  # type: ignore[assignment]
    logs: list[str] = []
    monkeypatch.setattr(gui, "_append_log", logs.append)
    monkeypatch.setattr(gui, "_set_running", lambda _: None)
    monkeypatch.setattr("pokemon_go_cleanup.gui.time.monotonic", lambda: 165.0)

    gui._handle_process_line("IV_ONLY_PROGRESS 3/10 甲⑮⑭⑬")
    gui._handle_process_done(16)

    assert gui._successful_scans_var.value == "3"  # type: ignore[attr-defined]
    assert gui._elapsed_time_var.value == "00:01:05"  # type: ignore[attr-defined]
    assert gui._status_var.value == "失败（退出码 16）"  # type: ignore[attr-defined]
