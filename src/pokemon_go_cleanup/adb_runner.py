"""Injectable command runners for the ADB subprocess boundary."""

from __future__ import annotations

import subprocess
from typing import Protocol

from pokemon_go_cleanup.exceptions import AdbCommandError


class AdbRunner(Protocol):
    """Execute complete ADB commands as text or raw bytes."""

    def run_text(self, command: list[str], *, timeout_seconds: float) -> str: ...

    def run_bytes(self, command: list[str], *, timeout_seconds: float) -> bytes: ...


class SubprocessAdbRunner:
    """Run ADB commands through the local subprocess boundary."""

    def run_text(self, command: list[str], *, timeout_seconds: float) -> str:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                text=True,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AdbCommandError(f"Could not run ADB: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise AdbCommandError(
                f"ADB command failed with exit code {completed.returncode}: "
                f"{detail or '<no error output>'}"
            )
        return completed.stdout

    def run_bytes(self, command: list[str], *, timeout_seconds: float) -> bytes:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AdbCommandError(f"Could not run ADB: {error}") from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AdbCommandError(
                f"ADB command failed with exit code {completed.returncode}: "
                f"{stderr or '<no error output>'}"
            )
        return completed.stdout
