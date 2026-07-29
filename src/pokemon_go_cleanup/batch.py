"""Bounded Huawei Mate 30 batch orchestration built on the proven one-scan flow."""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pokemon_go_cleanup.automation import (
    AutoScanResult,
    ExpectedStates,
    PageDetection,
    Point,
    Swipe,
)
from pokemon_go_cleanup.exceptions import BatchAutomationError, LocalStorageError
from pokemon_go_cleanup.models import Device, ScanManifest
from pokemon_go_cleanup.recognition import (
    RecognitionResult,
    normalize_ocr_text,
    parse_cp_raw,
)
from pokemon_go_cleanup.storage import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

BATCH_CSV_COLUMNS: Final = (
    "batch_index",
    "scan_id",
    "pokemon_name",
    "cp",
    "page_fingerprint",
    "fast_move",
    "charged_move_1",
    "charged_move_2",
    "attack_iv",
    "defense_iv",
    "hp_iv",
    "warnings",
    "scan_directory",
)
BatchStopReason = Literal["limit_reached", "wrapped_to_first"]


@dataclass(frozen=True, slots=True)
class HuaweiMate30BatchConfig:
    """Fixed switching gesture and timing for the proven 1440x3120 layout."""

    next_pokemon: Swipe = field(
        default_factory=lambda: Swipe(Point(260, 1500), Point(1180, 1500), 600)
    )
    poll_interval_seconds: float = 0.5
    switch_timeout_seconds: float = 15.0
    max_switch_attempts: int = 2
    fingerprint_crop: tuple[int, int, int, int] = (150, 180, 1290, 1650)
    fingerprint_size: int = 16


HUAWEI_MATE_30_BATCH: Final = HuaweiMate30BatchConfig()


class BatchCsvRow(BaseModel):
    """One durable batch inventory row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    batch_index: int = Field(gt=0)
    scan_id: str = Field(min_length=1)
    pokemon_name: str | None = None
    cp: int | None = Field(default=None, gt=0)
    page_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    fast_move: str | None = None
    charged_move_1: str | None = None
    charged_move_2: str | None = None
    attack_iv: int | None = Field(default=None, ge=0, le=15)
    defense_iv: int | None = Field(default=None, ge=0, le=15)
    hp_iv: int | None = Field(default=None, ge=0, le=15)
    warnings: str = ""
    scan_directory: Path

    def to_csv_row(self) -> dict[str, object]:
        """Return values formatted for ``csv.DictWriter``."""

        return {
            "batch_index": self.batch_index,
            "scan_id": self.scan_id,
            "pokemon_name": self.pokemon_name or "",
            "cp": "" if self.cp is None else self.cp,
            "page_fingerprint": self.page_fingerprint,
            "fast_move": self.fast_move or "",
            "charged_move_1": self.charged_move_1 or "",
            "charged_move_2": self.charged_move_2 or "",
            "attack_iv": "" if self.attack_iv is None else self.attack_iv,
            "defense_iv": "" if self.defense_iv is None else self.defense_iv,
            "hp_iv": "" if self.hp_iv is None else self.hp_iv,
            "warnings": self.warnings,
            "scan_directory": str(self.scan_directory),
        }


@dataclass(frozen=True, slots=True)
class SummaryIdentity:
    """OCR identity and perceptual fingerprint for one detail summary."""

    pokemon_name: str
    cp: int
    page_fingerprint: str


@dataclass(frozen=True, slots=True)
class BatchScanResult:
    """Completed rows and the safe reason the batch stopped."""

    csv_path: Path
    rows: tuple[BatchCsvRow, ...]
    new_rows: tuple[BatchCsvRow, ...]
    stop_reason: BatchStopReason


@dataclass(frozen=True, slots=True)
class _SwitchWaitResult:
    screenshot: bytes
    detection: PageDetection
    identity: SummaryIdentity | None
    outcome: Literal["changed", "same", "unexpected"]
    elapsed_seconds: float


class BatchAdbGateway(Protocol):
    """ADB operations needed outside the existing one-scan state machine."""

    def resolve_device(self, serial_number: str | None = None) -> Device: ...
    def capture_screen(self, serial_number: str) -> bytes: ...
    def swipe(
        self,
        serial_number: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int,
    ) -> None: ...


class SingleScanRunner(Protocol):
    """The existing automatic one-scan service surface."""

    def scan_one(
        self,
        *,
        debug: bool = False,
        dry_run: bool = False,
        serial_number: str | None = None,
        notes: str | None = None,
    ) -> AutoScanResult: ...


class BatchPageDetector(Protocol):
    """Existing page detector used to gate horizontal switching."""

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: ExpectedStates,
    ) -> PageDetection: ...


def page_fingerprint(
    png_bytes: bytes,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> str:
    """Return a 256-bit difference hash for the fixed summary display region."""

    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            if image.size != (1440, 3120):
                raise BatchAutomationError(
                    f"Batch fingerprint requires 1440x3120; got {image.width}x{image.height}."
                )
            left, top, right, bottom = config.fingerprint_crop
            prepared = (
                image.convert("L")
                .crop((left, top, right, bottom))
                .resize(
                    (config.fingerprint_size + 1, config.fingerprint_size),
                    Image.Resampling.BILINEAR,
                )
            )
            pixels = cast(list[int], list(prepared.get_flattened_data()))
    except BatchAutomationError:
        raise
    except (OSError, ValueError) as error:
        raise BatchAutomationError(
            f"Could not decode screenshot for batch fingerprint: {error}"
        ) from error

    value = 0
    width = config.fingerprint_size + 1
    for y in range(config.fingerprint_size):
        row_start = y * width
        for x in range(config.fingerprint_size):
            value = (value << 1) | int(pixels[row_start + x] > pixels[row_start + x + 1])
    return f"{value:064x}"


def summary_identity(
    detection: PageDetection,
    png_bytes: bytes,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> SummaryIdentity:
    """Extract the name/CP evidence emitted by the existing summary detector."""

    if detection.state != "detail_summary":
        raise BatchAutomationError(f"Expected detail_summary; detected {detection.state}.")
    cp: int | None = None
    name: str | None = None
    for raw in detection.matched_texts:
        parsed_cp = parse_cp_raw(raw)
        if parsed_cp is not None and cp is None:
            cp = parsed_cp
            continue
        normalized = normalize_ocr_text(raw)
        if normalized and name is None:
            name = normalized
    if name is None or cp is None:
        raise BatchAutomationError(
            "detail_summary did not provide both a reliable Pokémon name and CP."
        )
    return SummaryIdentity(name, cp, page_fingerprint(png_bytes, config))


def _recognition_identity(
    result: RecognitionResult,
    summary_png: bytes,
    config: HuaweiMate30BatchConfig,
) -> SummaryIdentity | None:
    if result.pokemon_name.value is None or result.cp.value is None:
        return None
    return SummaryIdentity(
        pokemon_name=normalize_ocr_text(result.pokemon_name.value),
        cp=result.cp.value,
        page_fingerprint=page_fingerprint(summary_png, config),
    )


def _same_name_and_cp(first: SummaryIdentity, second: SummaryIdentity) -> bool:
    return first.pokemon_name == second.pokemon_name and first.cp == second.cp


def _optional_integer(value: str) -> int | None:
    normalized = value.strip()
    return None if not normalized else int(normalized)


class _BatchCsvStore:
    def __init__(self, path: Path, *, resume: bool) -> None:
        self.path = path.expanduser().resolve()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise LocalStorageError(
                f"Could not create CSV directory '{self.path.parent}': {error}"
            ) from error
        if self.path.exists() and not resume:
            raise BatchAutomationError(
                f"Batch CSV already exists at '{self.path}'. Pass --resume to append safely."
            )
        self.rows = self._load() if self.path.exists() else []
        self._scan_ids = {row.scan_id for row in self.rows}
        if len(self._scan_ids) != len(self.rows):
            raise BatchAutomationError(
                f"Batch CSV '{self.path}' contains duplicate scan_id values."
            )
        if resume:
            self._validate_completed_rows()

    def append(self, row: BatchCsvRow) -> bool:
        """Persist a new row atomically; return False for a resumed duplicate."""

        if row.scan_id in self._scan_ids:
            return False
        self.rows.append(row)
        self._scan_ids.add(row.scan_id)
        self._write()
        return True

    def _load(self) -> list[BatchCsvRow]:
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source)
                if tuple(reader.fieldnames or ()) != BATCH_CSV_COLUMNS:
                    raise BatchAutomationError(
                        f"Batch CSV '{self.path}' has an incompatible header."
                    )
                output: list[BatchCsvRow] = []
                for raw in reader:
                    output.append(
                        BatchCsvRow(
                            batch_index=int(raw["batch_index"]),
                            scan_id=raw["scan_id"],
                            pokemon_name=raw["pokemon_name"] or None,
                            cp=_optional_integer(raw["cp"]),
                            page_fingerprint=raw["page_fingerprint"],
                            fast_move=raw["fast_move"] or None,
                            charged_move_1=raw["charged_move_1"] or None,
                            charged_move_2=raw["charged_move_2"] or None,
                            attack_iv=_optional_integer(raw["attack_iv"]),
                            defense_iv=_optional_integer(raw["defense_iv"]),
                            hp_iv=_optional_integer(raw["hp_iv"]),
                            warnings=raw["warnings"],
                            scan_directory=Path(raw["scan_directory"]),
                        )
                    )
                return output
        except BatchAutomationError:
            raise
        except (OSError, UnicodeError, KeyError, ValueError, ValidationError) as error:
            raise BatchAutomationError(
                f"Could not resume batch CSV '{self.path}': {error}"
            ) from error

    def _validate_completed_rows(self) -> None:
        for row in self.rows:
            manifest_path = row.scan_directory / "manifest.json"
            try:
                manifest = ScanManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, ValidationError) as error:
                raise BatchAutomationError(
                    f"Resume row {row.scan_id} has no valid complete manifest: {error}"
                ) from error
            if manifest.scan_id != row.scan_id or manifest.scan_status != "complete":
                raise BatchAutomationError(
                    f"Resume row {row.scan_id} does not reference a complete scan."
                )

    def _write(self) -> None:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=BATCH_CSV_COLUMNS,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(row.to_csv_row() for row in self.rows)
        atomic_write_text(self.path, buffer.getvalue())


class _BatchDebugRecorder:
    def __init__(self, scan_directory: Path, enabled: bool) -> None:
        self.enabled = enabled
        self.directory = scan_directory / "debug" / "batch"
        if enabled:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise LocalStorageError(
                    f"Could not create batch debug directory '{self.directory}': {error}"
                ) from error

    def screen(self, label: str, png_bytes: bytes) -> None:
        if self.enabled:
            atomic_write_bytes(self.directory / f"{label}.png", png_bytes)

    def json(self, name: str, data: object) -> None:
        if self.enabled:
            atomic_write_text(
                self.directory / name,
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            )


class BatchScanService:
    """Scan, persist, and safely advance through a bounded number of Pokémon."""

    def __init__(
        self,
        adb: BatchAdbGateway,
        scanner: SingleScanRunner,
        detector: BatchPageDetector,
        *,
        config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adb = adb
        self._scanner = scanner
        self._detector = detector
        self._config = config
        self._monotonic = monotonic
        self._sleeper = sleeper

    def scan(
        self,
        *,
        limit: int,
        csv_path: Path,
        debug: bool,
        resume: bool,
        delay_seconds: float,
        serial_number: str | None = None,
    ) -> BatchScanResult:
        """Run the proven one-scan workflow repeatedly with guarded switching."""

        if limit <= 0:
            raise BatchAutomationError("--limit must be greater than 0.")
        if delay_seconds < 0:
            raise BatchAutomationError("--delay cannot be negative.")
        store = _BatchCsvStore(csv_path, resume=resume)
        if len(store.rows) >= limit:
            return BatchScanResult(
                csv_path=store.path,
                rows=tuple(store.rows),
                new_rows=(),
                stop_reason="limit_reached",
            )

        device = self._adb.resolve_device(serial_number)
        first_identity = self._identity_from_row(store.rows[0]) if store.rows else None
        new_rows: list[BatchCsvRow] = []

        while len(store.rows) < limit:
            scan = self._scanner.scan_one(
                debug=debug,
                serial_number=device.serial_number,
            )
            if scan.manifest.scan_status != "complete" or scan.recognition is None:
                raise BatchAutomationError(
                    "The one-Pokémon scan did not return a complete recognized result."
                )
            summary_path = scan.scan_directory / "summary.png"
            try:
                summary_png = summary_path.read_bytes()
            except OSError as error:
                raise BatchAutomationError(
                    f"Could not read completed summary screenshot '{summary_path}': {error}"
                ) from error
            current_identity = _recognition_identity(
                scan.recognition,
                summary_png,
                self._config,
            )
            row = self._row(
                len(store.rows) + 1,
                scan,
                page_fingerprint(summary_png, self._config),
            )
            if store.append(row):
                new_rows.append(row)
                logger.info(
                    "batch_scan_row_saved",
                    extra={
                        "batch_index": row.batch_index,
                        "scan_id": row.scan_id,
                        "csv_path": str(store.path),
                    },
                )
            else:
                logger.info(
                    "batch_scan_row_skipped",
                    extra={"scan_id": row.scan_id, "reason": "resume_duplicate"},
                )

            if len(store.rows) >= limit:
                return BatchScanResult(
                    csv_path=store.path,
                    rows=tuple(store.rows),
                    new_rows=tuple(new_rows),
                    stop_reason="limit_reached",
                )
            if current_identity is None:
                raise BatchAutomationError(
                    "The completed scan has no reliable name/CP, so switching would be unsafe."
                )
            if first_identity is None:
                first_identity = current_identity

            if delay_seconds:
                self._sleeper(delay_seconds)
            next_identity = self._switch_to_next(
                device.serial_number,
                current_identity,
                scan.scan_directory,
                debug=debug,
            )
            if _same_name_and_cp(next_identity, first_identity):
                return BatchScanResult(
                    csv_path=store.path,
                    rows=tuple(store.rows),
                    new_rows=tuple(new_rows),
                    stop_reason="wrapped_to_first",
                )

        raise AssertionError("Batch loop terminated without a stop reason.")

    def _switch_to_next(
        self,
        serial: str,
        previous: SummaryIdentity,
        scan_directory: Path,
        *,
        debug: bool,
    ) -> SummaryIdentity:
        recorder = _BatchDebugRecorder(scan_directory, debug)
        before = self._adb.capture_screen(serial)
        recorder.screen("before_switch", before)
        before_detection = self._detector.detect(
            before,
            expected=("detail_summary",),
        )
        recorder.json("before_switch_state.json", before_detection.to_json_data())
        current = summary_identity(before_detection, before, self._config)
        if not _same_name_and_cp(current, previous):
            raise BatchAutomationError(
                "The detail page changed after the one-Pokémon scan; no swipe was sent."
            )

        for attempt in range(1, self._config.max_switch_attempts + 1):
            gesture = self._config.next_pokemon
            recorder.json(
                f"switch_attempt_{attempt}_action.json",
                {
                    "kind": "swipe",
                    "coordinates": asdict(gesture),
                    "before": asdict(current),
                },
            )
            self._adb.swipe(
                serial,
                gesture.start.x,
                gesture.start.y,
                gesture.end.x,
                gesture.end.y,
                gesture.duration_ms,
            )
            result = self._wait_for_next_summary(
                serial,
                previous,
                recorder,
                attempt,
            )
            recorder.screen(
                f"after_switch_attempt_{attempt}",
                result.screenshot,
            )
            recorder.json(
                f"switch_attempt_{attempt}_state.json",
                {
                    "outcome": result.outcome,
                    "elapsed_seconds": result.elapsed_seconds,
                    "detection": result.detection.to_json_data(),
                    "identity": (asdict(result.identity) if result.identity is not None else None),
                },
            )
            if result.outcome == "changed" and result.identity is not None:
                return result.identity
            if result.outcome == "unexpected":
                raise BatchAutomationError(
                    "Unexpected page state while switching Pokémon: "
                    f"{result.detection.state}. Batch stopped."
                )
            if attempt < self._config.max_switch_attempts:
                confirmation = self._adb.capture_screen(serial)
                recorder.screen(
                    f"before_switch_attempt_{attempt + 1}",
                    confirmation,
                )
                confirmation_detection = self._detector.detect(
                    confirmation,
                    expected=("detail_summary",),
                )
                current = summary_identity(
                    confirmation_detection,
                    confirmation,
                    self._config,
                )
                if not _same_name_and_cp(current, previous):
                    return current

        raise BatchAutomationError(
            "Two horizontal swipes did not reach a different name/CP within "
            "15 seconds each. Batch stopped to avoid rescanning the same Pokémon."
        )

    def _wait_for_next_summary(
        self,
        serial: str,
        previous: SummaryIdentity,
        recorder: _BatchDebugRecorder,
        attempt: int,
    ) -> _SwitchWaitResult:
        expected: ExpectedStates = (
            "detail_summary",
            "detail_moves",
            "action_menu",
            "appraisal_dialogue",
            "appraisal_bars",
        )
        interval = self._config.poll_interval_seconds
        timeout = self._config.switch_timeout_seconds
        started = self._monotonic()
        maximum_samples = math.ceil(timeout / interval)
        samples: list[dict[str, object]] = []
        last_screen: bytes | None = None
        last_detection: PageDetection | None = None
        last_identity: SummaryIdentity | None = None

        for sample_index in range(1, maximum_samples + 1):
            actual_elapsed = self._monotonic() - started
            if actual_elapsed >= timeout:
                break
            self._sleeper(min(interval, timeout - actual_elapsed))
            screenshot = self._adb.capture_screen(serial)
            detection = self._detect_switch_screen(screenshot, abnormal=expected[1:])
            measured_elapsed = self._monotonic() - started
            elapsed = max(measured_elapsed, sample_index * interval)
            identity: SummaryIdentity | None = None
            if detection.state == "detail_summary":
                identity = summary_identity(detection, screenshot, self._config)
            samples.append(
                {
                    "sample": sample_index,
                    "elapsed_seconds": elapsed,
                    "state": detection.state,
                    "confidence": detection.confidence,
                    "matched_texts": list(detection.matched_texts),
                    "identity": asdict(identity) if identity is not None else None,
                }
            )
            recorder.screen(
                f"switch_attempt_{attempt}_wait_{sample_index:02d}",
                screenshot,
            )
            recorder.json(
                f"switch_attempt_{attempt}_wait.json",
                {
                    "target_state": "detail_summary",
                    "interval_seconds": interval,
                    "timeout_seconds": timeout,
                    "samples": samples,
                },
            )
            last_screen = screenshot
            last_detection = detection
            last_identity = identity
            if identity is not None and not _same_name_and_cp(identity, previous):
                return _SwitchWaitResult(
                    screenshot,
                    detection,
                    identity,
                    "changed",
                    elapsed,
                )
            if detection.state not in ("detail_summary", "unknown"):
                return _SwitchWaitResult(
                    screenshot,
                    detection,
                    identity,
                    "unexpected",
                    elapsed,
                )
            if measured_elapsed >= timeout:
                break

        if last_screen is None or last_detection is None:
            last_screen = self._adb.capture_screen(serial)
            last_detection = self._detect_switch_screen(
                last_screen,
                abnormal=expected[1:],
            )
            if last_detection.state == "detail_summary":
                last_identity = summary_identity(
                    last_detection,
                    last_screen,
                    self._config,
                )
        if last_detection.state not in ("detail_summary", "unknown"):
            return _SwitchWaitResult(
                last_screen,
                last_detection,
                last_identity,
                "unexpected",
                timeout,
            )
        return _SwitchWaitResult(
            last_screen,
            last_detection,
            last_identity,
            "same",
            timeout,
        )

    def _detect_switch_screen(
        self,
        screenshot: bytes,
        *,
        abnormal: ExpectedStates,
    ) -> PageDetection:
        summary = self._detector.detect(
            screenshot,
            expected=("detail_summary",),
        )
        if summary.state == "detail_summary":
            return summary
        abnormal_detection = self._detector.detect(
            screenshot,
            expected=abnormal,
        )
        return abnormal_detection if abnormal_detection.state != "unknown" else summary

    @staticmethod
    def _identity_from_row(row: BatchCsvRow) -> SummaryIdentity | None:
        if row.pokemon_name is None or row.cp is None:
            return None
        return SummaryIdentity(
            row.pokemon_name,
            row.cp,
            row.page_fingerprint,
        )

    @staticmethod
    def _row(
        batch_index: int,
        scan: AutoScanResult,
        fingerprint: str,
    ) -> BatchCsvRow:
        recognition = scan.recognition
        if recognition is None:
            raise BatchAutomationError("Missing recognition result for completed scan.")
        return BatchCsvRow(
            batch_index=batch_index,
            scan_id=recognition.scan_id,
            pokemon_name=recognition.pokemon_name.value,
            cp=recognition.cp.value,
            page_fingerprint=fingerprint,
            fast_move=recognition.fast_move.value,
            charged_move_1=recognition.charged_move_1.value,
            charged_move_2=recognition.charged_move_2.value,
            attack_iv=recognition.attack_iv,
            defense_iv=recognition.defense_iv,
            hp_iv=recognition.hp_iv,
            warnings=" | ".join(recognition.warnings),
            scan_directory=scan.scan_directory.resolve(),
        )
