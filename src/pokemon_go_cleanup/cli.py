"""Typer command-line interface."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from pokemon_go_cleanup.adb import AdbClient
from pokemon_go_cleanup.capture import CaptureService
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import PokemonGoCleanupError
from pokemon_go_cleanup.guided_prompts import prompt_guided_step
from pokemon_go_cleanup.logging_config import configure_logging
from pokemon_go_cleanup.scan import GuidedScanService

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="pokemon-go-cleanup",
    help="Capture an ADB-connected device screen and store local metadata.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
device_app = typer.Typer(
    name="device",
    help="Inspect ADB-connected devices.",
    no_args_is_help=True,
)
app.add_typer(device_app)


@dataclass(frozen=True, slots=True)
class CliContext:
    """Dependencies configured by the root command."""

    config: AppConfig


def _abort(error: PokemonGoCleanupError) -> NoReturn:
    logger.error(
        "command_failed",
        extra={"error_type": type(error).__name__, "exit_code": error.exit_code},
    )
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=error.exit_code)


def _get_context(context: typer.Context) -> CliContext:
    value = context.obj
    if not isinstance(value, CliContext):
        raise RuntimeError("CLI context was not initialized.")
    return value


def _build_client(context: typer.Context) -> AdbClient:
    return AdbClient.from_config(_get_context(context).config)


@app.callback()
def main(
    context: typer.Context,
    data_dir: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            help="Local data directory for screenshots and guided scans.",
        ),
    ] = None,
    adb_path: Annotated[
        Path | None,
        typer.Option(
            "--adb-path",
            help="Path to adb.exe. When omitted, adb is discovered on PATH.",
        ),
    ] = None,
    adb_timeout_seconds: Annotated[
        float | None,
        typer.Option(
            "--adb-timeout",
            min=1,
            max=120,
            help="ADB command timeout in seconds.",
        ),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="DEBUG, INFO, WARNING, ERROR, or CRITICAL."),
    ] = "WARNING",
    log_format: Annotated[
        str,
        typer.Option("--log-format", help="Structured 'json' logs or human-readable 'text'."),
    ] = "json",
) -> None:
    """Configure local storage, ADB discovery, and structured logging."""

    normalized_log_level = log_level.upper()
    allowed_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if normalized_log_level not in allowed_log_levels:
        raise typer.BadParameter(
            f"Unsupported log level '{log_level}'.",
            param_hint="--log-level",
        )
    normalized_log_format = log_format.lower()
    if normalized_log_format not in {"json", "text"}:
        raise typer.BadParameter(
            f"Unsupported log format '{log_format}'.",
            param_hint="--log-format",
        )

    configure_logging(
        level=normalized_log_level,
        json_logs=normalized_log_format == "json",
    )
    try:
        environment_config = AppConfig()
        config = AppConfig(
            data_dir=data_dir if data_dir is not None else environment_config.data_dir,
            adb_path=adb_path if adb_path is not None else environment_config.adb_path,
            adb_timeout_seconds=(
                adb_timeout_seconds
                if adb_timeout_seconds is not None
                else environment_config.adb_timeout_seconds
            ),
        )
    except ValidationError as error:
        raise typer.BadParameter(str(error)) from error
    context.obj = CliContext(config=config)


@device_app.command("list")
def device_list(context: typer.Context) -> None:
    """List every ADB device, including unauthorized or offline devices."""

    try:
        devices = _build_client(context).list_devices()
    except PokemonGoCleanupError as error:
        _abort(error)

    if not devices:
        typer.echo("No connected ADB devices found.")
        return

    typer.echo("SERIAL\tSTATE\tMODEL")
    for device in devices:
        typer.echo(
            f"{device.serial_number}\t{device.state}\t{device.model_name or '-'}"
        )
    if any(device.state == "unauthorized" for device in devices):
        typer.echo(
            "Authorization required: unlock the device and accept the USB debugging prompt.",
            err=True,
        )


@device_app.command("info")
def device_info(
    context: typer.Context,
    serial_number: Annotated[
        str | None,
        typer.Option("--serial", "-s", help="ADB serial number to select."),
    ] = None,
) -> None:
    """Show identity fields and the current resolution for one device."""

    try:
        info = _build_client(context).get_device_info(serial_number)
    except PokemonGoCleanupError as error:
        _abort(error)
    typer.echo(info.model_dump_json(indent=2))


@app.command()
def capture(
    context: typer.Context,
    serial_number: Annotated[
        str | None,
        typer.Option("--serial", "-s", help="ADB serial number to select."),
    ] = None,
) -> None:
    """Capture the current screen and write a PNG plus JSON metadata."""

    cli_context = _get_context(context)
    try:
        client = AdbClient.from_config(cli_context.config)
        result = CaptureService(cli_context.config, client).capture(serial_number)
    except PokemonGoCleanupError as error:
        _abort(error)
    typer.echo(result.model_dump_json(indent=2))


@app.command("scan-one")
def scan_one(
    context: typer.Context,
    guided: Annotated[
        bool,
        typer.Option(
            "--guided",
            help="Run the manual three-screenshot guided workflow.",
        ),
    ] = False,
    notes: Annotated[
        str | None,
        typer.Option("--notes", help="Optional notes stored only in manifest.json."),
    ] = None,
    serial_number: Annotated[
        str | None,
        typer.Option("--serial", "-s", help="ADB serial number to select."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Data directory for this scan; files are stored below OUTPUT/scans.",
        ),
    ] = None,
) -> None:
    """Capture summary, moves, and appraisal views prepared by the user."""

    if not guided:
        raise typer.BadParameter(
            "Only guided mode is available. Pass --guided.",
            param_hint="--guided",
        )

    cli_context = _get_context(context)
    scan_config = cli_context.config
    if output is not None:
        scan_config = AppConfig(
            data_dir=output,
            adb_path=scan_config.adb_path,
            adb_timeout_seconds=scan_config.adb_timeout_seconds,
        )

    try:
        client = _build_client(context)
        result = GuidedScanService(scan_config, client).scan_one(
            prepare_step=prompt_guided_step,
            serial_number=serial_number,
            notes=notes,
        )
    except PokemonGoCleanupError as error:
        _abort(error)
    typer.echo(result.model_dump_json(indent=2))
