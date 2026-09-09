"""Typer command-line interface."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from pokemon_go_cleanup.adb import AdbClient
from pokemon_go_cleanup.annotation import AnnotationService
from pokemon_go_cleanup.automation import (
    AutoScanService,
    HuaweiMate30PageDetector,
    planned_actions,
)
from pokemon_go_cleanup.batch import (
    BatchScanResult,
    BatchScanService,
    IvOnlyBatchRenameService,
)
from pokemon_go_cleanup.capture import CaptureService
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.dataset import ANNOTATION_FILENAME, validate_dataset
from pokemon_go_cleanup.exceptions import (
    AnnotationError,
    AnnotationExistsError,
    PokemonGoCleanupError,
    RecognitionError,
)
from pokemon_go_cleanup.guided_prompts import prompt_guided_step
from pokemon_go_cleanup.logging_config import configure_logging
from pokemon_go_cleanup.models import GroundTruth, ScanValidationResult
from pokemon_go_cleanup.recognition import (
    RecognitionResult,
    RecognitionService,
    discover_complete_scans,
    write_inventory_csv,
)
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
dataset_app = typer.Typer(
    name="dataset",
    help="Validate and inspect local guided-scan datasets.",
    no_args_is_help=True,
)
app.add_typer(device_app)
app.add_typer(dataset_app)


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
        typer.echo(f"{device.serial_number}\t{device.state}\t{device.model_name or '-'}")
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
        typer.echo("Error: scan-one requires --guided.", err=True)
        raise typer.Exit(code=2)

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


@app.command("rename-iv-one")
def rename_iv_one(
    context: typer.Context,
    serial_number: Annotated[
        str | None,
        typer.Option("--serial", "-s", help="ADB serial number to select."),
    ] = None,
) -> None:
    """Read IVs and rename the current Pokemon; no moves, CSV, or saved scans."""

    typer.echo("扫描当前一只的 IV 并命名。不扫描技能，不保存 CSV 或截图。")
    try:
        client = _build_client(context)
        reader = RecognitionService()
        result = AutoScanService(
            _get_context(context).config,
            client,
            HuaweiMate30PageDetector(reader),
            reader,
        ).rename_iv_one(serial_number=serial_number)
    except KeyboardInterrupt:
        typer.echo("已停止 IV 命名。未保存文件。", err=True)
        raise typer.Exit(code=130) from None
    except PokemonGoCleanupError as error:
        _abort(error)
    typer.echo(f"命名完成：已追加圈号 IV {result.iv_suffix}")


@app.command("rename-iv-batch")
def rename_iv_batch(
    context: typer.Context,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="Maximum Pokemon to rename in this run."),
    ] = 20,
    delay_seconds: Annotated[
        float,
        typer.Option(
            "--delay",
            min=0,
            max=120,
            help="Extra seconds to wait before switching to the next Pokemon.",
        ),
    ] = 1.0,
    serial_number: Annotated[
        str | None,
        typer.Option("--serial", "-s", help="ADB serial number to select."),
    ] = None,
) -> None:
    """Rename a bounded sequence through the no-file IV-only workflow."""

    typer.echo(
        f"批量扫描 IV 并命名，最多 {limit} 只。不扫描技能，不保存 CSV 或扫描文件。"
    )
    try:
        client = _build_client(context)
        reader = RecognitionService()
        detector = HuaweiMate30PageDetector(reader)
        scanner = AutoScanService(
            _get_context(context).config,
            client,
            detector,
            reader,
        )
        result = IvOnlyBatchRenameService(client, scanner, detector).rename(
            limit=limit,
            delay_seconds=delay_seconds,
            serial_number=serial_number,
            on_success=lambda completed, maximum, item: typer.echo(
                f"IV_ONLY_PROGRESS {completed}/{maximum} {item.iv_suffix}"
            ),
        )
    except KeyboardInterrupt:
        typer.echo("已停止批量 IV 命名，不会继续切换下一只。", err=True)
        raise typer.Exit(code=130) from None
    except PokemonGoCleanupError as error:
        _abort(error)
    typer.echo(
        f"批量 IV 命名完成：成功 {result.completed_count} 只，"
        f"停止原因：{result.stop_reason}"
    )


@app.command("scan-auto-one")
def scan_auto_one(
    context: typer.Context,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Save raw before/after screens, state evidence, and coordinates.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Detect the initial page and print the plan without ADB input.",
        ),
    ] = False,
    rename_with_iv: Annotated[
        bool,
        typer.Option(
            "--rename-with-iv",
            help="Reset the nickname to the default Chinese name, then append recognized IVs.",
        ),
    ] = False,
    serial_number: Annotated[
        str | None,
        typer.Option("--serial", "-s", help="ADB serial number to select."),
    ] = None,
    notes: Annotated[
        str | None,
        typer.Option("--notes", help="Optional notes stored only in manifest.json."),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Data directory for this scan; files are stored below OUTPUT/scans.",
        ),
    ] = None,
) -> None:
    """Automatically scan one fixed-layout Huawei Mate 30 detail page."""

    cli_context = _get_context(context)
    scan_config = cli_context.config
    if output is not None:
        scan_config = AppConfig(
            data_dir=output,
            adb_path=scan_config.adb_path,
            adb_timeout_seconds=scan_config.adb_timeout_seconds,
        )

    if dry_run:
        typer.echo("DRY RUN: no ADB tap, swipe, or BACK command will be sent.")
        typer.echo("Planned Huawei Mate 30 coordinates:")
        for action in planned_actions(rename_with_iv=rename_with_iv):
            typer.echo(f"- {action['name']} ({action['kind']}): {action['coordinates']}")

    try:
        client = _build_client(context)
        reader = RecognitionService()
        detector = HuaweiMate30PageDetector(reader)
        result = AutoScanService(
            scan_config,
            client,
            detector,
            reader,
        ).scan_one(
            debug=debug,
            dry_run=dry_run,
            rename_with_iv=rename_with_iv,
            serial_number=serial_number,
            notes=notes,
        )
    except KeyboardInterrupt:
        typer.echo("Stopped by Ctrl+C; the manifest was marked incomplete.", err=True)
        raise typer.Exit(code=130) from None
    except PokemonGoCleanupError as error:
        _abort(error)

    if result.dry_run:
        typer.echo(f"Dry-run scan directory: {result.scan_directory}")
        typer.echo(f"Manifest marked incomplete (dry_run): {result.manifest_path}")
        return
    if result.recognition is None:
        _abort(RecognitionError("Automatic scan completed without recognition output."))
    _echo_recognition_results((result.recognition,))
    typer.echo(f"Scan directory: {result.scan_directory}")
    typer.echo(f"Saved recognition: {result.scan_directory / 'recognition.json'}")


@app.command("scan-batch")
def scan_batch(
    context: typer.Context,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, help="Maximum total rows in this batch CSV."),
    ] = 20,
    csv_path: Annotated[
        Path,
        typer.Option("--csv", help="Atomic batch inventory CSV destination."),
    ] = Path("inventory.csv"),
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Keep per-scan automation and switch evidence."),
    ] = False,
    rename_with_iv: Annotated[
        bool,
        typer.Option(
            "--rename-with-iv",
            help="Reset each nickname to the default Chinese name and append recognized IVs.",
        ),
    ] = False,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="Validate and continue an existing batch CSV."),
    ] = False,
    delay_seconds: Annotated[
        float,
        typer.Option(
            "--delay",
            min=0,
            max=120,
            help="Extra seconds to wait before switching to the next Pokémon.",
        ),
    ] = 1.0,
) -> None:
    """Automatically scan a bounded sequence on the fixed Huawei Mate 30 layout."""

    cli_context = _get_context(context)
    try:
        client = _build_client(context)
        reader = RecognitionService()
        detector = HuaweiMate30PageDetector(reader)
        scanner = AutoScanService(
            cli_context.config,
            client,
            detector,
            reader,
        )
        result = BatchScanService(client, scanner, detector).scan(
            limit=limit,
            csv_path=csv_path,
            debug=debug,
            resume=resume,
            delay_seconds=delay_seconds,
            rename_with_iv=rename_with_iv,
        )
    except KeyboardInterrupt:
        typer.echo(
            "Stopped by Ctrl+C; completed CSV rows and scan files were preserved.",
            err=True,
        )
        raise typer.Exit(code=130) from None
    except PokemonGoCleanupError as error:
        _abort(error)

    _echo_batch_result(result)


def _echo_batch_result(result: BatchScanResult) -> None:
    headers = ("INDEX", "SCAN ID", "POKEMON", "CP", "STATUS")
    rows: list[tuple[str, ...]] = [
        (
            str(row.batch_index),
            row.scan_id,
            _recognition_value(row.pokemon_name),
            _recognition_value(row.cp),
            "saved",
        )
        for row in result.new_rows
    ]
    if rows:
        _echo_table(headers, rows)
    typer.echo(f"Batch stopped: {result.stop_reason}")
    typer.echo(f"CSV rows: {len(result.rows)}")
    typer.echo(f"Saved CSV: {result.csv_path}")


def _dataset_root(context: typer.Context, output: Path | None) -> Path:
    configured_root = _get_context(context).config.scan_root
    return (output if output is not None else configured_root).expanduser().resolve()


def _manifest_state(result: ScanValidationResult) -> str:
    if result.manifest_valid:
        return "valid"
    if "missing manifest.json" in result.issues:
        return "missing"
    return "invalid"


def _annotation_state(result: ScanValidationResult) -> str:
    return "yes" if result.annotation_present else "no"


def _echo_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [
        max(len(header), *(len(row[index]) for row in rows)) for index, header in enumerate(headers)
    ]
    typer.echo("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    typer.echo("  ".join("-" * width for width in widths))
    for row in rows:
        typer.echo("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _echo_dataset_results(
    results: tuple[ScanValidationResult, ...],
    *,
    include_issues: bool,
) -> None:
    headers = [
        "SCAN ID",
        "CAPTURE DATE",
        "SCREENSHOTS",
        "MANIFEST",
        "ANNOTATION",
        "STATUS",
    ]
    if include_issues:
        headers.append("DETAILS")

    rows: list[tuple[str, ...]] = []
    for result in results:
        row: tuple[str, ...] = (
            result.scan_id,
            result.capture_date.isoformat() if result.capture_date is not None else "-",
            f"{result.screenshot_count}/3",
            _manifest_state(result),
            _annotation_state(result),
            result.overall_status,
        )
        if include_issues:
            row += ("; ".join(result.issues) if result.issues else "-",)
        rows.append(row)
    _echo_table(tuple(headers), rows)


def _prompt_required_text(label: str, current: str | None = None) -> str:
    value = typer.prompt(
        label,
        default=current,
        show_default=current is not None,
    )
    return str(value).strip()


def _prompt_optional_text(label: str, current: str | None = None) -> str | None:
    value = typer.prompt(
        label,
        default=current if current is not None else "",
        show_default=current is not None,
    )
    normalized = str(value).strip()
    return normalized or None


def _prompt_integer(label: str, current: int | None = None) -> int:
    return int(
        typer.prompt(
            label,
            default=current,
            show_default=current is not None,
            type=int,
        )
    )


def _prompt_float(label: str, current: float | None = None) -> float:
    return float(
        typer.prompt(
            label,
            default=current,
            show_default=current is not None,
            type=float,
        )
    )


def _prompt_ground_truth(existing: GroundTruth | None) -> GroundTruth:
    existing_types = ", ".join(existing.types) if existing is not None else None
    try:
        return GroundTruth(
            pokemon_name=_prompt_required_text(
                "宝可梦名称 / Pokémon name",
                existing.pokemon_name if existing is not None else None,
            ),
            cp=_prompt_integer("CP", existing.cp if existing is not None else None),
            hp_current=_prompt_integer(
                "当前 HP / Current HP",
                existing.hp_current if existing is not None else None,
            ),
            hp_max=_prompt_integer(
                "最大 HP / Maximum HP",
                existing.hp_max if existing is not None else None,
            ),
            weight_kg=_prompt_float(
                "体重 kg / Weight kg",
                existing.weight_kg if existing is not None else None,
            ),
            height_m=_prompt_float(
                "身高 m / Height m",
                existing.height_m if existing is not None else None,
            ),
            types=tuple(
                value.strip()
                for value in _prompt_required_text(
                    "属性类型 (逗号分隔) / Types", existing_types
                ).split(",")
            ),
            fast_move=_prompt_required_text(
                "一般招式 / Fast move",
                existing.fast_move if existing is not None else None,
            ),
            charged_move_1=_prompt_required_text(
                "特殊招式 1 / Charged move 1",
                existing.charged_move_1 if existing is not None else None,
            ),
            charged_move_2=_prompt_optional_text(
                "特殊招式 2 (可选) / Charged move 2 (optional)",
                existing.charged_move_2 if existing is not None else None,
            ),
            attack_iv=_prompt_integer(
                "攻击 IV (0-15) / Attack IV",
                existing.attack_iv if existing is not None else None,
            ),
            defense_iv=_prompt_integer(
                "防御 IV (0-15) / Defense IV",
                existing.defense_iv if existing is not None else None,
            ),
            hp_iv=_prompt_integer(
                "HP IV (0-15) / HP IV",
                existing.hp_iv if existing is not None else None,
            ),
            favorite=typer.confirm(
                "已收藏 / Favorite",
                default=existing.favorite if existing is not None else False,
            ),
            shiny=typer.confirm(
                "异色 / Shiny",
                default=existing.shiny if existing is not None else False,
            ),
            shadow=typer.confirm(
                "暗影 / Shadow",
                default=existing.shadow if existing is not None else False,
            ),
            purified=typer.confirm(
                "净化 / Purified",
                default=existing.purified if existing is not None else False,
            ),
            costume=typer.confirm(
                "特殊服饰 / Costume",
                default=existing.costume if existing is not None else False,
            ),
            notes=_prompt_optional_text(
                "备注 (可选) / Notes (optional)",
                existing.notes if existing is not None else None,
            ),
        )
    except ValidationError as error:
        raise AnnotationError(f"Invalid annotation values: {error}") from error


@dataset_app.command("validate")
def dataset_validate(
    context: typer.Context,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Dataset root containing dated scan directories.",
        ),
    ] = None,
) -> None:
    """Recursively validate screenshots, manifests, and annotations."""

    dataset_root = _dataset_root(context, output)
    try:
        results = validate_dataset(dataset_root)
    except PokemonGoCleanupError as error:
        _abort(error)

    if not results:
        typer.echo(f"No scan directories found under '{dataset_root}'.")
        return

    _echo_dataset_results(results, include_issues=True)
    counts = {
        status: sum(result.overall_status == status for result in results)
        for status in ("complete", "incomplete", "invalid")
    }
    typer.echo(
        "Summary: "
        f"complete={counts['complete']}, "
        f"incomplete={counts['incomplete']}, "
        f"invalid={counts['invalid']}"
    )
    if counts["invalid"]:
        raise typer.Exit(code=1)


@dataset_app.command("status")
def dataset_status(
    context: typer.Context,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Dataset root containing dated scan directories.",
        ),
    ] = None,
) -> None:
    """Show scan completeness, manifest validity, and annotation presence."""

    dataset_root = _dataset_root(context, output)
    try:
        results = validate_dataset(dataset_root)
    except PokemonGoCleanupError as error:
        _abort(error)

    if not results:
        typer.echo(f"No scan directories found under '{dataset_root}'.")
        return
    _echo_dataset_results(results, include_issues=False)


@app.command()
def annotate(
    scan_directory: Annotated[
        Path,
        typer.Argument(help="Directory containing one summary/moves/appraisal scan."),
    ],
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace ground_truth.json without prompting."),
    ] = False,
    input_json: Annotated[
        Path | None,
        typer.Option(
            "--input-json",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Validate values from JSON instead of prompting.",
        ),
    ] = None,
) -> None:
    """Create one validated UTF-8 ground_truth.json annotation."""

    service = AnnotationService()
    try:
        screenshot_paths = service.resolve_screenshot_paths(scan_directory)
    except PokemonGoCleanupError as error:
        _abort(error)

    typer.echo("Screenshot paths / 截图完整路径:")
    for step, path in screenshot_paths.items():
        typer.echo(f"{step}: {path}")

    resolved_scan_directory = scan_directory.expanduser().resolve()
    annotation_path = resolved_scan_directory / ANNOTATION_FILENAME
    allow_overwrite = force
    if annotation_path.exists() and not force:
        if input_json is not None:
            _abort(AnnotationExistsError(annotation_path))
        allow_overwrite = typer.confirm(
            f"ground_truth.json 已存在, 是否覆盖? / Overwrite '{annotation_path}'?",
            default=False,
        )
        if not allow_overwrite:
            typer.echo("Annotation was not changed.")
            return

    try:
        if input_json is not None:
            annotation = service.load_json_input(input_json)
        else:
            annotation = _prompt_ground_truth(service.load_existing(resolved_scan_directory))
        saved_path = service.save(
            resolved_scan_directory,
            annotation,
            allow_overwrite=allow_overwrite,
        )
    except PokemonGoCleanupError as error:
        _abort(error)
    typer.echo(f"Saved annotation: {saved_path.resolve()}")


def _recognition_value(value: object | None) -> str:
    return "-" if value is None or value == "" else str(value)


def _echo_recognition_results(results: tuple[RecognitionResult, ...]) -> None:
    headers = (
        "SCAN ID",
        "POKEMON",
        "CP",
        "FAST MOVE",
        "CHARGED MOVE 1",
        "CHARGED MOVE 2",
        "ATK",
        "DEF",
        "HP",
        "WARNINGS",
    )
    rows: list[tuple[str, ...]] = [
        (
            result.scan_id,
            _recognition_value(result.pokemon_name.value),
            _recognition_value(result.cp.value),
            _recognition_value(result.fast_move.value),
            _recognition_value(result.charged_move_1.value),
            _recognition_value(result.charged_move_2.value),
            _recognition_value(result.attack_iv),
            _recognition_value(result.defense_iv),
            _recognition_value(result.hp_iv),
            " | ".join(result.warnings) or "-",
        )
        for result in results
    ]
    _echo_table(headers, rows)


@app.command("read-scan")
def read_scan_command(
    scan_directory: Annotated[
        Path,
        typer.Argument(help="One directory containing the three calibrated screenshots."),
    ],
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Save calibrated crops and IV detection overlays."),
    ] = False,
) -> None:
    """Read one Huawei Mate 30 Traditional Chinese scan."""

    try:
        result = RecognitionService().read_scan(scan_directory, debug=debug)
    except PokemonGoCleanupError as error:
        _abort(error)
    _echo_recognition_results((result,))
    typer.echo(f"Saved recognition: {(scan_directory.resolve() / 'recognition.json')}")


@app.command("read-dataset")
def read_dataset_command(
    context: typer.Context,
    csv_path: Annotated[
        Path,
        typer.Option(
            "--csv",
            help="Destination CSV path, for example inventory.csv.",
        ),
    ],
) -> None:
    """Read every complete local scan and write one inventory CSV row each."""

    try:
        scan_directories = discover_complete_scans(_get_context(context).config.scan_root)
        if not scan_directories:
            raise RecognitionError("No complete scans were found.")
        service = RecognitionService()
        results = tuple(service.read_scan(scan_directory) for scan_directory in scan_directories)
        destination = write_inventory_csv(csv_path, results)
    except PokemonGoCleanupError as error:
        _abort(error)
    _echo_recognition_results(results)
    typer.echo(f"Saved CSV: {destination}")
