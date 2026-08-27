"""Bounded Huawei Mate 30 batch orchestration built on the proven one-scan flow."""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pokemon_go_cleanup.automation import (
    AutoScanResult,
    ExpectedStates,
    NicknameRenameResult,
    PageDetection,
    Point,
    Swipe,
    compact_editor_nickname_text,
    nickname_text_skeleton,
)
from pokemon_go_cleanup.exceptions import BatchAutomationError, LocalStorageError
from pokemon_go_cleanup.models import Device, ScanManifest
from pokemon_go_cleanup.recognition import (
    RecognitionResult,
    normalize_cp_candidate,
    normalize_ocr_text,
    parse_cp_raw,
)
from pokemon_go_cleanup.storage import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

LEGACY_BATCH_CSV_COLUMNS: Final = (
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
BATCH_CSV_COLUMNS: Final = (
    *LEGACY_BATCH_CSV_COLUMNS,
    "nickname_before",
    "nickname_after",
    "rename_status",
)
BatchStopReason = Literal["limit_reached", "wrapped_to_first"]
RenameStatus = Literal["not_requested", "verified"]


@dataclass(frozen=True, slots=True)
class HuaweiMate30BatchConfig:
    """Fixed switching gesture and timing for the proven 1440x3120 layout."""

    next_pokemon: Swipe = field(
        default_factory=lambda: Swipe(Point(1180, 1500), Point(260, 1500), 600)
    )
    retry_next_pokemon: Swipe = field(
        default_factory=lambda: Swipe(Point(1300, 1500), Point(140, 1500), 850)
    )
    poll_interval_seconds: float = 0.5
    switch_timeout_seconds: float = 15.0
    resume_cp_retry_frames: int = 2
    resume_cp_retry_interval_seconds: float = 0.5
    verified_switch_cp_retry_frames: int = 2
    verified_switch_cp_retry_interval_seconds: float = 0.5
    fingerprint_crop: tuple[int, int, int, int] = (100, 1550, 1340, 2300)
    fingerprint_size: int = 16
    duplicate_distance_threshold: int = 8


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
    nickname_before: str | None = None
    nickname_after: str | None = None
    rename_status: RenameStatus = "not_requested"

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
            "nickname_before": self.nickname_before or "",
            "nickname_after": self.nickname_after or "",
            "rename_status": self.rename_status,
        }


@dataclass(frozen=True, slots=True)
class SummaryIdentity:
    """OCR identity and static-region fingerprint for one detail summary."""

    pokemon_name: str
    cp: int
    page_fingerprint: str
    hp_text: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeObservation:
    """Partial current-page evidence used only before a resumed batch starts."""

    pokemon_name: str
    cp: int | None
    hp_text: str | None
    page_fingerprint: str
    observed_nickname: str | None


@dataclass(frozen=True, slots=True)
class _PartialSummaryObservation:
    """Partial summary evidence used when CP is temporarily unreadable."""

    pokemon_name: str
    hp_text: str | None
    page_fingerprint: str


@dataclass(frozen=True, slots=True)
class ExpectedNicknameTransition:
    """The only context allowed to accept an expected nickname mutation."""

    before: SummaryIdentity
    after: SummaryIdentity
    expected_nickname: str
    editor_observed_nickname: str
    summary_observed_nickname: str


def matches_expected_nickname_transition(
    transition: ExpectedNicknameTransition,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> bool:
    """Require exact planned text while every non-name identity feature remains."""

    expected = compact_editor_nickname_text(transition.expected_nickname)
    editor_observed = compact_editor_nickname_text(
        transition.editor_observed_nickname
    )
    if editor_observed != expected:
        return False
    if nickname_text_skeleton(transition.summary_observed_nickname) != (
        nickname_text_skeleton(expected)
    ):
        return False
    if transition.before.cp != transition.after.cp:
        return False
    if (
        transition.before.hp_text is not None
        and transition.after.hp_text is not None
        and transition.before.hp_text != transition.after.hp_text
    ):
        return False
    return (
        fingerprint_distance(
            transition.before.page_fingerprint,
            transition.after.page_fingerprint,
        )
        <= config.duplicate_distance_threshold
    )


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
    outcome: Literal["changed", "same", "unexpected", "cp_unreadable"]
    elapsed_seconds: float
    cp_fallback_used: bool = False


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
        rename_with_iv: bool = False,
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

    def read_summary_nickname(self, png_bytes: bytes) -> str: ...

    def read_summary_cp(self, png_bytes: bytes) -> int | None: ...

    def read_summary_hp(self, png_bytes: bytes) -> str | None: ...


def _static_summary_image(
    png_bytes: bytes,
    config: HuaweiMate30BatchConfig,
) -> Image.Image:
    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            if image.size != (1440, 3120):
                raise BatchAutomationError(
                    f"Batch fingerprint requires 1440x3120; got {image.width}x{image.height}."
                )
            left, top, right, bottom = config.fingerprint_crop
            return image.convert("RGB").crop((left, top, right, bottom))
    except BatchAutomationError:
        raise
    except (OSError, ValueError) as error:
        raise BatchAutomationError(
            f"Could not decode screenshot for batch fingerprint: {error}"
        ) from error


def static_summary_crop(
    png_bytes: bytes,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> bytes:
    """Return the fixed HP-through-details ROI as a PNG for debug evidence."""

    output = io.BytesIO()
    _static_summary_image(png_bytes, config).save(output, format="PNG")
    return output.getvalue()


def page_fingerprint(
    png_bytes: bytes,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> str:
    """Return a 256-bit hash for the static HP-through-details summary ROI."""

    prepared = (
        _static_summary_image(png_bytes, config)
        .convert("L")
        .resize(
            (config.fingerprint_size + 1, config.fingerprint_size),
            Image.Resampling.BILINEAR,
        )
    )
    pixels = cast(list[int], list(prepared.get_flattened_data()))
    value = 0
    width = config.fingerprint_size + 1
    for y in range(config.fingerprint_size):
        row_start = y * width
        for x in range(config.fingerprint_size):
            value = (value << 1) | int(pixels[row_start + x] > pixels[row_start + x + 1])
    return f"{value:064x}"


def fingerprint_distance(first: str, second: str) -> int:
    """Return the Hamming distance between two 256-bit hexadecimal hashes."""

    return (int(first, 16) ^ int(second, 16)).bit_count()


def same_static_summary(
    current: SummaryIdentity,
    previous: SummaryIdentity,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> bool:
    """Reject only an adjacent repeat supported by static ROI and OCR evidence."""

    if not _same_name_and_cp(current, previous):
        return False
    if (
        current.hp_text is not None
        and previous.hp_text is not None
        and current.hp_text != previous.hp_text
    ):
        return False
    return (
        fingerprint_distance(current.page_fingerprint, previous.page_fingerprint)
        <= config.duplicate_distance_threshold
    )


def _same_immutable_summary(
    current: SummaryIdentity,
    previous: SummaryIdentity,
    config: HuaweiMate30BatchConfig,
) -> bool:
    """Detect an ambiguous renamed page without weakening general identity checks."""

    if current.cp != previous.cp:
        return False
    if (
        current.hp_text is not None
        and previous.hp_text is not None
        and current.hp_text != previous.hp_text
    ):
        return False
    return (
        fingerprint_distance(current.page_fingerprint, previous.page_fingerprint)
        <= config.duplicate_distance_threshold
    )


def matches_verified_renamed_identity(
    current: SummaryIdentity,
    baseline: SummaryIdentity,
    *,
    expected_nickname: str,
    observed_nickname: str,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> bool:
    """Match one verified renamed page without trusting generic long-name OCR."""

    return nickname_text_skeleton(observed_nickname) == nickname_text_skeleton(
        expected_nickname
    ) and _same_immutable_summary(current, baseline, config)


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
    hp_value = detection.details.get("hp")
    hp_text = hp_value if isinstance(hp_value, str) else None
    return SummaryIdentity(
        name,
        cp,
        page_fingerprint(png_bytes, config),
        hp_text,
    )


def partial_summary_observation(
    detection: PageDetection,
    png_bytes: bytes,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> _PartialSummaryObservation:
    """Extract name, HP, and static evidence without requiring CP."""

    if detection.state != "detail_summary":
        raise BatchAutomationError(f"Expected detail_summary; detected {detection.state}.")
    name: str | None = None
    for raw in detection.matched_texts:
        if normalize_cp_candidate(raw) is not None:
            continue
        normalized = normalize_ocr_text(raw)
        if normalized:
            name = normalized
            break
    if name is None:
        raise BatchAutomationError(
            "detail_summary did not provide a reliable Pokémon name."
        )
    hp_value = detection.details.get("hp")
    return _PartialSummaryObservation(
        pokemon_name=name,
        hp_text=hp_value if isinstance(hp_value, str) else None,
        page_fingerprint=page_fingerprint(png_bytes, config),
    )


def resume_observation(
    detection: PageDetection,
    png_bytes: bytes,
    *,
    observed_nickname: str | None,
    config: HuaweiMate30BatchConfig = HUAWEI_MATE_30_BATCH,
) -> ResumeObservation:
    """Extract name-first resume evidence while leaving strict identities unchanged."""

    if detection.state != "detail_summary":
        raise BatchAutomationError(f"Expected detail_summary; detected {detection.state}.")
    cp: int | None = None
    name: str | None = None
    for raw in detection.matched_texts:
        normalized_cp = normalize_cp_candidate(raw)
        if normalized_cp is not None and cp is None:
            cp = int(normalized_cp[2:])
            continue
        normalized = normalize_ocr_text(raw)
        if normalized and name is None:
            name = normalized
    if name is None:
        raise BatchAutomationError(
            "Resume detail_summary did not provide a reliable Pokémon name."
        )
    hp_value = detection.details.get("hp")
    hp_text = hp_value if isinstance(hp_value, str) else None
    compact_nickname = (
        compact_editor_nickname_text(observed_nickname)
        if observed_nickname is not None
        else ""
    )
    return ResumeObservation(
        pokemon_name=name,
        cp=cp,
        hp_text=hp_text,
        page_fingerprint=page_fingerprint(png_bytes, config),
        observed_nickname=compact_nickname or None,
    )


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


def _same_switch_identity(
    first: SummaryIdentity,
    second: SummaryIdentity,
    config: HuaweiMate30BatchConfig,
) -> bool:
    return _same_name_and_cp(first, second) and (
        fingerprint_distance(first.page_fingerprint, second.page_fingerprint)
        <= config.duplicate_distance_threshold
    )


def _optional_integer(value: str) -> int | None:
    normalized = value.strip()
    return None if not normalized else int(normalized)


def _rename_status(value: str | None) -> RenameStatus:
    if value in (None, "", "not_requested"):
        return "not_requested"
    if value == "verified":
        return "verified"
    raise ValueError(f"unsupported rename_status: {value}")


class _BatchCsvStore:
    def __init__(self, path: Path, *, resume: bool, rename_with_iv: bool) -> None:
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
        expected_status: RenameStatus = "verified" if rename_with_iv else "not_requested"
        if self.rows and any(row.rename_status != expected_status for row in self.rows):
            raise BatchAutomationError(
                "Resume must use the same --rename-with-iv setting as every existing "
                "row in the batch CSV."
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
                header = tuple(reader.fieldnames or ())
                if header not in (LEGACY_BATCH_CSV_COLUMNS, BATCH_CSV_COLUMNS):
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
                            nickname_before=(
                                raw.get("nickname_before") or raw["pokemon_name"] or None
                            ),
                            nickname_after=raw.get("nickname_after") or None,
                            rename_status=_rename_status(raw.get("rename_status")),
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
            if row.rename_status == "verified":
                self._validate_nickname_evidence(row)

    @staticmethod
    def _validate_nickname_evidence(row: BatchCsvRow) -> None:
        if row.nickname_after is None:
            raise BatchAutomationError(
                f"Resume row {row.scan_id} is missing its verified nickname."
            )
        summary_path = row.scan_directory / "renamed_summary.png"
        evidence_path = row.scan_directory / "nickname_change.json"
        try:
            summary_path.read_bytes()
            raw = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BatchAutomationError(
                f"Resume row {row.scan_id} has invalid nickname evidence: {error}"
            ) from error
        if (
            not isinstance(raw, dict)
            or raw.get("status") != "verified"
            or raw.get("nickname_after") != row.nickname_after
            or raw.get("summary_filename") != "renamed_summary.png"
        ):
            raise BatchAutomationError(
                f"Resume row {row.scan_id} nickname evidence does not match its CSV row."
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

    def text(self, name: str, value: str) -> None:
        if self.enabled:
            atomic_write_text(self.directory / name, value.rstrip() + "\n")


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

    def _identity_from_recognition_or_cp(
        self,
        detection: PageDetection,
        png_bytes: bytes,
        recognition: RecognitionResult,
    ) -> SummaryIdentity:
        """Prefer fresh CP OCR, then reuse CP recognized from these same bytes."""

        recognized = _recognition_identity(recognition, png_bytes, self._config)
        if recognized is None:
            raise BatchAutomationError(
                "The completed recognition result did not provide a reliable name and CP."
            )
        try:
            strict = summary_identity(detection, png_bytes, self._config)
        except BatchAutomationError:
            observation = partial_summary_observation(
                detection,
                png_bytes,
                self._config,
            )
            if nickname_text_skeleton(observation.pokemon_name) != (
                nickname_text_skeleton(recognized.pokemon_name)
            ):
                raise BatchAutomationError(
                    "Saved summary without CP did not match the completed recognition name."
                ) from None
            return replace(
                recognized,
                hp_text=observation.hp_text,
            )
        if not _same_name_and_cp(strict, recognized):
            raise BatchAutomationError(
                "Saved summary OCR does not match the completed recognition result."
            )
        return strict

    def _identity_from_saved_row(
        self,
        detection: PageDetection,
        png_bytes: bytes,
        row: BatchCsvRow,
    ) -> SummaryIdentity:
        """Restore a durable row even when its unchanged screenshot loses CP OCR."""

        row_identity = self._identity_from_row(row)
        if row_identity is None:
            raise BatchAutomationError(
                f"CSV row {row.scan_id} has no reliable saved name and CP."
            )
        try:
            strict = summary_identity(detection, png_bytes, self._config)
        except BatchAutomationError:
            observation = partial_summary_observation(
                detection,
                png_bytes,
                self._config,
            )
            name_matches = nickname_text_skeleton(observation.pokemon_name) == (
                nickname_text_skeleton(row_identity.pokemon_name)
            )
            fingerprint_gap = fingerprint_distance(
                observation.page_fingerprint,
                row_identity.page_fingerprint,
            )
            if (
                not name_matches
                or fingerprint_gap > self._config.duplicate_distance_threshold
            ):
                raise BatchAutomationError(
                    f"CSV row {row.scan_id} does not match its referenced summary.png."
                ) from None
            return replace(
                row_identity,
                page_fingerprint=observation.page_fingerprint,
                hp_text=observation.hp_text,
            )
        if not _same_name_and_cp(strict, row_identity):
            raise BatchAutomationError(
                f"CSV row {row.scan_id} does not match its referenced summary.png."
            )
        return strict

    def _identity_from_strong_cp_fallback(
        self,
        detection: PageDetection,
        png_bytes: bytes,
        baseline: SummaryIdentity,
        *,
        baseline_png: bytes,
        expected_nickname: str,
        observed_nickname: str,
        require_generic_name: bool,
    ) -> SummaryIdentity | None:
        """Reuse baseline CP only after exact name, HP, and static-page proof."""

        observation = partial_summary_observation(
            detection,
            png_bytes,
            self._config,
        )
        baseline_hp = baseline.hp_text or self._detector.read_summary_hp(baseline_png)
        current_hp = observation.hp_text or self._detector.read_summary_hp(png_bytes)
        expected_skeleton = nickname_text_skeleton(expected_nickname)
        wide_name_matches = nickname_text_skeleton(observed_nickname) == expected_skeleton
        generic_name_matches = nickname_text_skeleton(
            observation.pokemon_name
        ) == expected_skeleton
        fingerprint_gap = fingerprint_distance(
            observation.page_fingerprint,
            baseline.page_fingerprint,
        )
        matched = (
            wide_name_matches
            and (generic_name_matches or not require_generic_name)
            and current_hp is not None
            and baseline_hp is not None
            and current_hp == baseline_hp
            and fingerprint_gap <= self._config.duplicate_distance_threshold
        )
        if not matched:
            return None
        return SummaryIdentity(
            pokemon_name=(
                baseline.pokemon_name
                if require_generic_name
                else observation.pokemon_name
            ),
            cp=baseline.cp,
            page_fingerprint=observation.page_fingerprint,
            hp_text=current_hp,
        )

    def scan(
        self,
        *,
        limit: int,
        csv_path: Path,
        debug: bool,
        resume: bool,
        delay_seconds: float,
        rename_with_iv: bool = False,
        serial_number: str | None = None,
    ) -> BatchScanResult:
        """Run the proven one-scan workflow repeatedly with guarded switching."""

        if limit <= 0:
            raise BatchAutomationError("--limit must be greater than 0.")
        if delay_seconds < 0:
            raise BatchAutomationError("--delay cannot be negative.")
        store = _BatchCsvStore(
            csv_path,
            resume=resume,
            rename_with_iv=rename_with_iv,
        )
        if len(store.rows) >= limit:
            return BatchScanResult(
                csv_path=store.path,
                rows=tuple(store.rows),
                new_rows=(),
                stop_reason="limit_reached",
            )

        device = self._adb.resolve_device(serial_number)
        first_identity = (
            self._completed_identity_from_row(store.rows[0]) if store.rows else None
        )
        new_rows: list[BatchCsvRow] = []
        if resume and store.rows:
            self._prepare_resume_position(
                device.serial_number,
                store.rows[-1],
                debug=debug,
            )

        while len(store.rows) < limit:
            scan = self._scanner.scan_one(
                debug=debug,
                rename_with_iv=rename_with_iv,
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
            summary_detection = self._detector.detect(
                summary_png,
                expected=("detail_summary",),
            )
            current_identity = self._identity_from_recognition_or_cp(
                summary_detection,
                summary_png,
                scan.recognition,
            )
            completed_identity = current_identity
            if rename_with_iv:
                completed_identity = self._verified_renamed_identity(
                    current_identity,
                    scan,
                    debug=debug,
                )
            if store.rows:
                recorder = _BatchDebugRecorder(scan.scan_directory, debug)
                _, same_as_last = self._compare_with_row(
                    summary_png,
                    current_identity,
                    store.rows[-1],
                    recorder,
                    prefix="append_guard",
                )
                if same_as_last:
                    raise BatchAutomationError(
                        "The completed summary is an adjacent duplicate of the last "
                        "CSV row. The new row was not appended."
                    )
            row = self._row(
                len(store.rows) + 1,
                scan,
                current_identity.page_fingerprint,
                rename_with_iv=rename_with_iv,
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
            if first_identity is None:
                first_identity = completed_identity

            if delay_seconds:
                self._sleeper(delay_seconds)
            switched = self._switch_to_next(
                device.serial_number,
                completed_identity,
                scan.scan_directory,
                debug=debug,
                expected_nickname=(
                    scan.nickname_change.expected_nickname
                    if rename_with_iv and scan.nickname_change is not None
                    else None
                ),
            )
            next_identity = switched.identity
            if next_identity is None:
                raise AssertionError("A completed switch did not return summary identity.")
            wrapped = _same_switch_identity(
                next_identity,
                first_identity,
                self._config,
            )
            first_row = store.rows[0]
            wrap_observed_nickname: str | None = None
            if rename_with_iv:
                if (
                    first_row.rename_status != "verified"
                    or first_row.nickname_after is None
                ):
                    raise BatchAutomationError(
                        "Rename batch first row is missing verified nickname evidence."
                    )
                wrap_observed_nickname = self._detector.read_summary_nickname(
                    switched.screenshot
                )
                wrapped = matches_verified_renamed_identity(
                    next_identity,
                    first_identity,
                    expected_nickname=first_row.nickname_after,
                    observed_nickname=wrap_observed_nickname,
                    config=self._config,
                )
            wrap_recorder = _BatchDebugRecorder(scan.scan_directory, debug)
            wrap_recorder.json(
                "wrap_comparison_state.json",
                {
                    "first_identity": asdict(first_identity),
                    "next_identity": asdict(next_identity),
                    "expected_nickname": (
                        first_row.nickname_after if rename_with_iv else None
                    ),
                    "observed_nickname": wrap_observed_nickname,
                    "wrapped_to_first": wrapped,
                },
            )
            if wrapped:
                return BatchScanResult(
                    csv_path=store.path,
                    rows=tuple(store.rows),
                    new_rows=tuple(new_rows),
                    stop_reason="wrapped_to_first",
                )

        raise AssertionError("Batch loop terminated without a stop reason.")

    def _verified_renamed_identity(
        self,
        before: SummaryIdentity,
        scan: AutoScanResult,
        *,
        debug: bool,
    ) -> SummaryIdentity:
        change = scan.nickname_change
        recognition = scan.recognition
        if change is None or recognition is None:
            raise BatchAutomationError(
                "The one-Pokémon scan did not return verified nickname evidence."
            )
        if change.nickname_before != (recognition.pokemon_name.value or ""):
            raise BatchAutomationError(
                "Verified nickname evidence does not match the recognized original name."
            )
        original_summary_path = scan.scan_directory / "summary.png"
        saved_summary_path = scan.scan_directory / "renamed_summary.png"
        saved_evidence_path = scan.scan_directory / "nickname_change.json"
        try:
            original_summary = original_summary_path.read_bytes()
            saved_summary = saved_summary_path.read_bytes()
            saved_evidence = json.loads(saved_evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BatchAutomationError(
                f"Verified nickname artifacts were not durable: {error}"
            ) from error
        if (
            saved_summary != change.summary_png
            or not isinstance(saved_evidence, dict)
            or saved_evidence.get("status") != "verified"
            or saved_evidence.get("nickname_after") != change.expected_nickname
            or saved_evidence.get("editor_observed_nickname")
            != change.editor_observed_nickname
        ):
            raise BatchAutomationError(
                "Verified nickname artifacts do not match the in-memory result."
            )
        values = (recognition.attack_iv, recognition.defense_iv, recognition.hp_iv)
        if any(value is None for value in values):
            raise BatchAutomationError("Verified nickname evidence is missing an IV value.")
        suffix = "/".join(str(value) for value in values)
        if not change.expected_nickname.endswith(suffix):
            raise BatchAutomationError(
                "Verified nickname evidence does not contain the recognized half-width IV suffix."
            )
        recorder = _BatchDebugRecorder(scan.scan_directory, debug)
        cp_source = "summary_ocr"
        try:
            after = summary_identity(
                change.summary_detection,
                change.summary_png,
                self._config,
            )
        except BatchAutomationError:
            fallback_after = self._identity_from_strong_cp_fallback(
                change.summary_detection,
                change.summary_png,
                before,
                baseline_png=original_summary,
                expected_nickname=change.expected_nickname,
                observed_nickname=change.summary_observed_nickname,
                require_generic_name=False,
            )
            cp_source = "exact_nickname_hp_fingerprint_fallback"
            if fallback_after is None:
                raise BatchAutomationError(
                    "Verified rename could not read CP and did not preserve exact "
                    "nickname, HP, and static-fingerprint evidence. No row was "
                    "appended and no swipe was sent."
                ) from None
            after = fallback_after
        transition = ExpectedNicknameTransition(
            before=before,
            after=after,
            expected_nickname=change.expected_nickname,
            editor_observed_nickname=change.editor_observed_nickname,
            summary_observed_nickname=change.summary_observed_nickname,
        )
        recorder.screen("rename_verified_summary", change.summary_png)
        recorder.json(
            "rename_transition_state.json",
            {
                "before": asdict(before),
                "after": asdict(after),
                "expected_nickname": change.expected_nickname,
                "editor_observed_nickname": change.editor_observed_nickname,
                "summary_observed_nickname": change.summary_observed_nickname,
                "cp_source": cp_source,
                "fingerprint_distance": fingerprint_distance(
                    before.page_fingerprint,
                    after.page_fingerprint,
                ),
                "matched": matches_expected_nickname_transition(
                    transition,
                    self._config,
                ),
            },
        )
        if not matches_expected_nickname_transition(transition, self._config):
            raise BatchAutomationError(
                "Expected nickname transition failed: nickname, CP, HP, or static "
                "fingerprint did not preserve the same Pokémon. No row was appended "
                "and no swipe was sent."
            )
        return after

    def _completed_identity_from_row(self, row: BatchCsvRow) -> SummaryIdentity | None:
        if row.rename_status != "verified":
            return self._identity_from_row(row)
        if row.nickname_after is None:
            raise BatchAutomationError(
                f"Resume row {row.scan_id} is missing its post-rename nickname."
            )
        pre_path = row.scan_directory / "summary.png"
        post_path = row.scan_directory / "renamed_summary.png"
        evidence_path = row.scan_directory / "nickname_change.json"
        try:
            pre_png = pre_path.read_bytes()
            post_png = post_path.read_bytes()
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise BatchAutomationError(
                f"Could not restore renamed identity for row {row.scan_id}: {error}"
            ) from error
        pre_detection = self._detector.detect(
            pre_png,
            expected=("detail_summary",),
        )
        pre = self._identity_from_saved_row(pre_detection, pre_png, row)
        post_detection = self._detector.detect(
            post_png,
            expected=("detail_summary",),
        )
        editor_observed = evidence.get("editor_observed_nickname")
        if not isinstance(editor_observed, str):
            raise BatchAutomationError(
                f"Resume row {row.scan_id} is missing exact editor nickname evidence."
            )
        summary_observed = self._detector.read_summary_nickname(post_png)
        try:
            post = summary_identity(post_detection, post_png, self._config)
        except BatchAutomationError:
            fallback_post = self._identity_from_strong_cp_fallback(
                post_detection,
                post_png,
                pre,
                baseline_png=pre_png,
                expected_nickname=row.nickname_after,
                observed_nickname=summary_observed,
                require_generic_name=False,
            )
            if fallback_post is None:
                raise BatchAutomationError(
                    f"Resume row {row.scan_id} could not restore CP and did not "
                    "preserve exact nickname, HP, and static-fingerprint evidence."
                ) from None
            post = fallback_post
        transition = ExpectedNicknameTransition(
            before=pre,
            after=post,
            expected_nickname=row.nickname_after,
            editor_observed_nickname=editor_observed,
            summary_observed_nickname=summary_observed,
        )
        if not matches_expected_nickname_transition(transition, self._config):
            raise BatchAutomationError(
                f"Resume row {row.scan_id} failed its saved nickname transition check."
            )
        return post

    def _prepare_resume_position(
        self,
        serial: str,
        last_row: BatchCsvRow,
        *,
        debug: bool,
    ) -> None:
        recorder = _BatchDebugRecorder(last_row.scan_directory, debug)
        current_png = self._adb.capture_screen(serial)
        recorder.screen("resume_current", current_png)
        current_detection = self._detector.detect(
            current_png,
            expected=("detail_summary",),
        )
        if current_detection.state != "detail_summary":
            recorder.json(
                "resume_state.json",
                {
                    "state": current_detection.to_json_data(),
                    "same_as_last": None,
                    "resume_pre_switch_executed": False,
                },
            )
            raise BatchAutomationError(
                "Resume requires the current phone page to be detail_summary; "
                f"detected {current_detection.state}. No swipe was sent."
            )
        observed_nickname = self._detector.read_summary_nickname(current_png)
        current_observation = resume_observation(
            current_detection,
            current_png,
            observed_nickname=observed_nickname,
            config=self._config,
        )
        expected_name = (
            last_row.nickname_after
            if last_row.rename_status == "verified"
            else last_row.pokemon_name
        )
        if not expected_name:
            raise BatchAutomationError(
                "Resume CSV last row has no reliable name or verified nickname."
            )
        expected_skeleton = nickname_text_skeleton(expected_name)
        generic_name_matches = nickname_text_skeleton(
            current_observation.pokemon_name
        ) == expected_skeleton
        wide_name_matches = (
            current_observation.observed_nickname is not None
            and nickname_text_skeleton(current_observation.observed_nickname)
            == expected_skeleton
        )
        clearly_different_name = (
            not generic_name_matches
            and current_observation.observed_nickname is not None
            and not wide_name_matches
        )
        if clearly_different_name:
            validated_previous, _ = self._resume_fallback_baseline(last_row)
            recorder.json(
                "resume_state.json",
                {
                    "current_observation": asdict(current_observation),
                    "expected_name": expected_name,
                    "last_identity": asdict(validated_previous),
                    "name_relation": "different",
                    "same_as_last": False,
                    "cp_retry_attempts": [],
                    "resume_pre_switch_executed": False,
                },
            )
            return

        cp_retry_attempts: list[dict[str, object]] = []
        if current_observation.cp is None:
            current_observation, cp_retry_attempts = self._retry_resume_cp(
                serial,
                current_observation,
                recorder,
            )

        if current_observation.cp is None:
            previous_identity, previous_png = self._resume_fallback_baseline(last_row)
            previous_hp = self._detector.read_summary_hp(previous_png)
            previous_identity = replace(previous_identity, hp_text=previous_hp)
            fingerprint_gap = fingerprint_distance(
                current_observation.page_fingerprint,
                previous_identity.page_fingerprint,
            )
            strong_fallback = (
                generic_name_matches
                and wide_name_matches
                and current_observation.hp_text is not None
                and previous_identity.hp_text is not None
                and current_observation.hp_text == previous_identity.hp_text
                and fingerprint_gap <= self._config.duplicate_distance_threshold
            )
            state_data: dict[str, object] = {
                "current_observation": asdict(current_observation),
                "expected_name": expected_name,
                "name_relation": "same",
                "last_identity": asdict(previous_identity),
                "fingerprint_distance": fingerprint_gap,
                "duplicate_distance_threshold": self._config.duplicate_distance_threshold,
                "strong_cp_missing_fallback": strong_fallback,
                "same_as_last": strong_fallback,
                "cp_retry_attempts": cp_retry_attempts,
                "resume_pre_switch_executed": strong_fallback,
            }
            recorder.json("resume_state.json", state_data)
            if not strong_fallback:
                raise BatchAutomationError(
                    "Resume could not read CP and did not have exact name/nickname, "
                    "HP, and static-fingerprint evidence for the last Pokémon. "
                    "No swipe or scan was sent."
                )
            switched = self._switch_after_resume_fallback(
                serial,
                previous_identity,
                current_observation,
                last_row.scan_directory,
                baseline_png=previous_png,
                expected_nickname=expected_name,
                require_generic_name=last_row.rename_status != "verified",
                debug=debug,
            )
            if switched.identity is None:
                raise AssertionError("A completed resume switch has no summary identity.")
            state_data["next_identity"] = asdict(switched.identity)
            recorder.json("resume_state.json", state_data)
            return

        current_identity = SummaryIdentity(
            pokemon_name=current_observation.pokemon_name,
            cp=current_observation.cp,
            page_fingerprint=current_observation.page_fingerprint,
            hp_text=current_observation.hp_text,
        )
        resume_observed_nickname = current_observation.observed_nickname
        if last_row.rename_status == "verified":
            restored_identity = self._completed_identity_from_row(last_row)
            if restored_identity is None or last_row.nickname_after is None:
                raise BatchAutomationError(
                    "Resume could not restore the last verified renamed identity."
                )
            previous_identity = restored_identity
            nickname_matches = nickname_text_skeleton(
                resume_observed_nickname or ""
            ) == (
                nickname_text_skeleton(last_row.nickname_after)
            )
            same_as_last = nickname_matches and same_static_summary(
                current_identity,
                previous_identity,
                self._config,
            )
            immutable_match = _same_immutable_summary(
                current_identity,
                previous_identity,
                self._config,
            )
            recorder.screen(
                "resume_current_static_roi",
                static_summary_crop(current_png, self._config),
            )
            recorder.screen(
                "resume_last_static_roi",
                static_summary_crop(
                    (last_row.scan_directory / "renamed_summary.png").read_bytes(),
                    self._config,
                ),
            )
            if immutable_match and not same_as_last:
                recorder.json(
                    "resume_state.json",
                    {
                        **self._comparison_data(
                            current_identity,
                            previous_identity,
                            False,
                        ),
                        "expected_nickname": last_row.nickname_after,
                        "observed_nickname": resume_observed_nickname,
                        "immutable_match": True,
                        "resume_pre_switch_executed": False,
                    },
                )
                raise BatchAutomationError(
                    "Resume found the last Pokémon's CP/HP/fingerprint but not its "
                    "verified expected nickname. The state is ambiguous; no swipe or "
                    "scan was sent."
                )
        else:
            previous_identity, same_as_last = self._compare_with_row(
                current_png,
                current_identity,
                last_row,
                recorder,
                prefix="resume",
            )
        state_data = self._comparison_data(
            current_identity,
            previous_identity,
            same_as_last,
        )
        if last_row.rename_status == "verified":
            state_data["expected_nickname"] = last_row.nickname_after
            state_data["observed_nickname"] = resume_observed_nickname
        state_data["current_observation"] = asdict(current_observation)
        state_data["cp_retry_attempts"] = cp_retry_attempts
        state_data["resume_pre_switch_executed"] = same_as_last
        recorder.json("resume_state.json", state_data)
        if same_as_last:
            switched = self._switch_to_next(
                serial,
                previous_identity,
                last_row.scan_directory,
                debug=debug,
                expected_nickname=(
                    last_row.nickname_after
                    if last_row.rename_status == "verified"
                    else None
                ),
            )
            if switched.identity is None:
                raise AssertionError("A completed resume switch has no summary identity.")
            state_data["next_identity"] = asdict(switched.identity)
            recorder.json("resume_state.json", state_data)

    def _retry_resume_cp(
        self,
        serial: str,
        observation: ResumeObservation,
        recorder: _BatchDebugRecorder,
    ) -> tuple[ResumeObservation, list[dict[str, object]]]:
        attempts: list[dict[str, object]] = []
        for attempt in range(1, self._config.resume_cp_retry_frames + 1):
            self._sleeper(self._config.resume_cp_retry_interval_seconds)
            screenshot = self._adb.capture_screen(serial)
            recorder.screen(f"resume_cp_retry_{attempt}", screenshot)
            retry_fingerprint = page_fingerprint(screenshot, self._config)
            distance = fingerprint_distance(
                observation.page_fingerprint,
                retry_fingerprint,
            )
            if distance > self._config.duplicate_distance_threshold:
                attempts.append(
                    {
                        "attempt": attempt,
                        "cp": None,
                        "fingerprint_distance": distance,
                        "page_changed": True,
                    }
                )
                recorder.json("resume_cp_retry_state.json", {"attempts": attempts})
                raise BatchAutomationError(
                    "Resume page changed during CP-only retry. No swipe or scan was sent."
                )
            cp = self._detector.read_summary_cp(screenshot)
            attempts.append(
                {
                    "attempt": attempt,
                    "cp": cp,
                    "fingerprint_distance": distance,
                    "page_changed": False,
                }
            )
            recorder.json("resume_cp_retry_state.json", {"attempts": attempts})
            if cp is not None:
                return replace(observation, cp=cp), attempts
        return observation, attempts

    def _resume_fallback_baseline(
        self,
        row: BatchCsvRow,
    ) -> tuple[SummaryIdentity, bytes]:
        path = row.scan_directory / (
            "renamed_summary.png" if row.rename_status == "verified" else "summary.png"
        )
        try:
            png_bytes = path.read_bytes()
        except OSError as error:
            raise BatchAutomationError(
                f"Could not read resume baseline screenshot '{path}': {error}"
            ) from error
        if row.rename_status == "verified":
            identity = self._completed_identity_from_row(row)
        else:
            detection = self._detector.detect(
                png_bytes,
                expected=("detail_summary",),
            )
            identity = self._identity_from_saved_row(detection, png_bytes, row)
        if identity is None:
            raise BatchAutomationError("Resume could not restore the last identity.")
        return identity, png_bytes

    def _switch_after_resume_fallback(
        self,
        serial: str,
        previous: SummaryIdentity,
        observation: ResumeObservation,
        scan_directory: Path,
        *,
        baseline_png: bytes,
        expected_nickname: str,
        require_generic_name: bool,
        debug: bool,
    ) -> _SwitchWaitResult:
        """Send one switch after the dedicated no-CP resume proof succeeds."""

        recorder = _BatchDebugRecorder(scan_directory, debug)
        gesture = self._config.next_pokemon
        recorder.json(
            "resume_fallback_switch_action.json",
            {
                "kind": "swipe",
                "coordinates": asdict(gesture),
                "before": asdict(observation),
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
            1,
            baseline_png=baseline_png,
            expected_nickname=expected_nickname,
            require_generic_name=require_generic_name,
        )
        recorder.screen("after_resume_fallback_switch", result.screenshot)
        recorder.json(
            "resume_fallback_switch_state.json",
            {
                "outcome": result.outcome,
                "elapsed_seconds": result.elapsed_seconds,
                "detection": result.detection.to_json_data(),
                "identity": asdict(result.identity) if result.identity is not None else None,
            },
        )
        if result.outcome == "changed" and result.identity is not None:
            return result
        raise BatchAutomationError(
            "The CP-missing resume fallback sent one verified left swipe but did "
            "not confirm a different Pokémon. No retry swipe was sent."
        )

    def _compare_with_row(
        self,
        current_png: bytes,
        current_identity: SummaryIdentity,
        previous_row: BatchCsvRow,
        recorder: _BatchDebugRecorder,
        *,
        prefix: str,
    ) -> tuple[SummaryIdentity, bool]:
        previous_path = previous_row.scan_directory / "summary.png"
        try:
            previous_png = previous_path.read_bytes()
        except OSError as error:
            raise BatchAutomationError(
                f"Could not read previous summary screenshot '{previous_path}': {error}"
            ) from error
        previous_detection = self._detector.detect(
            previous_png,
            expected=("detail_summary",),
        )
        previous_identity = self._identity_from_saved_row(
            previous_detection,
            previous_png,
            previous_row,
        )
        same_as_last = same_static_summary(
            current_identity,
            previous_identity,
            self._config,
        )
        recorder.text(
            f"{prefix}_last_summary_path.txt",
            str(previous_path.resolve()),
        )
        recorder.screen(
            f"{prefix}_current_static_roi",
            static_summary_crop(current_png, self._config),
        )
        recorder.screen(
            f"{prefix}_last_static_roi",
            static_summary_crop(previous_png, self._config),
        )
        if prefix != "resume":
            recorder.screen(f"{prefix}_current", current_png)
        recorder.json(
            f"{prefix}_state.json",
            self._comparison_data(
                current_identity,
                previous_identity,
                same_as_last,
            ),
        )
        return previous_identity, same_as_last

    def _comparison_data(
        self,
        current: SummaryIdentity,
        previous: SummaryIdentity,
        same_as_last: bool,
    ) -> dict[str, object]:
        return {
            "static_roi": list(self._config.fingerprint_crop),
            "current_fingerprint": current.page_fingerprint,
            "last_fingerprint": previous.page_fingerprint,
            "fingerprint_distance": fingerprint_distance(
                current.page_fingerprint, previous.page_fingerprint
            ),
            "duplicate_distance_threshold": self._config.duplicate_distance_threshold,
            "current_ocr": {
                "pokemon_name": current.pokemon_name,
                "cp": current.cp,
                "hp": current.hp_text,
            },
            "last_ocr": {
                "pokemon_name": previous.pokemon_name,
                "cp": previous.cp,
                "hp": previous.hp_text,
            },
            "same_as_last": same_as_last,
        }

    def _switch_to_next(
        self,
        serial: str,
        previous: SummaryIdentity,
        scan_directory: Path,
        *,
        debug: bool,
        expected_nickname: str | None = None,
    ) -> _SwitchWaitResult:
        recorder = _BatchDebugRecorder(scan_directory, debug)
        fallback_name = expected_nickname or previous.pokemon_name
        require_generic_name = expected_nickname is None
        baseline_path = scan_directory / (
            "summary.png" if require_generic_name else "renamed_summary.png"
        )
        try:
            baseline_png = baseline_path.read_bytes()
        except OSError as error:
            raise BatchAutomationError(
                f"Could not read switch baseline '{baseline_path}': {error}"
            ) from error
        before = self._adb.capture_screen(serial)
        recorder.screen("before_switch", before)
        before_detection = self._detector.detect(
            before,
            expected=("detail_summary",),
        )
        recorder.json("before_switch_state.json", before_detection.to_json_data())
        current: SummaryIdentity | None = None
        strong_cp_fallback = False
        try:
            current = summary_identity(before_detection, before, self._config)
        except BatchAutomationError:
            current, strong_cp_fallback = (
                self._known_identity_switch_precheck(
                    serial,
                    before,
                    before_detection,
                    previous,
                    fallback_name,
                    scan_directory,
                    recorder,
                    require_generic_name=require_generic_name,
                )
            )
        if current is not None and not _same_switch_identity(
            current,
            previous,
            self._config,
        ):
            raise BatchAutomationError(
                "The detail page changed after the one-Pokémon scan; no swipe was sent."
            )

        if strong_cp_fallback:
            return self._switch_after_cp_fallback(
                serial,
                previous,
                fallback_name,
                recorder,
                baseline_png=baseline_png,
                require_generic_name=require_generic_name,
            )
        if current is None:
            raise AssertionError("A strict switch precheck returned no identity.")

        gestures = (
            self._config.next_pokemon,
            self._config.retry_next_pokemon,
        )
        for attempt, gesture in enumerate(gestures, start=1):
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
                baseline_png=baseline_png,
                expected_nickname=fallback_name,
                require_generic_name=require_generic_name,
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
                    "cp_fallback_used": result.cp_fallback_used,
                    "detection": result.detection.to_json_data(),
                    "identity": (asdict(result.identity) if result.identity is not None else None),
                },
            )
            if result.outcome == "changed" and result.identity is not None:
                return result
            if result.outcome == "unexpected":
                raise BatchAutomationError(
                    "Unexpected page state while switching Pokémon: "
                    f"{result.detection.state}. Batch stopped."
                )
            if result.outcome == "cp_unreadable":
                raise BatchAutomationError(
                    "The switched detail page still had unreadable CP and did not "
                    "match the prior page through exact name, HP, and static "
                    "fingerprint. No retry swipe was sent."
                )
            if result.cp_fallback_used and attempt < len(gestures):
                raise BatchAutomationError(
                    "The first swipe remained on a page verified only through the "
                    "CP-missing name, HP, and fingerprint fallback. No stronger "
                    "retry swipe was sent."
                )
            if attempt < len(gestures):
                confirmation = self._adb.capture_screen(serial)
                recorder.screen(
                    f"before_switch_attempt_{attempt + 1}",
                    confirmation,
                )
                confirmation_detection = self._detector.detect(
                    confirmation,
                    expected=("detail_summary",),
                )
                confirmation_used_cp_fallback = False
                try:
                    current = summary_identity(
                        confirmation_detection,
                        confirmation,
                        self._config,
                    )
                except BatchAutomationError:
                    observed_nickname = self._detector.read_summary_nickname(
                        confirmation
                    )
                    current = self._identity_from_strong_cp_fallback(
                        confirmation_detection,
                        confirmation,
                        previous,
                        baseline_png=baseline_png,
                        expected_nickname=fallback_name,
                        observed_nickname=observed_nickname,
                        require_generic_name=require_generic_name,
                    )
                    if current is None:
                        raise BatchAutomationError(
                            "The confirmation page still had unreadable CP and did "
                            "not preserve exact name, HP, and static-fingerprint "
                            "evidence. No retry swipe was sent."
                        ) from None
                    confirmation_used_cp_fallback = True
                if not _same_switch_identity(current, previous, self._config):
                    return _SwitchWaitResult(
                        confirmation,
                        confirmation_detection,
                        current,
                        "changed",
                        0.0,
                    )
                if confirmation_used_cp_fallback:
                    raise BatchAutomationError(
                        "The retry confirmation matched only through the CP-missing "
                        "name, HP, and fingerprint fallback. No stronger retry "
                        "swipe was sent."
                    )

        raise BatchAutomationError(
            "Two left swipes did not reach a different Pokémon within 15 seconds "
            "each. The list may be at its end or the gesture did not take effect. "
            "Batch stopped safely."
        )

    def _known_identity_switch_precheck(
        self,
        serial: str,
        before: bytes,
        detection: PageDetection,
        previous: SummaryIdentity,
        expected_nickname: str,
        scan_directory: Path,
        recorder: _BatchDebugRecorder,
        *,
        require_generic_name: bool,
    ) -> tuple[SummaryIdentity | None, bool]:
        """Retry CP, then require exact name, HP, and static ROI before one swipe."""

        observation = partial_summary_observation(
            detection,
            before,
            self._config,
        )
        attempts: list[dict[str, object]] = []
        fallback_screen = before
        fallback_fingerprint = observation.page_fingerprint
        for attempt in range(1, self._config.verified_switch_cp_retry_frames + 1):
            self._sleeper(self._config.verified_switch_cp_retry_interval_seconds)
            screenshot = self._adb.capture_screen(serial)
            recorder.screen(f"before_switch_cp_retry_{attempt}", screenshot)
            retry_fingerprint = page_fingerprint(screenshot, self._config)
            previous_frame_distance = fingerprint_distance(
                observation.page_fingerprint,
                retry_fingerprint,
            )
            baseline_distance = fingerprint_distance(
                previous.page_fingerprint,
                retry_fingerprint,
            )
            fallback_screen = screenshot
            fallback_fingerprint = retry_fingerprint
            if (
                previous_frame_distance > self._config.duplicate_distance_threshold
                or baseline_distance > self._config.duplicate_distance_threshold
            ):
                attempts.append(
                    {
                        "attempt": attempt,
                        "cp": None,
                        "previous_frame_fingerprint_distance": previous_frame_distance,
                        "baseline_fingerprint_distance": baseline_distance,
                        "page_changed": True,
                    }
                )
                recorder.json(
                    "before_switch_cp_retry_state.json",
                    {"attempts": attempts},
                )
                raise BatchAutomationError(
                    "The detail page changed during the identity CP retry; "
                    "no swipe was sent."
                )
            cp = self._detector.read_summary_cp(screenshot)
            attempts.append(
                {
                    "attempt": attempt,
                    "cp": cp,
                    "previous_frame_fingerprint_distance": previous_frame_distance,
                    "baseline_fingerprint_distance": baseline_distance,
                    "page_changed": False,
                }
            )
            recorder.json(
                "before_switch_cp_retry_state.json",
                {"attempts": attempts},
            )
            if cp is not None:
                return (
                    SummaryIdentity(
                        pokemon_name=(
                            previous.pokemon_name
                            if require_generic_name
                            else observation.pokemon_name
                        ),
                        cp=cp,
                        page_fingerprint=retry_fingerprint,
                        hp_text=observation.hp_text,
                    ),
                    False,
                )

        baseline_path = scan_directory / (
            "summary.png" if require_generic_name else "renamed_summary.png"
        )
        try:
            baseline_png = baseline_path.read_bytes()
        except OSError as error:
            raise BatchAutomationError(
                f"Could not read switch fallback baseline '{baseline_path}': {error}"
            ) from error
        baseline_hp = self._detector.read_summary_hp(baseline_png)
        observed_nickname = self._detector.read_summary_nickname(fallback_screen)
        current_hp = self._detector.read_summary_hp(fallback_screen)
        distance = fingerprint_distance(
            fallback_fingerprint,
            previous.page_fingerprint,
        )
        nickname_matches = nickname_text_skeleton(observed_nickname) == (
            nickname_text_skeleton(expected_nickname)
        )
        generic_name_matches = nickname_text_skeleton(
            observation.pokemon_name
        ) == nickname_text_skeleton(expected_nickname)
        strong_fallback = (
            nickname_matches
            and (generic_name_matches or not require_generic_name)
            and current_hp is not None
            and baseline_hp is not None
            and current_hp == baseline_hp
            and distance <= self._config.duplicate_distance_threshold
        )
        fallback_state_name = (
            "before_switch_cp_fallback_state.json"
            if require_generic_name
            else "before_switch_verified_rename_fallback_state.json"
        )
        recorder.json(
            fallback_state_name,
            {
                "expected_nickname": expected_nickname,
                "observed_nickname": observed_nickname,
                "nickname_matches": nickname_matches,
                "generic_name_matches": generic_name_matches,
                "generic_name_required": require_generic_name,
                "current_hp": current_hp,
                "baseline_hp": baseline_hp,
                "hp_matches": (
                    current_hp is not None
                    and baseline_hp is not None
                    and current_hp == baseline_hp
                ),
                "current_fingerprint": fallback_fingerprint,
                "baseline_fingerprint": previous.page_fingerprint,
                "fingerprint_distance": distance,
                "duplicate_distance_threshold": self._config.duplicate_distance_threshold,
                "cp_retry_attempts": attempts,
                "matched": strong_fallback,
            },
        )
        if not strong_fallback:
            raise BatchAutomationError(
                "Before-switch CP retry failed and the page did not have exact "
                "name/nickname, HP, and static-fingerprint evidence. No swipe was sent."
            )
        return None, True

    def _switch_after_cp_fallback(
        self,
        serial: str,
        previous: SummaryIdentity,
        expected_nickname: str,
        recorder: _BatchDebugRecorder,
        *,
        baseline_png: bytes,
        require_generic_name: bool,
    ) -> _SwitchWaitResult:
        """Send only one swipe after a strong CP-missing identity proof."""

        gesture = self._config.next_pokemon
        artifact_prefix = (
            "cp_fallback" if require_generic_name else "verified_rename_fallback"
        )
        recorder.json(
            f"{artifact_prefix}_switch_action.json",
            {
                "kind": "swipe",
                "coordinates": asdict(gesture),
                "expected_nickname": expected_nickname,
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
            1,
            baseline_png=baseline_png,
            expected_nickname=expected_nickname,
            require_generic_name=require_generic_name,
        )
        recorder.screen(f"after_{artifact_prefix}_switch", result.screenshot)
        recorder.json(
            f"{artifact_prefix}_switch_state.json",
            {
                "outcome": result.outcome,
                "elapsed_seconds": result.elapsed_seconds,
                "detection": result.detection.to_json_data(),
                "identity": (
                    asdict(result.identity) if result.identity is not None else None
                ),
            },
        )
        if result.outcome == "changed" and result.identity is not None:
            return result
        raise BatchAutomationError(
            "The CP-missing identity fallback sent one left swipe but did "
            "not confirm a different Pokémon. No retry swipe was sent."
        )

    def _wait_for_next_summary(
        self,
        serial: str,
        previous: SummaryIdentity,
        recorder: _BatchDebugRecorder,
        attempt: int,
        *,
        baseline_png: bytes,
        expected_nickname: str,
        require_generic_name: bool,
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
        last_cp_unreadable = False
        cp_fallback_used = False

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
            cp_fallback = False
            if detection.state == "detail_summary":
                try:
                    identity = summary_identity(detection, screenshot, self._config)
                except BatchAutomationError:
                    observed_nickname = self._detector.read_summary_nickname(screenshot)
                    identity = self._identity_from_strong_cp_fallback(
                        detection,
                        screenshot,
                        previous,
                        baseline_png=baseline_png,
                        expected_nickname=expected_nickname,
                        observed_nickname=observed_nickname,
                        require_generic_name=require_generic_name,
                    )
                    cp_fallback = identity is not None
                    cp_fallback_used = cp_fallback_used or cp_fallback
            samples.append(
                {
                    "sample": sample_index,
                    "elapsed_seconds": elapsed,
                    "state": detection.state,
                    "confidence": detection.confidence,
                    "matched_texts": list(detection.matched_texts),
                    "identity": asdict(identity) if identity is not None else None,
                    "cp_fallback": cp_fallback,
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
                    "cp_fallback_used": cp_fallback_used,
                },
            )
            last_screen = screenshot
            last_detection = detection
            last_identity = identity
            last_cp_unreadable = detection.state == "detail_summary" and identity is None
            if identity is not None and not _same_switch_identity(
                identity,
                previous,
                self._config,
            ):
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
                try:
                    last_identity = summary_identity(
                        last_detection,
                        last_screen,
                        self._config,
                    )
                except BatchAutomationError:
                    observed_nickname = self._detector.read_summary_nickname(last_screen)
                    last_identity = self._identity_from_strong_cp_fallback(
                        last_detection,
                        last_screen,
                        previous,
                        baseline_png=baseline_png,
                        expected_nickname=expected_nickname,
                        observed_nickname=observed_nickname,
                        require_generic_name=require_generic_name,
                    )
                    cp_fallback_used = cp_fallback_used or last_identity is not None
                    last_cp_unreadable = last_identity is None
        if last_detection.state not in ("detail_summary", "unknown"):
            return _SwitchWaitResult(
                last_screen,
                last_detection,
                last_identity,
                "unexpected",
                timeout,
            )
        if last_cp_unreadable:
            return _SwitchWaitResult(
                last_screen,
                last_detection,
                None,
                "cp_unreadable",
                timeout,
                cp_fallback_used,
            )
        return _SwitchWaitResult(
            last_screen,
            last_detection,
            last_identity,
            "same",
            timeout,
            cp_fallback_used,
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
        *,
        rename_with_iv: bool,
    ) -> BatchCsvRow:
        recognition = scan.recognition
        if recognition is None:
            raise BatchAutomationError("Missing recognition result for completed scan.")
        change: NicknameRenameResult | None = scan.nickname_change
        if rename_with_iv and change is None:
            raise BatchAutomationError("Missing verified nickname result for completed scan.")
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
            nickname_before=(
                change.nickname_before
                if change is not None
                else recognition.pokemon_name.value
            ),
            nickname_after=(change.expected_nickname if change is not None else None),
            rename_status=("verified" if change is not None else "not_requested"),
        )
