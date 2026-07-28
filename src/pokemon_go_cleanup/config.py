"""Validated runtime configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    """CLI settings from flags or ``POKEMON_GO_CLEANUP_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="POKEMON_GO_CLEANUP_",
        extra="ignore",
        frozen=True,
    )

    data_dir: Path = Path("data")
    adb_path: Path | None = None
    adb_timeout_seconds: float = Field(default=15.0, gt=0, le=120)

    @property
    def screenshot_root(self) -> Path:
        """Base directory for dated screenshot folders."""

        return self.data_dir / "screenshots"

    @property
    def scan_root(self) -> Path:
        """Base directory for dated guided-scan folders."""

        return self.data_dir / "scans"
