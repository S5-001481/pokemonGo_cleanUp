"""Safety-gated automation for one fixed Huawei Mate 30 scan."""

from __future__ import annotations

import io
import json
import logging
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, Protocol
from uuid import uuid4

from PIL import Image, ImageChops, ImageStat

from pokemon_go_cleanup import __version__
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import AutomationError, LocalStorageError, PokemonGoCleanupError
from pokemon_go_cleanup.models import Device, ScanManifest, ScanStep, ScreenResolution
from pokemon_go_cleanup.recognition import (
    HEIGHT,
    NAME_RECT,
    WIDTH,
    OcrCandidate,
    RecognitionResult,
    RecognitionService,
    normalize_cp_candidate,
    normalize_ocr_text,
)
from pokemon_go_cleanup.storage import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

PageState = Literal[
    "detail_summary",
    "detail_moves",
    "action_menu",
    "appraisal_dialogue",
    "appraisal_bars",
    "unknown",
]
ExpectedStates = tuple[PageState, ...]

MOVE_PAGE_EVIDENCE_RECT: Final = (100, 900, 1360, 1800)
SUMMARY_HP_RECT: Final = (500, 1520, 940, 1680)
_MOVE_PAGE_LABELS: Final = (
    "道館",
    "團體戰",
    "訓練家對戰",
    "新攻擊招式",
    "新攻撃招式",
)
_MOVE_EVIDENCE_MIN_CONFIDENCE: Final = 0.80


@dataclass(frozen=True, slots=True)
class Point:
    """One fixed Huawei screen coordinate."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Swipe:
    """One fixed Huawei swipe gesture."""

    start: Point
    end: Point
    duration_ms: int


@dataclass(frozen=True, slots=True)
class HuaweiMate30AutomationConfig:
    """Every coordinate and timeout used by the fixed 1440x3120 workflow."""

    width: int = WIDTH
    height: int = HEIGHT
    scroll_to_moves: Swipe = Swipe(Point(720, 2380), Point(720, 930), 650)
    menu_button: Point = Point(1244, 2772)
    appraisal_advance: Point = Point(1120, 1660)
    action_menu_rect: tuple[int, int, int, int] = (160, 1150, 1320, 2860)
    appraisal_target_rect: tuple[int, int, int, int] = (160, 1450, 1320, 2700)
    transfer_forbidden_rect: tuple[int, int, int, int] = (0, 2700, 1440, 3120)
    appraisal_dialogue_rect: tuple[int, int, int, int] = (100, 1250, 1340, 2860)
    menu_ocr_min_confidence: float = 0.85
    transfer_clearance_pixels: int = 150
    stable_interval_seconds: float = 0.3
    stable_difference_threshold: float = 0.008
    stable_consecutive_samples: int = 3
    step_timeout_seconds: float = 8.0
    summary_poll_interval_seconds: float = 0.5
    summary_wait_timeout_seconds: float = 15.0
    menu_poll_interval_seconds: float = 0.5
    menu_wait_timeout_seconds: float = 10.0
    max_menu_tap_attempts: int = 2
    moves_poll_interval_seconds: float = 0.5
    moves_wait_timeout_seconds: float = 15.0
    max_moves_swipe_attempts: int = 2
    total_timeout_seconds: float = 180.0


HUAWEI_MATE_30_AUTOMATION: Final = HuaweiMate30AutomationConfig()


@dataclass(frozen=True, slots=True)
class PageDetection:
    """One screen classification with OCR evidence and an optional safe target."""

    state: PageState
    confidence: float
    matched_texts: tuple[str, ...] = ()
    appraisal_target: Point | None = None
    details: dict[str, object] = field(default_factory=dict)

    def to_json_data(self) -> dict[str, object]:
        return {
            "state": self.state,
            "confidence": self.confidence,
            "matched_texts": list(self.matched_texts),
            "appraisal_target": (
                asdict(self.appraisal_target) if self.appraisal_target is not None else None
            ),
            "details": self.details,
        }


def match_move_name_power_rows(
    candidates: Sequence[OcrCandidate],
) -> tuple[tuple[OcrCandidate, OcrCandidate], ...]:
    """Match a move name with the damage number on the same fixed-layout row."""

    names: list[OcrCandidate] = []
    powers: list[OcrCandidate] = []
    for candidate in candidates:
        value = normalize_ocr_text(candidate.raw)
        if candidate.confidence < _MOVE_EVIDENCE_MIN_CONFIDENCE:
            continue
        if (
            candidate.box[0] < 800
            and any("\u4e00" <= character <= "\u9fff" for character in value)
            and not any(label in value for label in _MOVE_PAGE_LABELS)
        ):
            names.append(candidate)
        elif (
            candidate.box[0] >= 1050
            and re.fullmatch(
                r"\d{1,3}(?:\s*\+\s*\d{1,3})?",
                value,
            )
        ):
            powers.append(candidate)

    matches: list[tuple[OcrCandidate, OcrCandidate]] = []
    for name in names:
        name_center_y = (name.box[1] + name.box[3]) // 2
        same_row = [
            power
            for power in powers
            if abs((power.box[1] + power.box[3]) // 2 - name_center_y) <= 70
        ]
        if same_row:
            matches.append((name, max(same_row, key=lambda candidate: candidate.confidence)))
    return tuple(matches)


@dataclass(frozen=True, slots=True)
class StableScreenResult:
    """The final stable screenshot and all measured differences."""

    png_bytes: bytes
    differences: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _StateWaitResult:
    """Final screenshot, detection, and outcome from one target-state attempt."""

    png_bytes: bytes
    detection: PageDetection
    outcome: Literal["reached", "timeout", "unexpected"]
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class AutoScanResult:
    """Outcome of a live run or a non-mutating dry run."""

    scan_directory: Path
    manifest_path: Path
    manifest: ScanManifest
    recognition: RecognitionResult | None
    dry_run: bool
    planned_actions: tuple[dict[str, object], ...]


class AutomationAdbGateway(Protocol):
    """ADB behavior used by the one-Pokémon automation state machine."""

    def resolve_device(self, serial_number: str | None = None) -> Device: ...
    def get_resolution(self, serial_number: str) -> ScreenResolution: ...
    def capture_screen(self, serial_number: str) -> bytes: ...
    def tap(self, serial_number: str, x: int, y: int) -> None: ...
    def swipe(
        self,
        serial_number: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int,
    ) -> None: ...
    def press_back(self, serial_number: str) -> None: ...


class PageDetector(Protocol):
    """Classify a screenshot only against caller-approved expected states."""

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: ExpectedStates,
    ) -> PageDetection: ...


class ScanReader(Protocol):
    """Existing screenshot reader surface used after automation succeeds."""

    def read_scan(self, directory: Path, *, debug: bool = False) -> RecognitionResult: ...


StableWaiter = Callable[[Callable[[], bytes]], StableScreenResult]


def _point_in_rect(point: Point, rectangle: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = rectangle
    return x1 <= point.x <= x2 and y1 <= point.y <= y2


def appraisal_target_is_safe(
    target: Point,
    transfer_targets: Sequence[Point],
    config: HuaweiMate30AutomationConfig = HUAWEI_MATE_30_AUTOMATION,
) -> bool:
    """Require the OCR target to stay in its lane and away from Transfer."""

    if not _point_in_rect(target, config.appraisal_target_rect):
        return False
    if _point_in_rect(target, config.transfer_forbidden_rect):
        return False
    return all(
        math.dist((target.x, target.y), (transfer.x, transfer.y))
        >= config.transfer_clearance_pixels
        for transfer in transfer_targets
    )


def planned_actions(
    config: HuaweiMate30AutomationConfig = HUAWEI_MATE_30_AUTOMATION,
) -> tuple[dict[str, object], ...]:
    """Return the exact fixed plan printed by dry-run."""

    return (
        {"name": "scroll_to_moves", "kind": "swipe", "coordinates": asdict(config.scroll_to_moves)},
        {
            "name": "open_action_menu",
            "kind": "tap",
            "coordinates": asdict(config.menu_button),
            "maximum_attempts": config.max_menu_tap_attempts,
        },
        {
            "name": "open_appraisal",
            "kind": "tap",
            "coordinates": "OCR center of 調查寶可夢; no fixed fallback",
        },
        {
            "name": "advance_appraisal_dialogue",
            "kind": "tap",
            "coordinates": asdict(config.appraisal_advance),
            "maximum_attempts": 1,
        },
        {
            "name": "exit_appraisal",
            "kind": "tap",
            "coordinates": {"x": 720, "y": 1560},
        },
    )


def _thumbnail(png_bytes: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            return image.convert("L").resize((144, 312), Image.Resampling.BILINEAR)
    except (OSError, ValueError) as error:
        raise AutomationError(f"Could not decode stability screenshot: {error}") from error


def screen_difference(previous_png: bytes, current_png: bytes) -> float:
    """Return normalized mean pixel difference for two reduced screenshots."""

    difference = ImageChops.difference(_thumbnail(previous_png), _thumbnail(current_png))
    return float(ImageStat.Stat(difference).mean[0]) / 255.0


def wait_for_stable_screen(
    capture: Callable[[], bytes],
    *,
    interval_seconds: float = 0.3,
    difference_threshold: float = 0.008,
    consecutive_samples: int = 3,
    timeout_seconds: float = 8.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> StableScreenResult:
    """Wait until several consecutive reduced screenshots are nearly identical."""

    if interval_seconds <= 0 or timeout_seconds <= 0 or consecutive_samples <= 0:
        raise ValueError("Stability timing values must be positive.")
    previous = capture()
    differences: list[float] = []
    stable_count = 0
    deadline = monotonic() + timeout_seconds
    maximum_samples = math.ceil(timeout_seconds / interval_seconds) + 1
    for _ in range(maximum_samples):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleeper(min(interval_seconds, remaining))
        current = capture()
        difference = screen_difference(previous, current)
        differences.append(difference)
        stable_count = stable_count + 1 if difference <= difference_threshold else 0
        if stable_count >= consecutive_samples:
            return StableScreenResult(current, tuple(differences))
        previous = current
    raise AutomationError(f"Screen did not stabilize within {timeout_seconds:.1f} seconds.")


class HuaweiMate30PageDetector:
    """Detect only the fixed Traditional Chinese 1440x3120 page layouts."""

    _DIALOGUE_KEYWORDS: Final = ("整體", "調查", "攻擊", "防禦", "寶可夢", "HP")

    def __init__(
        self,
        reader: RecognitionService,
        config: HuaweiMate30AutomationConfig = HUAWEI_MATE_30_AUTOMATION,
    ) -> None:
        self._reader = reader
        self._config = config

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: ExpectedStates,
    ) -> PageDetection:
        image = self._decode(png_bytes)
        expected_set = set(expected)

        if "appraisal_bars" in expected_set:
            bars, _, _ = self._reader._ivs(image)
            if bars is not None:
                return PageDetection(
                    state="appraisal_bars",
                    confidence=1.0,
                    details={"iv_values": [bar.value for bar in bars]},
                )

        if "action_menu" in expected_set:
            menu_detection = self._detect_action_menu(image)
            if menu_detection is not None:
                return menu_detection

        if "detail_moves" in expected_set:
            warnings: list[str] = []
            moves, _, _ = self._reader._moves(image, warnings)
            if len(moves) >= 2:
                return PageDetection(
                    state="detail_moves",
                    confidence=min(move.confidence for move in moves[:2]),
                    matched_texts=tuple(move.raw for move in moves),
                )
            candidates = self._ocr_rectangle(image, MOVE_PAGE_EVIDENCE_RECT)
            move_rows = match_move_name_power_rows(candidates)
            if move_rows:
                anchor_found = any(
                    "道館" in normalize_ocr_text(candidate.raw)
                    or "團體戰" in normalize_ocr_text(candidate.raw)
                    for candidate in candidates
                )
                confidence = min(
                    min(name.confidence, power.confidence) for name, power in move_rows
                )
                if anchor_found:
                    confidence = min(1.0, confidence + 0.01)
                return PageDetection(
                    state="detail_moves",
                    confidence=confidence,
                    matched_texts=tuple(item.raw for row in move_rows for item in row),
                    details={
                        "move_rows": len(move_rows),
                        "move_section_anchor": anchor_found,
                    },
                )

        if "detail_summary" in expected_set:
            cp_candidates, _ = self._reader._ocr_cp_variants(image)
            name_candidates, _ = self._reader._ocr_variants(image, NAME_RECT)
            cp = self._reader._best_cp(cp_candidates)
            name = self._reader._best_text(name_candidates)
            hp_candidates = self._ocr_rectangle(image, SUMMARY_HP_RECT)
            hp = max(
                (
                    candidate
                    for candidate in hp_candidates
                    if re.search(
                        r"\d+\s*/\s*\d+\s*HP",
                        normalize_ocr_text(candidate.raw),
                        re.IGNORECASE,
                    )
                ),
                key=lambda candidate: candidate.confidence,
                default=None,
            )
            if name is not None and (cp is not None or hp is not None):
                details: dict[str, object] = {}
                evidence = "name_hp"
                matched_texts: tuple[str, ...] = (name.raw,)
                confidence_candidates = [name.confidence]
                if cp is not None:
                    normalized_cp = normalize_cp_candidate(cp.raw)
                    if normalized_cp is not None:
                        details["cp"] = normalized_cp
                    matched_texts = (cp.raw, name.raw)
                    confidence_candidates.append(cp.confidence)
                    evidence = "name_cp"
                if hp is not None:
                    details["hp"] = re.sub(r"\s+", "", normalize_ocr_text(hp.raw)).upper()
                    confidence_candidates.append(hp.confidence)
                    if cp is not None:
                        evidence = "name_cp_hp"
                details["summary_evidence"] = evidence
                return PageDetection(
                    state="detail_summary",
                    confidence=min(confidence_candidates),
                    matched_texts=matched_texts,
                    details=details,
                )

        if "appraisal_dialogue" in expected_set:
            candidates = self._ocr_rectangle(
                image,
                self._config.appraisal_dialogue_rect,
            )

            normalized = tuple(
                (
                    candidate,
                    normalize_ocr_text(candidate.raw).upper(),
                )
                for candidate in candidates
            )

            keyword_hits = {
                keyword
                for keyword in self._DIALOGUE_KEYWORDS
                if any(
                    keyword.upper() in text
                    for _, text in normalized
                )
            }

            stat_hits = {
                keyword
                for keyword in ("攻擊", "防禦", "HP")
                if keyword in keyword_hits
            }

            # 正常详情页可能只有 HP。
            # 招式页的“新攻擊招式”可能只有 攻擊。
            # 单独一个普通关键词不能证明是评价对话。
            dialogue_confirmed = (
                "整體" in keyword_hits
                or "調查" in keyword_hits
                or len(stat_hits) >= 2
            )

            if dialogue_confirmed:
                matched = tuple(
                    candidate
                    for candidate, candidate_text in normalized
                    if any(
                        keyword.upper() in candidate_text
                        for keyword in keyword_hits
                    )
                )

                return PageDetection(
                    state="appraisal_dialogue",
                    confidence=max(
                        item.confidence for item in matched
                    ),
                    matched_texts=tuple(
                        item.raw for item in matched
                    ),
                    details={
                        "keyword_hits": sorted(keyword_hits),
                    },
                )

        return PageDetection(
            state="unknown",
            confidence=0.0,
            details={"expected": list(expected)},
        )

    def _decode(self, png_bytes: bytes) -> object:
        encoded = self._reader._np.frombuffer(png_bytes, dtype=self._reader._np.uint8)
        image = self._reader._cv2.imdecode(encoded, self._reader._cv2.IMREAD_COLOR)
        if image is None:
            raise AutomationError("ADB screenshot could not be decoded for page detection.")
        if image.shape[:2] != (self._config.height, self._config.width):
            raise AutomationError(
                f"Automatic scan requires {self._config.width}x{self._config.height}; "
                f"got {image.shape[1]}x{image.shape[0]}."
            )
        return image

    def _ocr_rectangle(
        self,
        image: object,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[OcrCandidate, ...]:
        crop = self._reader._crop(image, rectangle)
        scale = 1.5
        prepared = self._reader._cv2.resize(
            self._reader._sharpen(crop),
            None,
            fx=scale,
            fy=scale,
            interpolation=self._reader._cv2.INTER_CUBIC,
        )
        return self._reader._run_ocr(prepared, rectangle, scale)

    def _detect_action_menu(self, image: object) -> PageDetection | None:
        candidates = self._ocr_rectangle(image, self._config.action_menu_rect)
        appraisal_candidates = tuple(
            candidate
            for candidate in candidates
            if "調查寶可夢" in normalize_ocr_text(candidate.raw).replace(" ", "")
            and candidate.confidence >= self._config.menu_ocr_min_confidence
        )
        if not appraisal_candidates:
            return None
        appraisal = max(appraisal_candidates, key=lambda item: item.confidence)
        target = Point(
            x=(appraisal.box[0] + appraisal.box[2]) // 2,
            y=(appraisal.box[1] + appraisal.box[3]) // 2,
        )
        transfer_candidates = tuple(
            candidate
            for candidate in candidates
            if "傳送" in normalize_ocr_text(candidate.raw).replace(" ", "")
        )
        transfer_targets = tuple(
            Point(
                x=(candidate.box[0] + candidate.box[2]) // 2,
                y=(candidate.box[1] + candidate.box[3]) // 2,
            )
            for candidate in transfer_candidates
        )
        if not appraisal_target_is_safe(target, transfer_targets, self._config):
            return None
        return PageDetection(
            state="action_menu",
            confidence=appraisal.confidence,
            matched_texts=tuple(candidate.raw for candidate in candidates),
            appraisal_target=target,
            details={
                "appraisal_box": list(appraisal.box),
                "transfer_centers": [asdict(item) for item in transfer_targets],
            },
        )


class _DebugRecorder:
    def __init__(self, scan_directory: Path, enabled: bool) -> None:
        self._enabled = enabled
        self._directory = scan_directory / "debug" / "automation"
        self._state_index = 0
        self._actions: list[dict[str, object]] = []
        if enabled:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise LocalStorageError(
                    f"Could not create automation debug directory '{self._directory}': {error}"
                ) from error

    def write_plan(self, actions: Sequence[dict[str, object]]) -> None:
        if self._enabled:
            self._write_json("plan.json", {"actions": list(actions)})

    def screen(self, label: str, png_bytes: bytes) -> None:
        if self._enabled:
            atomic_write_bytes(self._directory / f"{label}.png", png_bytes)

    def detection(self, label: str, detection: PageDetection) -> None:
        if not self._enabled:
            return
        self._state_index += 1
        self._write_json(
            f"state_{self._state_index:02d}_{label}.json",
            detection.to_json_data(),
        )

    def action(
        self,
        *,
        name: str,
        kind: str,
        coordinates: object,
        executed: bool,
        before_state: PageState,
    ) -> None:
        if not self._enabled:
            return
        self._actions.append(
            {
                "name": name,
                "kind": kind,
                "coordinates": coordinates,
                "executed": executed,
                "before_state": before_state,
            }
        )
        self._write_json("actions.json", {"actions": self._actions})

    def stability(self, label: str, result: StableScreenResult) -> None:
        if self._enabled:
            self._write_json(
                f"stability_{label}.json",
                {"differences": list(result.differences)},
            )

    def named_state(
        self,
        label: str,
        detection: PageDetection,
        *,
        outcome: str,
        elapsed_seconds: float,
    ) -> None:
        if self._enabled:
            self._write_json(
                f"{label}_state.json",
                {
                    "outcome": outcome,
                    "elapsed_seconds": elapsed_seconds,
                    "detection": detection.to_json_data(),
                },
            )

    def state_wait(
        self,
        label: str,
        *,
        interval_seconds: float,
        timeout_seconds: float,
        samples: Sequence[dict[str, object]],
    ) -> None:
        if self._enabled:
            self._write_json(
                f"{label}_wait.json",
                {
                    "target_state": "action_menu",
                    "interval_seconds": interval_seconds,
                    "timeout_seconds": timeout_seconds,
                    "samples": list(samples),
                },
            )

    def poll_states(
        self,
        label: str,
        *,
        target_state: PageState,
        interval_seconds: float,
        timeout_seconds: float,
        samples: Sequence[dict[str, object]],
    ) -> None:
        if self._enabled:
            self._write_json(
                f"{label}_states.json",
                {
                    "target_state": target_state,
                    "interval_seconds": interval_seconds,
                    "timeout_seconds": timeout_seconds,
                    "samples": list(samples),
                },
            )

    def _write_json(self, name: str, data: object) -> None:
        atomic_write_text(
            self._directory / name,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        )


@dataclass(slots=True)
class _Session:
    scan_directory: Path
    manifest_path: Path
    manifest: ScanManifest
    recorder: _DebugRecorder
    current_step: str = "initialize"


class AutoScanService:
    """Run exactly one safety-gated Huawei Mate 30 scan from the detail top."""

    def __init__(
        self,
        config: AppConfig,
        adb: AutomationAdbGateway,
        detector: PageDetector,
        reader: ScanReader,
        *,
        automation: HuaweiMate30AutomationConfig = HUAWEI_MATE_30_AUTOMATION,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
        stable_waiter: StableWaiter | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        application_version: str = __version__,
    ) -> None:
        self._config = config
        self._adb = adb
        self._detector = detector
        self._reader = reader
        self._automation = automation
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._token_factory = token_factory or (lambda: uuid4().hex)
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._application_version = application_version
        if stable_waiter is None:
            self._stable_waiter: StableWaiter = lambda capture: wait_for_stable_screen(
                capture,
                interval_seconds=automation.stable_interval_seconds,
                difference_threshold=automation.stable_difference_threshold,
                consecutive_samples=automation.stable_consecutive_samples,
                timeout_seconds=automation.step_timeout_seconds,
            )
        else:
            self._stable_waiter = stable_waiter

    def scan_one(
        self,
        *,
        debug: bool = False,
        dry_run: bool = False,
        serial_number: str | None = None,
        notes: str | None = None,
    ) -> AutoScanResult:
        """Capture, navigate, recognize, and safely return from one detail page."""

        device = self._adb.resolve_device(serial_number)
        resolution = self._adb.get_resolution(device.serial_number)
        if (resolution.width, resolution.height) != (
            self._automation.width,
            self._automation.height,
        ):
            raise AutomationError(
                f"scan-auto-one requires {self._automation.width}x{self._automation.height}; "
                f"device reported {resolution}."
            )
        started_at = self._clock()
        if started_at.tzinfo is None:
            started_at = started_at.astimezone()
        scan_id = f"{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{self._token_factory()}"
        scan_directory = self._config.scan_root / started_at.strftime("%Y-%m-%d") / scan_id
        try:
            scan_directory.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            raise LocalStorageError(
                f"Could not create automatic scan directory '{scan_directory}': {error}"
            ) from error
        manifest_path = scan_directory / "manifest.json"
        manifest = ScanManifest(
            scan_id=scan_id,
            started_at=started_at,
            device_serial=device.serial_number,
            device_model=device.model_name,
            screen_resolution=resolution,
            workflow_mode="automatic",
            application_version=self._application_version,
            scan_status="in_progress",
            notes=notes,
        )
        self._write_manifest(manifest_path, manifest)
        session = _Session(
            scan_directory=scan_directory,
            manifest_path=manifest_path,
            manifest=manifest,
            recorder=_DebugRecorder(scan_directory, debug),
        )
        actions = planned_actions(self._automation)
        session.recorder.write_plan(actions)
        deadline = self._monotonic() + self._automation.total_timeout_seconds
        try:
            result = self._run(
                session,
                device.serial_number,
                deadline,
                actions,
                debug=debug,
                dry_run=dry_run,
            )
        except KeyboardInterrupt:
            self._mark_incomplete_after_interrupt(session)
            raise
        except PokemonGoCleanupError as error:
            raise self._fail(session, error) from error
        except Exception as error:
            wrapped = AutomationError(f"Unexpected automation failure: {error}")
            raise self._fail(session, wrapped) from error
        return result

    def _run(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        actions: tuple[dict[str, object], ...],
        *,
        debug: bool,
        dry_run: bool,
    ) -> AutoScanResult:
        session.current_step = "verify_detail_summary"
        initial = self._adb.capture_screen(serial)
        session.recorder.screen("00_initial", initial)
        initial, summary_detection = self._verify_detail_summary(
            session, serial, initial, deadline
        )
        session.current_step = "capture_summary"
        self._save_capture(session, "summary", initial)

        if dry_run:
            session.current_step = "dry_run"
            session.manifest = session.manifest.model_copy(
                update={"scan_status": "incomplete", "failed_step": "dry_run"}
            )
            self._write_manifest(session.manifest_path, session.manifest)
            return self._result(session, None, True, actions)

        self._check_deadline(deadline)
        session.current_step = "scroll_to_moves"
        moves, _ = self._scroll_to_moves(
            session,
            serial,
            initial,
            summary_detection,
            deadline,
        )
        session.current_step = "capture_moves"
        self._save_capture(session, "moves", moves)

        self._check_deadline(deadline)
        session.current_step = "open_action_menu"
        menu_screen, menu_detection = self._open_action_menu(
            session,
            serial,
            deadline,
        )
        if menu_detection.appraisal_target is None:
            raise AutomationError("Action menu did not provide a safe OCR target for 調查寶可夢.")

        self._check_deadline(deadline)
        session.current_step = "open_appraisal"
        appraisal_target = menu_detection.appraisal_target
        self._require_state(menu_detection, ("action_menu",))
        session.recorder.screen("before_open_appraisal", menu_screen)
        session.recorder.action(
            name="open_appraisal",
            kind="tap",
            coordinates=asdict(appraisal_target),
            executed=True,
            before_state=menu_detection.state,
        )
        self._adb.tap(
            serial,
            appraisal_target.x,
            appraisal_target.y,
        )

        entry_result = self._wait_for_appraisal_entry(
            session,
            serial,
        )
        appraisal_screen = entry_result.png_bytes
        appraisal_detection = entry_result.detection

        if entry_result.outcome != "reached":
            atomic_write_bytes(
                session.scan_directory / "appraisal_entry_timeout.png",
                appraisal_screen,
            )
            session.recorder.screen("appraisal_entry_timeout", appraisal_screen)
            raise AutomationError(
                "Appraisal dialogue or IV bars did not appear within 30 seconds "
                "after opening appraisal. No additional tap was sent."
            )

        if appraisal_detection.state == "appraisal_dialogue":
            self._check_deadline(deadline)
            session.current_step = "advance_appraisal_dialogue_1"
            self._require_state(appraisal_detection, ("appraisal_dialogue",))
            session.recorder.screen(
                "before_advance_appraisal_dialogue_1",
                appraisal_screen,
            )
            session.recorder.action(
                name="advance_appraisal_dialogue_1",
                kind="tap",
                coordinates=asdict(self._automation.appraisal_advance),
                executed=True,
                before_state=appraisal_detection.state,
            )
            self._adb.tap(
                serial,
                self._automation.appraisal_advance.x,
                self._automation.appraisal_advance.y,
            )
            session.current_step = "wait_for_appraisal_bars"
            wait_result = self._wait_for_appraisal_bars(
                session,
                serial,
                deadline,
            )
            appraisal_screen = wait_result.png_bytes
            appraisal_detection = wait_result.detection
            if wait_result.outcome == "unexpected":
                raise AutomationError(
                    "Unexpected page state while waiting for appraisal_bars: "
                    f"{appraisal_detection.state}. No additional tap was sent."
                )
            if wait_result.outcome != "reached":
                atomic_write_bytes(
                    session.scan_directory / "appraisal_wait_timeout.png",
                    appraisal_screen,
                )
                session.recorder.screen("appraisal_wait_timeout", appraisal_screen)
                raise AutomationError(
                    "IV bars did not appear within 30 seconds after the single "
                    "appraisal dialogue tap. No additional tap was sent."
                )
        if appraisal_detection.state != "appraisal_bars":
            raise AutomationError("IV bars were not detected. No additional tap was sent.")

        session.current_step = "capture_appraisal"
        appraisal = self._adb.capture_screen(serial)
        appraisal_detection = self._detect(
            session, "appraisal_capture", appraisal, ("appraisal_bars",)
        )
        self._require_state(appraisal_detection, ("appraisal_bars",))
        self._save_capture(session, "appraisal", appraisal)

        self._check_deadline(deadline)
        session.current_step = "exit_appraisal"
        self._exit_appraisal(
            session,
            serial,
            before=appraisal,
            before_detection=appraisal_detection,
        )

        self._check_deadline(deadline)
        session.current_step = "recognize_scan"
        recognition = self._reader.read_scan(session.scan_directory, debug=debug)
        session.manifest = session.manifest.model_copy(
            update={"scan_status": "complete", "failed_step": None}
        )
        self._write_manifest(session.manifest_path, session.manifest)
        logger.info(
            "automatic_scan_completed",
            extra={
                "scan_id": session.manifest.scan_id,
                "scan_directory": str(session.scan_directory.resolve()),
            },
        )
        return self._result(session, recognition, False, actions)

    def _verify_detail_summary(
        self,
        session: _Session,
        serial: str,
        initial: bytes,
        deadline: float,
    ) -> tuple[bytes, PageDetection]:
        """Wait for name plus CP, retaining name plus HP as the safe fallback."""

        interval = self._automation.summary_poll_interval_seconds
        timeout = self._automation.summary_wait_timeout_seconds
        started = self._monotonic()
        maximum_samples = math.ceil(timeout / interval)
        last_valid: tuple[bytes, PageDetection] | None = None
        screen = initial

        for sample_index in range(maximum_samples + 1):
            self._check_deadline(deadline)
            if sample_index > 0:
                elapsed = self._monotonic() - started
                if elapsed >= timeout:
                    break
                self._sleeper(min(interval, timeout - elapsed))
                screen = self._adb.capture_screen(serial)
                session.recorder.screen(
                    f"verify_detail_summary_poll_{sample_index:02d}",
                    screen,
                )

            detection = self._detect(
                session,
                f"verify_detail_summary_poll_{sample_index:02d}",
                screen,
                ("detail_summary",),
            )
            evidence = detection.details.get("summary_evidence")
            if detection.state == "detail_summary" and evidence in (
                "name_cp",
                "name_cp_hp",
            ):
                return screen, detection
            if detection.state == "detail_summary" and evidence == "name_hp":
                last_valid = (screen, detection)

            if self._monotonic() - started >= timeout:
                break

        if last_valid is not None:
            return last_valid
        raise AutomationError(
            "detail_summary was not confirmed within 15 seconds using name plus "
            "CP or name plus HP. No input was sent."
        )

    def _scroll_to_moves(
        self,
        session: _Session,
        serial: str,
        initial: bytes,
        initial_detection: PageDetection,
        deadline: float,
    ) -> tuple[bytes, PageDetection]:
        before = initial
        before_detection = initial_detection
        for attempt in range(1, self._automation.max_moves_swipe_attempts + 1):
            self._check_deadline(deadline)
            self._require_state(before_detection, ("detail_summary",))
            label = f"scroll_to_moves_attempt_{attempt}"
            session.recorder.screen(f"before_{label}", before)
            session.recorder.action(
                name=label,
                kind="swipe",
                coordinates=asdict(self._automation.scroll_to_moves),
                executed=True,
                before_state=before_detection.state,
            )
            gesture = self._automation.scroll_to_moves
            self._adb.swipe(
                serial,
                gesture.start.x,
                gesture.start.y,
                gesture.end.x,
                gesture.end.y,
                gesture.duration_ms,
            )
            result = self._wait_for_detail_moves(
                session,
                serial,
                label,
                deadline,
            )
            if result.outcome == "reached":
                return result.png_bytes, result.detection
            if result.outcome == "unexpected":
                raise AutomationError(
                    "Unexpected page state while waiting for detail_moves: "
                    f"{result.detection.state}. Automation stopped immediately."
                )
            if result.detection.state == "unknown":
                raise AutomationError(
                    "detail_moves was not detected within 15 seconds and the final "
                    "state was unknown. No second swipe was sent."
                )
            if result.detection.state != "detail_summary":
                raise AutomationError(
                    f"detail_moves wait ended in an unsupported state: {result.detection.state}."
                )
            before = result.png_bytes
            before_detection = result.detection

        raise AutomationError(
            "detail_moves was not detected within 15 seconds after either of the "
            "two allowed scroll swipes."
        )

    def _wait_for_detail_moves(
        self,
        session: _Session,
        serial: str,
        label: str,
        deadline: float,
    ) -> _StateWaitResult:
        interval = self._automation.moves_poll_interval_seconds
        timeout = self._automation.moves_wait_timeout_seconds
        started = self._monotonic()
        maximum_samples = math.ceil(timeout / interval)
        samples: list[dict[str, object]] = []
        last_screen: bytes | None = None
        last_detection: PageDetection | None = None

        for sample_index in range(1, maximum_samples + 1):
            self._check_deadline(deadline)
            actual_elapsed = self._monotonic() - started
            if actual_elapsed >= timeout:
                break
            self._sleeper(min(interval, timeout - actual_elapsed))
            screen = self._adb.capture_screen(serial)
            detection = self._detect_scroll_screen(
                session,
                f"{label}_poll_{sample_index:02d}",
                screen,
            )
            session.recorder.screen(
                f"{label}_poll_{sample_index:02d}",
                screen,
            )
            measured_elapsed = self._monotonic() - started
            elapsed = max(measured_elapsed, sample_index * interval)
            samples.append(
                {
                    "sample": sample_index,
                    "elapsed_seconds": elapsed,
                    "state": detection.state,
                    "confidence": detection.confidence,
                    "matched_texts": list(detection.matched_texts),
                }
            )
            session.recorder.poll_states(
                label,
                target_state="detail_moves",
                interval_seconds=interval,
                timeout_seconds=timeout,
                samples=samples,
            )
            last_screen = screen
            last_detection = detection
            if detection.state == "detail_moves":
                return _StateWaitResult(screen, detection, "reached", elapsed)
            if detection.state not in ("detail_summary", "unknown"):
                return _StateWaitResult(screen, detection, "unexpected", elapsed)
            if measured_elapsed >= timeout:
                break

        if last_screen is None or last_detection is None:
            last_screen = self._adb.capture_screen(serial)
            last_detection = self._detect_scroll_screen(
                session,
                f"{label}_poll_final",
                last_screen,
            )
            session.recorder.screen(f"{label}_poll_final", last_screen)
            samples.append(
                {
                    "sample": 0,
                    "elapsed_seconds": timeout,
                    "state": last_detection.state,
                    "confidence": last_detection.confidence,
                    "matched_texts": list(last_detection.matched_texts),
                }
            )
            session.recorder.poll_states(
                label,
                target_state="detail_moves",
                interval_seconds=interval,
                timeout_seconds=timeout,
                samples=samples,
            )
        return _StateWaitResult(last_screen, last_detection, "timeout", timeout)

    def _detect_scroll_screen(
        self,
        session: _Session,
        label: str,
        screen: bytes,
    ) -> PageDetection:
        moves = self._detect(session, f"{label}_moves", screen, ("detail_moves",))
        if moves.state == "detail_moves":
            return moves
        summary = self._detect(session, f"{label}_summary", screen, ("detail_summary",))
        if summary.state == "detail_summary":
            return summary
        abnormal = self._detect(
            session,
            f"{label}_abnormal",
            screen,
            ("action_menu", "appraisal_dialogue", "appraisal_bars"),
        )
        return abnormal if abnormal.state != "unknown" else moves

    def _open_action_menu(
        self,
        session: _Session,
        serial: str,
        deadline: float,
    ) -> tuple[bytes, PageDetection]:
        detail_states: ExpectedStates = ("detail_moves", "detail_summary")
        before = self._adb.capture_screen(serial)
        session.recorder.screen("before_open_action_menu", before)
        before_detection = self._detect(
            session,
            "before_open_action_menu",
            before,
            detail_states,
        )
        self._require_state(before_detection, detail_states)
        session.recorder.named_state(
            "before_open_action_menu",
            before_detection,
            outcome="ready",
            elapsed_seconds=0.0,
        )

        for attempt in range(1, self._automation.max_menu_tap_attempts + 1):
            self._check_deadline(deadline)
            if attempt > 1:
                before = self._adb.capture_screen(serial)
                session.recorder.screen(f"before_open_action_menu_attempt_{attempt}", before)
                before_detection = self._detect(
                    session,
                    f"before_open_action_menu_attempt_{attempt}",
                    before,
                    detail_states,
                )
                self._require_state(before_detection, detail_states)
                session.recorder.named_state(
                    f"before_open_action_menu_attempt_{attempt}",
                    before_detection,
                    outcome="ready",
                    elapsed_seconds=0.0,
                )

            action_name = f"open_action_menu_attempt_{attempt}"
            session.recorder.action(
                name=action_name,
                kind="tap",
                coordinates=asdict(self._automation.menu_button),
                executed=True,
                before_state=before_detection.state,
            )
            self._adb.tap(
                serial,
                self._automation.menu_button.x,
                self._automation.menu_button.y,
            )
            result = self._wait_for_action_menu(
                session,
                serial,
                action_name,
                deadline,
            )
            session.recorder.screen(f"after_{action_name}", result.png_bytes)
            session.recorder.named_state(
                action_name,
                result.detection,
                outcome=result.outcome,
                elapsed_seconds=result.elapsed_seconds,
            )
            if result.outcome == "reached":
                return result.png_bytes, result.detection
            if result.outcome == "unexpected":
                raise AutomationError(
                    "Unexpected page state while waiting for action_menu: "
                    f"{result.detection.state}. Automation stopped immediately."
                )

        raise AutomationError(
            "OCR did not detect action_menu within 10 seconds after either of "
            "the two allowed menu-button taps."
        )

    def _wait_for_action_menu(
        self,
        session: _Session,
        serial: str,
        label: str,
        deadline: float,
    ) -> _StateWaitResult:
        expected: ExpectedStates = (
            "action_menu",
            "detail_moves",
            "detail_summary",
            "appraisal_dialogue",
            "appraisal_bars",
        )
        interval = self._automation.menu_poll_interval_seconds
        timeout = self._automation.menu_wait_timeout_seconds
        started = self._monotonic()
        maximum_samples = math.ceil(timeout / interval)
        samples: list[dict[str, object]] = []
        last_screen: bytes | None = None
        last_detection: PageDetection | None = None

        for sample_index in range(1, maximum_samples + 1):
            self._check_deadline(deadline)
            actual_elapsed = self._monotonic() - started
            if actual_elapsed >= timeout:
                break
            self._sleeper(min(interval, timeout - actual_elapsed))
            screen = self._adb.capture_screen(serial)
            detection = self._detect(
                session,
                f"{label}_wait_{sample_index:02d}",
                screen,
                expected,
            )
            session.recorder.screen(
                f"{label}_wait_{sample_index:02d}",
                screen,
            )
            measured_elapsed = self._monotonic() - started
            elapsed = max(measured_elapsed, sample_index * interval)
            samples.append(
                {
                    "sample": sample_index,
                    "elapsed_seconds": elapsed,
                    "state": detection.state,
                    "confidence": detection.confidence,
                    "matched_texts": list(detection.matched_texts),
                }
            )
            session.recorder.state_wait(
                label,
                interval_seconds=interval,
                timeout_seconds=timeout,
                samples=samples,
            )
            last_screen = screen
            last_detection = detection
            if detection.state == "action_menu":
                return _StateWaitResult(screen, detection, "reached", elapsed)
            if detection.state not in ("detail_moves", "detail_summary"):
                return _StateWaitResult(screen, detection, "unexpected", elapsed)
            if measured_elapsed >= timeout:
                break

        if last_screen is None or last_detection is None:
            last_screen = self._adb.capture_screen(serial)
            last_detection = self._detect(
                session,
                f"{label}_timeout",
                last_screen,
                expected,
            )
        return _StateWaitResult(last_screen, last_detection, "timeout", timeout)

    def _wait_for_appraisal_entry(
        self,
        session: _Session,
        serial: str,
    ) -> _StateWaitResult:
        expected: ExpectedStates = (
            "appraisal_bars",
            "appraisal_dialogue",
            "action_menu",
        )
        interval = 0.5
        timeout = 30.0
        started = self._monotonic()
        maximum_samples = math.ceil(timeout / interval)
        last_screen: bytes | None = None
        last_detection: PageDetection | None = None
        unknown_after_menu_count = 0

        for sample_index in range(1, maximum_samples + 1):
            elapsed = self._monotonic() - started
            if elapsed >= timeout:
                break

            self._sleeper(min(interval, timeout - elapsed))
            screen = self._adb.capture_screen(serial)
            detection = self._detect(
                session,
                f"appraisal_entry_wait_{sample_index:02d}",
                screen,
                expected,
            )
            session.recorder.screen(
                f"appraisal_entry_wait_{sample_index:02d}",
                screen,
            )

            measured_elapsed = self._monotonic() - started
            last_screen = screen
            last_detection = detection

            if detection.state in (
                "appraisal_bars",
                "appraisal_dialogue",
            ):
                return _StateWaitResult(
                    screen,
                    detection,
                    "reached",
                    measured_elapsed,
                )

            if detection.state == "action_menu":
                # 调查宝可梦菜单仍然存在。不发送额外点击。
                unknown_after_menu_count = 0
                continue

            if detection.state == "unknown":
                # 菜单已经消失。严格 OCR 可能读不到评价对话。
                # 连续四帧、约两秒后再处理。只在这个特定阶段推断为评价对话。
                unknown_after_menu_count += 1

                if unknown_after_menu_count >= 4:
                    inferred = PageDetection(
                        state="appraisal_dialogue",
                        confidence=0.0,
                        details={
                            "inferred_from": (
                                "action_menu_disappeared_for_four_samples"
                            ),
                        },
                    )
                    session.recorder.detection(
                        "appraisal_entry_inferred_dialogue",
                        inferred,
                    )
                    return _StateWaitResult(
                        screen,
                        inferred,
                        "reached",
                        measured_elapsed,
                    )

                continue

            return _StateWaitResult(
                screen,
                detection,
                "unexpected",
                measured_elapsed,
            )

        if last_screen is None or last_detection is None:
            last_screen = self._adb.capture_screen(serial)
            last_detection = self._detect(
                session,
                "appraisal_entry_wait_final",
                last_screen,
                expected,
            )
            session.recorder.screen(
                "appraisal_entry_wait_final",
                last_screen,
            )

        if last_detection.state in (
            "appraisal_bars",
            "appraisal_dialogue",
        ):
            return _StateWaitResult(
                last_screen,
                last_detection,
                "reached",
                timeout,
            )

        return _StateWaitResult(
            last_screen,
            last_detection,
            "timeout",
            timeout,
        )

    def _wait_for_appraisal_bars(
        self,
        session: _Session,
        serial: str,
        deadline: float,
    ) -> _StateWaitResult:
        expected: ExpectedStates = (
            "appraisal_bars",
            "appraisal_dialogue",
        )
        interval = 0.5
        timeout = 30.0
        started = self._monotonic()
        maximum_samples = math.ceil(timeout / interval)
        last_screen: bytes | None = None
        last_detection: PageDetection | None = None

        for sample_index in range(1, maximum_samples + 1):
            now = self._monotonic()
            elapsed = now - started
            if elapsed >= timeout:
                break
            self._sleeper(min(interval, timeout - elapsed))
            screen = self._adb.capture_screen(serial)
            detection = self._detect(
                session,
                f"appraisal_bars_wait_{sample_index:02d}",
                screen,
                expected,
            )
            session.recorder.screen(
                f"appraisal_bars_wait_{sample_index:02d}",
                screen,
            )
            measured_elapsed = self._monotonic() - started
            last_screen = screen
            last_detection = detection
            if detection.state == "appraisal_bars":
                return _StateWaitResult(
                    screen,
                    detection,
                    "reached",
                    measured_elapsed,
                )
            if detection.state not in ("appraisal_dialogue", "unknown"):
                return _StateWaitResult(
                    screen,
                    detection,
                    "unexpected",
                    measured_elapsed,
                )

        if last_screen is None or last_detection is None:
            last_screen = self._adb.capture_screen(serial)
            last_detection = self._detect(
                session,
                "appraisal_bars_wait_final",
                last_screen,
                expected,
            )
            session.recorder.screen("appraisal_bars_wait_final", last_screen)
        if last_detection.state == "appraisal_bars":
            return _StateWaitResult(
                last_screen,
                last_detection,
                "reached",
                timeout,
            )
        if last_detection.state not in ("appraisal_dialogue", "unknown"):
            return _StateWaitResult(
                last_screen,
                last_detection,
                "unexpected",
                timeout,
            )
        return _StateWaitResult(
            last_screen,
            last_detection,
            "timeout",
            timeout,
        )

    def _tap(
        self,
        session: _Session,
        serial: str,
        *,
        name: str,
        point: Point,
        before: bytes,
        before_detection: PageDetection,
        before_expected: ExpectedStates,
        after_expected: ExpectedStates,
    ) -> bytes:
        self._require_state(before_detection, before_expected)
        session.recorder.screen(f"before_{name}", before)
        session.recorder.action(
            name=name,
            kind="tap",
            coordinates=asdict(point),
            executed=True,
            before_state=before_detection.state,
        )
        self._adb.tap(serial, point.x, point.y)
        stable = self._wait(session, serial, name)
        session.recorder.screen(f"after_{name}", stable)
        after_detection = self._detect(session, f"after_{name}", stable, after_expected)
        self._require_state(after_detection, after_expected)
        return stable

    def _exit_appraisal(
        self,
        session: _Session,
        serial: str,
        *,
        before: bytes,
        before_detection: PageDetection,
    ) -> None:
        self._require_state(before_detection, ("appraisal_bars",))
        session.recorder.screen("before_exit_appraisal", before)
        session.recorder.action(
            name="exit_appraisal",
            kind="tap",
            coordinates={"x": 720, "y": 1560},
            executed=True,
            before_state=before_detection.state,
        )

        # Send exactly one tap. Pokémon animation must not be used
        # as a screen-stability condition.
        self._adb.tap(serial, 720, 1560)

        interval = 0.5
        timeout = 15.0
        started = self._monotonic()
        sample_index = 0
        last_screen: bytes | None = None

        while self._monotonic() - started < timeout:
            self._sleeper(interval)
            sample_index += 1

            screen = self._adb.capture_screen(serial)
            last_screen = screen
            label = f"exit_appraisal_wait_{sample_index:02d}"

            session.recorder.screen(label, screen)
            detection = self._detect(
                session,
                label,
                screen,
                ("detail_summary", "detail_moves"),
            )

            if detection.state in ("detail_summary", "detail_moves"):
                session.recorder.screen("after_exit_appraisal", screen)
                return

            if detection.state != "unknown":
                raise AutomationError(
                    "Unexpected page state while exiting appraisal: "
                    f"{detection.state}. No additional input was sent."
                )

        if last_screen is None:
            last_screen = self._adb.capture_screen(serial)

        atomic_write_bytes(
            session.scan_directory / "exit_appraisal_timeout.png",
            last_screen,
        )
        session.recorder.screen("exit_appraisal_timeout", last_screen)

        raise AutomationError(
            "detail_summary or detail_moves was not detected within "
            "15 seconds after exiting appraisal. No additional input was sent."
        )

    def _wait(self, session: _Session, serial: str, label: str) -> bytes:
        result = self._stable_waiter(lambda: self._adb.capture_screen(serial))
        session.recorder.stability(label, result)
        return result.png_bytes

    def _detect(
        self,
        session: _Session,
        label: str,
        png_bytes: bytes,
        expected: ExpectedStates,
    ) -> PageDetection:
        detection = self._detector.detect(png_bytes, expected=expected)
        session.recorder.detection(label, detection)
        return detection

    @staticmethod
    def _require_state(
        detection: PageDetection,
        expected: ExpectedStates,
    ) -> None:
        if detection.state not in expected:
            raise AutomationError(
                f"Expected page state {', '.join(expected)}; "
                f"detected {detection.state}. No input was sent after this mismatch."
            )

    def _save_capture(
        self,
        session: _Session,
        step: ScanStep,
        png_bytes: bytes,
    ) -> None:
        destination = session.scan_directory / f"{step}.png"
        atomic_write_bytes(destination, png_bytes)
        captured_at = self._clock()
        if captured_at.tzinfo is None:
            captured_at = captured_at.astimezone()
        timestamps = dict(session.manifest.capture_timestamps)
        timestamps[step] = captured_at
        filenames = dict(session.manifest.screenshot_filenames)
        filenames[step] = destination.name
        session.manifest = session.manifest.model_copy(
            update={
                "capture_timestamps": timestamps,
                "screenshot_filenames": filenames,
            }
        )
        self._write_manifest(session.manifest_path, session.manifest)

    @staticmethod
    def _write_manifest(path: Path, manifest: ScanManifest) -> None:
        atomic_write_text(path, manifest.model_dump_json(indent=2) + "\n")

    def _check_deadline(self, deadline: float) -> None:
        if self._monotonic() >= deadline:
            raise AutomationError(
                f"Automatic scan exceeded {self._automation.total_timeout_seconds:.1f} seconds."
            )

    def _mark_incomplete_after_interrupt(self, session: _Session) -> None:
        try:
            session.manifest = session.manifest.model_copy(
                update={
                    "scan_status": "incomplete",
                    "failed_step": session.current_step,
                }
            )
            self._write_manifest(session.manifest_path, session.manifest)
        except PokemonGoCleanupError as error:
            logger.error(
                "automatic_scan_interrupt_manifest_failed",
                extra={"error": str(error), "failed_step": session.current_step},
            )

    def _fail(
        self,
        session: _Session,
        cause: PokemonGoCleanupError,
    ) -> AutomationError:
        manifest_error: PokemonGoCleanupError | None = None
        try:
            session.manifest = session.manifest.model_copy(
                update={
                    "scan_status": "incomplete",
                    "failed_step": session.current_step,
                }
            )
            self._write_manifest(session.manifest_path, session.manifest)
        except PokemonGoCleanupError as error:
            manifest_error = error
        detail = (
            f"Automatic scan failed at '{session.current_step}': {cause} "
            "Successful screenshots were preserved."
        )
        if manifest_error is None:
            detail += f" Manifest marked incomplete at '{session.manifest_path.resolve()}'."
        else:
            detail += f" Manifest update also failed: {manifest_error}"
        logger.error(
            "automatic_scan_incomplete",
            extra={
                "scan_id": session.manifest.scan_id,
                "failed_step": session.current_step,
                "cause": str(cause),
            },
        )
        return AutomationError(detail)

    @staticmethod
    def _result(
        session: _Session,
        recognition: RecognitionResult | None,
        dry_run: bool,
        actions: tuple[dict[str, object], ...],
    ) -> AutoScanResult:
        return AutoScanResult(
            scan_directory=session.scan_directory.resolve(),
            manifest_path=session.manifest_path.resolve(),
            manifest=session.manifest,
            recognition=recognition,
            dry_run=dry_run,
            planned_actions=actions,
        )
