"""Safety-gated automation for one fixed Huawei Mate 30 scan."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Final, Literal, Protocol
from uuid import uuid4

from pokemon_go_cleanup import __version__
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import AutomationError, LocalStorageError, PokemonGoCleanupError
from pokemon_go_cleanup.models import Device, ScanManifest, ScanStep, ScreenResolution
from pokemon_go_cleanup.recognition import (
    BAR_TOP,
    CIRCLED_IV_DIGITS,
    CP_RECT,
    HEIGHT,
    NAME_RECT,
    WIDTH,
    AppraisalRecognitionEvidence,
    MovesRecognitionEvidence,
    OcrCandidate,
    RecognitionEvidence,
    RecognitionResult,
    RecognitionService,
    RecognizedInteger,
    SummaryRecognitionEvidence,
    normalize_cp_candidate,
    normalize_nickname_text,
    normalize_ocr_text,
    parse_cp_raw,
)
from pokemon_go_cleanup.storage import atomic_write_bytes, atomic_write_text

logger = logging.getLogger(__name__)

PageState = Literal[
    "detail_summary",
    "detail_moves",
    "detail_ready",
    "detail_returned",
    "action_menu",
    "appraisal_dialogue",
    "appraisal_bars",
    "rename_keyboard",
    "rename_dialog",
    "unknown",
]
ExpectedStates = tuple[PageState, ...]
IvNicknameFormat = Literal["circled_iv"]

MOVE_PAGE_EVIDENCE_RECT: Final = (100, 900, 1360, 1800)
SUMMARY_HP_RECT: Final = (500, 1520, 940, 1680)
_MOVE_PAGE_LABELS: Final = (
    "道館",
    "團體戰",
    "訓練家對戰",
    "新攻擊招式",
    "新攻撃招式",
    "暗影獎勵",
    "天氣優勢",
    "天氣加成",
    "極巨",
    "LOCKED",
    "等級",
)
_MOVE_EVIDENCE_MIN_CONFIDENCE: Final = 0.80
_RETURN_CLOSE_BUTTON: Final = (720, 2772, 85)
_RETURN_MENU_BUTTON_RADIUS: Final = 105
_RETURN_BUTTON_RING_WIDTH: Final = 35
_RETURN_BUTTON_MIN_TEAL_RATIO: Final = 0.80
_RETURN_BUTTON_MIN_RING_CONTRAST: Final = 0.45


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
    nickname_edit: Point = Point(720, 1460)
    action_menu_rect: tuple[int, int, int, int] = (160, 1150, 1320, 2860)
    appraisal_target_rect: tuple[int, int, int, int] = (160, 1450, 1320, 2700)
    transfer_forbidden_rect: tuple[int, int, int, int] = (0, 2700, 1440, 3120)
    appraisal_dialogue_rect: tuple[int, int, int, int] = (100, 1250, 1340, 2860)
    appraisal_greeting_rect: tuple[int, int, int, int] = (80, 2450, 420, 2615)
    rename_dialog_rect: tuple[int, int, int, int] = (100, 700, 1340, 2200)
    nickname_input_rect: tuple[int, int, int, int] = (150, 1250, 1290, 1500)
    nickname_summary_rect: tuple[int, int, int, int] = (150, 1300, 1290, 1550)
    nickname_edit_row_tolerance: int = 70
    nickname_summary_expected_center_y: int = 1450
    nickname_summary_min_height_ratio: float = 0.4
    menu_ocr_min_confidence: float = 0.85
    transfer_clearance_pixels: int = 150
    summary_poll_interval_seconds: float = 0.5
    summary_wait_timeout_seconds: float = 15.0
    menu_poll_interval_seconds: float = 0.5
    menu_wait_timeout_seconds: float = 10.0
    max_menu_tap_attempts: int = 2
    moves_poll_interval_seconds: float = 0.5
    moves_wait_timeout_seconds: float = 15.0
    max_moves_swipe_attempts: int = 2
    rename_poll_interval_seconds: float = 0.5
    rename_wait_timeout_seconds: float = 10.0
    rename_cp_consensus_interval_seconds: float = 0.5
    rename_cp_consensus_max_frames: int = 6
    rename_cp_consensus_required_matches: int = 2
    nickname_maximum_characters: int = 32
    total_timeout_seconds: float = 180.0


HUAWEI_MATE_30_AUTOMATION: Final = HuaweiMate30AutomationConfig()


@dataclass(frozen=True, slots=True)
class PageDetection:
    """One screen classification with OCR evidence and an optional safe target."""

    state: PageState
    confidence: float
    matched_texts: tuple[str, ...] = ()
    appraisal_target: Point | None = None
    rename_keyboard_target: Point | None = None
    rename_confirm_target: Point | None = None
    details: dict[str, object] = field(default_factory=dict)
    nickname_edit_target: Point | None = None
    recognition_evidence: (
        SummaryRecognitionEvidence
        | MovesRecognitionEvidence
        | AppraisalRecognitionEvidence
        | None
    ) = None

    def to_json_data(self) -> dict[str, object]:
        return {
            "state": self.state,
            "confidence": self.confidence,
            "matched_texts": list(self.matched_texts),
            "appraisal_target": (
                asdict(self.appraisal_target) if self.appraisal_target is not None else None
            ),
            "rename_keyboard_target": (
                asdict(self.rename_keyboard_target)
                if self.rename_keyboard_target is not None
                else None
            ),
            "rename_confirm_target": (
                asdict(self.rename_confirm_target)
                if self.rename_confirm_target is not None
                else None
            ),
            "nickname_edit_target": (
                asdict(self.nickname_edit_target)
                if self.nickname_edit_target is not None
                else None
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
    nickname_change: NicknameRenameResult | None = None


@dataclass(frozen=True, slots=True)
class NicknameRenameResult:
    """Verified nickname mutation evidence returned to the batch layer."""

    nickname_before: str
    default_nickname: str
    expected_nickname: str
    editor_observed_nickname: str
    summary_observed_nickname: str
    summary_png: bytes
    summary_detection: PageDetection

@dataclass(frozen=True, slots=True)
class IvOnlyRenameResult:
    """In-memory result after one IV-only suffix append and safe detail return."""

    attack_iv: int
    defense_iv: int
    hp_iv: int
    iv_suffix: str
    detail_png: bytes = field(repr=False)
    detail_detection: PageDetection


@dataclass(frozen=True, slots=True)
class IvNickname:
    """One length-safe nickname and the IV text used to build it."""

    nickname: str
    format: IvNicknameFormat
    iv_suffix: str


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
    def press_key(self, serial_number: str, keycode: int) -> None: ...
    def input_text(self, serial_number: str, value: str) -> None: ...
    def ensure_unicode_input_available(self, serial_number: str) -> None: ...


class PageDetector(Protocol):
    """Classify a screenshot only against caller-approved expected states."""

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: ExpectedStates,
    ) -> PageDetection: ...

    def detect_returned_from_appraisal(self, png_bytes: bytes) -> PageDetection: ...

    def detect_detail_page_lightweight(self, png_bytes: bytes) -> PageDetection: ...

    def detect_nickname_controls(self, png_bytes: bytes) -> PageDetection: ...

    def read_summary_nickname(self, png_bytes: bytes) -> str: ...

    def read_summary_nickname_for_expected(
        self,
        png_bytes: bytes,
        expected_nickname: str,
    ) -> str: ...

    def read_summary_cp(self, png_bytes: bytes) -> int | None: ...

    def read_summary_hp(self, png_bytes: bytes) -> str | None: ...


class ScanReader(Protocol):
    """Existing screenshot reader surface used after automation succeeds."""

    def read_scan(self, directory: Path, *, debug: bool = False) -> RecognitionResult: ...

    def read_evidence(
        self,
        directory: Path,
        evidence: RecognitionEvidence,
        *,
        debug: bool = False,
    ) -> RecognitionResult | None: ...


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


def nickname_text_skeleton(value: str) -> str:
    """Return OCR-comparable nickname text while ignoring rendered slash loss."""

    return normalize_nickname_text(value).replace(" ", "").replace("/", "")


def compact_editor_nickname_text(value: str) -> str:
    """Remove OCR whitespace without folding full-width characters to ASCII."""

    return re.sub(r"\s+", "", value)


def circled_iv_suffix(attack: int, defense: int, hp: int) -> str:
    """Encode attack, defense and HP as three circled numbers, including zero."""

    values = (attack, defense, hp)
    if any(type(value) is not int or not 0 <= value <= 15 for value in values):
        raise AutomationError("IV values must be integers from 0 through 15.")
    return "".join(CIRCLED_IV_DIGITS[value] for value in values)


def build_iv_nickname(
    name: str,
    attack: int,
    defense: int,
    hp: int,
    *,
    max_length: int = 12,
) -> IvNickname:
    """Append exactly three circled IVs without truncating the default name."""

    suffix = circled_iv_suffix(attack, defense, hp)
    nickname = f"{name}{suffix}"
    if len(nickname) > max_length:
        raise AutomationError(
            f"IV nickname exceeds the {max_length}-character game limit; "
            "no nickname text was sent."
        )
    return IvNickname(nickname, "circled_iv", suffix)


def planned_actions(
    config: HuaweiMate30AutomationConfig = HUAWEI_MATE_30_AUTOMATION,
    *,
    rename_with_iv: bool = False,
) -> tuple[dict[str, object], ...]:
    """Return the exact fixed plan printed by dry-run."""

    actions: tuple[dict[str, object], ...] = (
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
    if not rename_with_iv:
        return actions
    return (*actions, {
        "name": "reset_nickname_to_default_chinese_name",
        "kind": "tap/keyevent/OCR-confirmed-tap",
        "coordinates": {
            "edit": asdict(config.nickname_edit),
            "confirm": "OCR center of 確定, 完成, or OK; no fixed fallback",
        },
    }, {
        "name": "append_iv_to_default_nickname",
        "kind": "tap/keyevent/text/OCR-confirmed-tap",
        "coordinates": {
            "edit": asdict(config.nickname_edit),
            "text": "circled attack, defense, HP (e.g. ⑭⑭⑮)",
            "confirm": "OCR center of 確定, 完成, or OK; no fixed fallback",
        },
    })


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
        png_sha256 = sha256(png_bytes).hexdigest()
        expected_set = set(expected)

        if "appraisal_bars" in expected_set:
            bars, bars_debug, detection_debug = self._reader._ivs(image)
            if bars is not None:
                return PageDetection(
                    state="appraisal_bars",
                    confidence=1.0,
                    details={
                        "iv_values": [bar.value for bar in bars],
                        "iv_geometry_path": (
                            "upward_fallback"
                            if bars[0].y_start < BAR_TOP
                            else "standard"
                        ),
                    },
                    recognition_evidence=AppraisalRecognitionEvidence(
                        png_sha256=png_sha256,
                        bars=bars,
                        bars_debug=bars_debug,
                        detection_debug=detection_debug,
                    ),
                )

        dialogue_has_priority_over_menu = (
            "appraisal_dialogue" in expected_set
            and (
                "action_menu" not in expected_set
                or expected.index("appraisal_dialogue") < expected.index("action_menu")
            )
        )
        if dialogue_has_priority_over_menu:
            greeting_detection = self._detect_appraisal_greeting(image)
            if greeting_detection is not None:
                return greeting_detection

        if "action_menu" in expected_set:
            menu_detection = self._detect_action_menu(image)
            if menu_detection is not None:
                return menu_detection

        if "detail_moves" in expected_set:
            warnings: list[str] = []
            moves, move_debug, move_fallback = self._reader._moves(image, warnings)
            if len(moves) >= 2:
                return PageDetection(
                    state="detail_moves",
                    confidence=min(move.confidence for move in moves[:2]),
                    matched_texts=tuple(move.raw for move in moves),
                    recognition_evidence=MovesRecognitionEvidence(
                        png_sha256=png_sha256,
                        candidates=moves,
                        warnings=tuple(warnings),
                        debug=move_debug,
                        fallback_debug=move_fallback,
                    ),
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

        if "rename_dialog" in expected_set or "rename_keyboard" in expected_set:
            rename_detection = self._detect_rename_dialog(image)
            if rename_detection is not None:
                return rename_detection

        if "detail_summary" in expected_set:
            cp_candidates, cp_debug = self._reader._ocr_variants(image, CP_RECT)
            cp = self._reader._best_cp(cp_candidates)
            name_candidates, name_debug = self._reader._ocr_variants(image, NAME_RECT)
            name = self._reader._best_text(name_candidates)
            if name is not None and cp is not None:
                evidence = None
                if self._reader._ordinary_cp_candidate_is_reliable(cp):
                    evidence = SummaryRecognitionEvidence(
                        png_sha256=png_sha256,
                        name_candidates=name_candidates,
                        cp_candidates=cp_candidates,
                        name_debug=name_debug,
                        cp_debug=cp_debug,
                    )
                return self._summary_name_cp_detection(
                    name,
                    cp,
                    ocr_path="fast_name_cp",
                    recognition_evidence=evidence,
                )

            if name is not None and cp is None:
                hsv_candidates = self._reader._ocr_cp_hsv_variants(image)
                cp = self._reader._best_cp(hsv_candidates)
                if cp is not None:
                    return self._summary_name_cp_detection(
                        name,
                        cp,
                        ocr_path="hsv_cp_fallback",
                        recognition_evidence=SummaryRecognitionEvidence(
                            png_sha256=png_sha256,
                            name_candidates=name_candidates,
                            cp_candidates=(*cp_candidates, *hsv_candidates),
                            name_debug=name_debug,
                            cp_debug=cp_debug,
                        ),
                    )

                fallback_candidates = self._reader._ocr_cp_fallback_variants(image)
                cp = self._reader._best_cp(fallback_candidates)
                if name is not None and cp is not None:
                    return self._summary_name_cp_detection(
                        name,
                        cp,
                        ocr_path="enhanced_cp_fallback",
                        recognition_evidence=SummaryRecognitionEvidence(
                            png_sha256=png_sha256,
                            name_candidates=name_candidates,
                            cp_candidates=(
                                *cp_candidates,
                                *hsv_candidates,
                                *fallback_candidates,
                            ),
                            name_debug=name_debug,
                            cp_debug=cp_debug,
                        ),
                    )

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
                if name is not None and hp is not None:
                    return PageDetection(
                        state="detail_summary",
                        confidence=min(name.confidence, hp.confidence),
                        matched_texts=(name.raw,),
                        details={
                            "hp": re.sub(
                                r"\s+", "", normalize_ocr_text(hp.raw)
                            ).upper(),
                            "summary_evidence": "name_hp",
                            "summary_ocr_path": "hp_fallback",
                        },
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

    def detect_detail_page_lightweight(self, png_bytes: bytes) -> PageDetection:
        """Confirm a detail page without reading its name, CP, HP, or moves."""

        return self._detect_detail_page_geometry(
            png_bytes,
            success_state="detail_ready",
        )

    def detect_nickname_controls(self, png_bytes: bytes) -> PageDetection:
        """Detect editor controls without extracting or validating nickname text."""

        image = self._decode(png_bytes)
        detection = self._detect_rename_dialog(image, include_nickname=False)
        return detection or PageDetection(
            state="unknown",
            confidence=0.0,
            details={"expected": ["rename_keyboard", "rename_dialog"]},
        )

    def detect_returned_from_appraisal(self, png_bytes: bytes) -> PageDetection:
        """Confirm appraisal is gone and both fixed detail-page buttons are visible."""

        return self._detect_detail_page_geometry(
            png_bytes,
            success_state="detail_returned",
        )

    def _detect_detail_page_geometry(
        self,
        png_bytes: bytes,
        *,
        success_state: Literal["detail_ready", "detail_returned"],
    ) -> PageDetection:
        image = self._decode(png_bytes)
        bars, _, _ = self._reader._ivs(image)
        if bars is not None:
            return PageDetection(
                state="unknown",
                confidence=0.0,
                details={
                    **({"detail_page_present": False} if success_state == "detail_ready" else {}),
                    "returned_from_appraisal": False,
                    "appraisal_overlay_absent": False,
                    "iv_appraisal_geometry_absent": False,
                    "reason": "iv_bars_present",
                },
            )

        close_x, close_y, close_radius = _RETURN_CLOSE_BUTTON
        close = self._detail_button_metrics(
            image,
            Point(close_x, close_y),
            close_radius,
        )
        menu = self._detail_button_metrics(
            image,
            self._config.menu_button,
            _RETURN_MENU_BUTTON_RADIUS,
        )
        buttons_present = all(
            metrics["disk_teal_ratio"] >= _RETURN_BUTTON_MIN_TEAL_RATIO
            and metrics["ring_contrast"] >= _RETURN_BUTTON_MIN_RING_CONTRAST
            for metrics in (close, menu)
        )
        details: dict[str, object] = {
            **({"detail_page_present": buttons_present} if success_state == "detail_ready" else {}),
            "returned_from_appraisal": buttons_present,
            "appraisal_overlay_absent": buttons_present,
            "iv_appraisal_geometry_absent": True,
            "detail_button_geometry": {
                "close": close,
                "menu": menu,
            },
        }
        if not buttons_present:
            details["reason"] = "detail_buttons_not_confirmed"
            return PageDetection(
                state="unknown",
                confidence=0.0,
                details=details,
            )
        return PageDetection(
            state=success_state,
            confidence=min(
                close["disk_teal_ratio"],
                menu["disk_teal_ratio"],
            ),
            details=details,
        )

    def _detail_button_metrics(
        self,
        image: object,
        center: Point,
        radius: int,
    ) -> dict[str, float]:
        extent = radius + _RETURN_BUTTON_RING_WIDTH
        rectangle = (
            center.x - extent,
            center.y - extent,
            center.x + extent + 1,
            center.y + extent + 1,
        )
        crop = self._reader._crop(image, rectangle)
        hsv = self._reader._cv2.cvtColor(crop, self._reader._cv2.COLOR_BGR2HSV)
        teal = self._reader._cv2.inRange(
            hsv,
            self._reader._np.array((70, 70, 60), dtype=self._reader._np.uint8),
            self._reader._np.array((105, 255, 255), dtype=self._reader._np.uint8),
        )
        y_coordinates, x_coordinates = self._reader._np.ogrid[
            : teal.shape[0],
            : teal.shape[1],
        ]
        local_center = extent
        squared_distance = (
            (x_coordinates - local_center) ** 2
            + (y_coordinates - local_center) ** 2
        )
        disk = squared_distance <= radius**2
        ring = (squared_distance <= extent**2) & ~disk
        disk_ratio = float(self._reader._np.count_nonzero(teal[disk])) / float(
            self._reader._np.count_nonzero(disk)
        )
        ring_ratio = float(self._reader._np.count_nonzero(teal[ring])) / float(
            self._reader._np.count_nonzero(ring)
        )
        return {
            "disk_teal_ratio": disk_ratio,
            "ring_teal_ratio": ring_ratio,
            "ring_contrast": disk_ratio - ring_ratio,
        }

    @staticmethod
    def _summary_name_cp_detection(
        name: OcrCandidate,
        cp: OcrCandidate,
        *,
        ocr_path: str,
        recognition_evidence: SummaryRecognitionEvidence | None = None,
    ) -> PageDetection:
        normalized_cp = normalize_cp_candidate(cp.raw)
        if normalized_cp is None:
            raise AssertionError("_best_cp returned a candidate without a CP prefix.")
        return PageDetection(
            state="detail_summary",
            confidence=min(name.confidence, cp.confidence),
            matched_texts=(cp.raw, name.raw),
            details={
                "cp": normalized_cp,
                "summary_evidence": "name_cp",
                "summary_ocr_path": ocr_path,
            },
            recognition_evidence=recognition_evidence,
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

    def _detect_appraisal_greeting(self, image: object) -> PageDetection | None:
        candidates = tuple(
            candidate
            for candidate in self._ocr_rectangle(
                image,
                self._config.appraisal_greeting_rect,
            )
            if "你好" in normalize_ocr_text(candidate.raw)
            and candidate.confidence >= self._config.menu_ocr_min_confidence
        )
        if not candidates:
            return None
        return PageDetection(
            state="appraisal_dialogue",
            confidence=max(candidate.confidence for candidate in candidates),
            matched_texts=tuple(candidate.raw for candidate in candidates),
            details={
                "dialogue_evidence": "greeting",
                "greeting_roi": list(self._config.appraisal_greeting_rect),
            },
        )

    def _circled_iv_regions(
        self, image: object, rectangle: tuple[int, int, int, int],
    ) -> tuple[tuple[int, int, int, int], ...]:
        """Find exactly three adjacent closed rings in the fixed nickname row."""

        cv2 = self._reader._cv2
        crop = self._reader._crop(image, rectangle)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            return ()
        rings: list[tuple[int, int, int, int]] = []
        left, top, _, _ = rectangle
        for index, contour in enumerate(contours):
            x, y, width, height = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)
            child = int(hierarchy[0][index][2])
            if (
                not 35 <= height <= 130
                or not 0.88 <= width / height <= 1.12
                or perimeter <= 0
                or 4 * math.pi * area / perimeter**2 < 0.82
                or child < 0
            ):
                continue
            hole = contours[child]
            if not 0.65 <= cv2.contourArea(hole) / area <= 0.96:
                continue
            rings.append((left + x, top + y, left + x + width, top + y + height))
        if len(rings) != 3:
            return ()
        rings.sort()
        heights = [bottom - top for _, top, _, bottom in rings]
        typical = sum(heights) / 3
        if max(heights) - min(heights) > typical * 0.15:
            return ()
        for first, second in pairwise(rings):
            if (
                abs((first[1] + first[3]) - (second[1] + second[3])) > typical * 0.25
                or not typical * 0.9 <= second[0] - first[0] <= typical * 1.4
            ):
                return ()
        return tuple(rings)

    def _read_circled_nickname(
        self, image: object, rectangle: tuple[int, int, int, int],
    ) -> str | None:
        """Recognize ring interiors independently; never infer values from the expected name."""

        rings = self._circled_iv_regions(image, rectangle)
        if not rings:
            return None
        values: list[int] = []
        for left, top, right, bottom in rings:
            readings: list[int] = []
            for fraction in (0.18, 0.23):
                inset = round((right - left) * fraction)
                inner = (left + inset, top + inset, right - inset, bottom - inset)
                crop = self._reader._crop(image, inner)
                prepared = self._reader._cv2.resize(
                    crop, None, fx=4, fy=4, interpolation=self._reader._cv2.INTER_CUBIC,
                )
                candidates = self._reader._run_ocr(prepared, inner, 4)
                if len(candidates) != 1 or candidates[0].confidence < 0.90:
                    return None
                digits = normalize_ocr_text(candidates[0].raw)
                if re.fullmatch(r"(?:[0-9]|1[0-5])", digits) is None:
                    return None
                readings.append(int(digits))
            if readings[0] != readings[1]:
                return None
            values.append(readings[0])
        first = rings[0]
        height = first[3] - first[1]
        name_rect = (
            rectangle[0], max(rectangle[1], first[1] - height // 3),
            first[0] - 3, min(rectangle[3], first[3] + height // 3),
        )
        if name_rect[2] <= name_rect[0]:
            return None
        candidates = self._ocr_rectangle(image, name_rect)
        if not candidates or any(candidate.confidence < 0.90 for candidate in candidates):
            return None
        name = self._join_summary_nickname_row(candidates)
        if not name:
            return None
        return name + circled_iv_suffix(*values)

    def read_summary_nickname(self, png_bytes: bytes) -> str:
        """Read a wide name row without changing generic page-identity OCR."""

        image = self._decode(png_bytes)
        circled = self._read_circled_nickname(image, self._config.nickname_summary_rect)
        if circled is not None:
            return circled
        return self._join_summary_nickname_row(
            self._ocr_rectangle(
                image,
                self._config.nickname_summary_rect,
            )
        )

    def read_summary_nickname_for_expected(
        self,
        png_bytes: bytes,
        expected_nickname: str,
    ) -> str:
        """Retry the same wide row with raw color only after a strict mismatch."""

        image = self._decode(png_bytes)
        circled = self._read_circled_nickname(image, self._config.nickname_summary_rect)
        if circled is not None:
            return circled
        observed = self._join_summary_nickname_row(
            self._ocr_rectangle(
                image,
                self._config.nickname_summary_rect,
            )
        )
        if nickname_text_skeleton(observed) == nickname_text_skeleton(
            expected_nickname
        ):
            return observed
        return self._join_summary_nickname_row(
            self._ocr_wide_nickname_raw_color(image)
        )

    def _ocr_wide_nickname_raw_color(
        self,
        image: object,
    ) -> tuple[OcrCandidate, ...]:
        rectangle = self._config.nickname_summary_rect
        crop = self._reader._crop(image, rectangle)
        scale = 2.0
        prepared = self._reader._cv2.resize(
            crop,
            None,
            fx=scale,
            fy=scale,
            interpolation=self._reader._cv2.INTER_CUBIC,
        )
        return self._reader._run_ocr(prepared, rectangle, scale)

    def _join_summary_nickname_row(
        self,
        candidates: Sequence[OcrCandidate],
    ) -> str:
        normalized = tuple(
            (candidate, normalize_nickname_text(candidate.raw).replace(" ", ""))
            for candidate in candidates
        )
        normalized = tuple((candidate, text) for candidate, text in normalized if text)
        if not normalized:
            return ""

        chinese_candidates = tuple(
            (candidate, text)
            for candidate, text in normalized
            if re.search(r"[\u3400-\u9fff]", text)
        )
        anchor_pool = chinese_candidates or normalized
        if chinese_candidates:
            anchor, _ = max(
                anchor_pool,
                key=lambda item: (
                    item[0].box[3] - item[0].box[1],
                    item[0].confidence,
                ),
            )
        else:
            anchor, _ = max(
                anchor_pool,
                key=lambda item: (
                    item[0].box[3] - item[0].box[1],
                    -abs(
                        (item[0].box[1] + item[0].box[3]) // 2
                        - self._config.nickname_summary_expected_center_y
                    ),
                    item[0].confidence,
                ),
            )

        anchor_center_y = (anchor.box[1] + anchor.box[3]) // 2
        anchor_height = max(1, anchor.box[3] - anchor.box[1])
        row_candidates = tuple(
            (candidate, text)
            for candidate, text in normalized
            if abs((candidate.box[1] + candidate.box[3]) // 2 - anchor_center_y)
            <= self._config.nickname_edit_row_tolerance
            and candidate.box[3] - candidate.box[1]
            >= anchor_height * self._config.nickname_summary_min_height_ratio
        )
        return "".join(
            text
            for candidate, text in sorted(
                row_candidates,
                key=lambda item: item[0].box[0],
            )
        )

    def read_summary_cp(self, png_bytes: bytes) -> int | None:
        """Run progressive CP-only OCR for a previously confirmed summary page."""

        image = self._decode(png_bytes)
        candidates, _ = self._reader._ocr_cp_variants(image)
        best = self._reader._best_cp(candidates)
        return parse_cp_raw(best.raw) if best is not None else None

    def read_summary_hp(self, png_bytes: bytes) -> str | None:
        """Read the fixed HP row without treating it as a replacement for CP."""

        image = self._decode(png_bytes)
        candidates = self._ocr_rectangle(image, SUMMARY_HP_RECT)
        hp = max(
            (
                candidate
                for candidate in candidates
                if re.search(
                    r"\d+\s*/\s*\d+\s*HP",
                    normalize_ocr_text(candidate.raw),
                    re.IGNORECASE,
                )
            ),
            key=lambda candidate: candidate.confidence,
            default=None,
        )
        if hp is None:
            return None
        return re.sub(r"\s+", "", normalize_ocr_text(hp.raw)).upper()

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

    def _detect_rename_dialog(
        self,
        image: object,
        *,
        include_nickname: bool = True,
    ) -> PageDetection | None:
        candidates = self._ocr_rectangle(image, self._config.rename_dialog_rect)
        nickname_details: dict[str, object] = {}
        if include_nickname:
            input_left, input_top, input_right, input_bottom = (
                self._config.nickname_input_rect
            )
            nickname_candidates = tuple(
                compact_editor_nickname_text(candidate.raw)
                for candidate in candidates
                if input_left <= (candidate.box[0] + candidate.box[2]) // 2 <= input_right
                and input_top <= (candidate.box[1] + candidate.box[3]) // 2 <= input_bottom
            )
            circled = self._read_circled_nickname(
                image,
                self._config.nickname_input_rect,
            )
            if circled is not None:
                nickname_candidates = (circled,)
            nickname_details = {
                "nickname_text_candidates": list(nickname_candidates),
                "nickname_evidence": (
                    "circled_geometry_ocr" if circled is not None else "row_ocr"
                ),
            }
        normalized = tuple(
            (candidate, normalize_ocr_text(candidate.raw).replace(" ", "").upper())
            for candidate in candidates
        )
        confirm_candidates = tuple(
            candidate
            for candidate, value in normalized
            if value in ("確定", "确定", "完成", "OK")
            and candidate.confidence >= self._config.menu_ocr_min_confidence
        )
        has_cancel = any(value in ("取消", "CANCEL") for _, value in normalized)
        if confirm_candidates and has_cancel:
            confirm = max(confirm_candidates, key=lambda candidate: candidate.confidence)
            return PageDetection(
                state="rename_dialog",
                confidence=confirm.confidence,
                matched_texts=tuple(candidate.raw for candidate in candidates),
                rename_confirm_target=Point(
                    x=(confirm.box[0] + confirm.box[2]) // 2,
                    y=(confirm.box[1] + confirm.box[3]) // 2,
                ),
                details=nickname_details,
            )

        title_candidates = tuple(
            candidate
            for candidate, value in normalized
            if value in ("設定暱稱", "設定暱称", "设定昵称")
            and candidate.confidence >= self._config.menu_ocr_min_confidence
        )
        keyboard_hits = tuple(
            candidate
            for candidate, value in normalized
            if candidate.box[1] >= 1800
            and (
                value in ("GIF", "拼音", "?123")
                or re.fullmatch(r"[A-Z0-9]", value) is not None
            )
            and candidate.confidence >= 0.80
        )
        keyboard_confirm_candidates = tuple(
            candidate
            for candidate, value in normalized
            if value in ("確定", "确定", "完成", "OK")
            and (candidate.box[0] + candidate.box[2]) // 2 >= 1000
            and candidate.confidence >= self._config.menu_ocr_min_confidence
        )
        if title_candidates and keyboard_confirm_candidates:
            title = max(title_candidates, key=lambda candidate: candidate.confidence)
            keyboard_confirm = max(
                keyboard_confirm_candidates,
                key=lambda candidate: candidate.confidence,
            )
            return PageDetection(
                state="rename_keyboard",
                confidence=title.confidence,
                matched_texts=tuple(candidate.raw for candidate in candidates),
                rename_keyboard_target=Point(
                    x=(keyboard_confirm.box[0] + keyboard_confirm.box[2]) // 2,
                    y=(keyboard_confirm.box[1] + keyboard_confirm.box[3]) // 2,
                ),
                details={
                    **nickname_details,
                    "keyboard_evidence_count": len(keyboard_hits),
                },
            )
        return None


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

    def timings(
        self,
        *,
        run_outcome: str,
        total_elapsed_seconds: float,
        stable_wait_seconds: float,
        ocr_seconds: float,
        steps: Sequence[dict[str, object]],
    ) -> None:
        if self._enabled:
            self._write_json(
                "timings.json",
                {
                    "run_outcome": run_outcome,
                    "total_elapsed_seconds": total_elapsed_seconds,
                    "stable_wait_seconds": stable_wait_seconds,
                    "ocr_seconds": ocr_seconds,
                    "steps": list(steps),
                },
            )

    def rename_cp_consensus(
        self,
        *,
        baseline_cp: int,
        checkpoint_cp: int,
        samples: Sequence[int | None],
        required_matches: int,
        trusted_cp: int | None,
    ) -> None:
        if self._enabled:
            self._write_json(
                "rename_cp_consensus.json",
                {
                    "baseline_cp": baseline_cp,
                    "checkpoint_cp": checkpoint_cp,
                    "samples": list(samples),
                    "required_matches": required_matches,
                    "trusted_cp": trusted_cp,
                },
            )

    def _write_json(self, name: str, data: object) -> None:
        atomic_write_text(
            self._directory / name,
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        )


class _StepProfiler:
    """Persist flat, non-overlapping monotonic timings for one automatic scan."""

    def __init__(
        self,
        recorder: _DebugRecorder,
        monotonic: Callable[[], float],
        ocr_seconds_total: Callable[[], float],
        started_at: float,
    ) -> None:
        self._recorder = recorder
        self._monotonic = monotonic
        self._ocr_seconds_total = ocr_seconds_total
        self._started_at = started_at
        self._started_ocr_seconds = ocr_seconds_total()
        self._steps: list[dict[str, object]] = []

    def record_completed(self, name: str, started_at: float, ended_at: float) -> None:
        self._append(name, started_at, ended_at, "completed", ocr_seconds=0.0)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        started_at = self._monotonic()
        started_ocr_seconds = self._ocr_seconds_total()
        try:
            yield
        except BaseException:
            self._append(
                name,
                started_at,
                self._monotonic(),
                "failed",
                ocr_seconds=self._ocr_seconds_total() - started_ocr_seconds,
            )
            raise
        else:
            self._append(
                name,
                started_at,
                self._monotonic(),
                "completed",
                ocr_seconds=self._ocr_seconds_total() - started_ocr_seconds,
            )

    def finish(self, outcome: str) -> None:
        self._persist(outcome, self._monotonic())

    def _append(
        self,
        name: str,
        started_at: float,
        ended_at: float,
        outcome: str,
        *,
        ocr_seconds: float,
    ) -> None:
        self._steps.append(
            {
                "sequence": len(self._steps) + 1,
                "step": name,
                "started_offset_seconds": round(started_at - self._started_at, 6),
                "duration_seconds": round(ended_at - started_at, 6),
                "stable_wait_seconds": 0.0,
                "ocr_seconds": round(max(0.0, ocr_seconds), 6),
                "outcome": outcome,
            }
        )
        self._persist("in_progress", ended_at)

    def _persist(self, outcome: str, ended_at: float) -> None:
        try:
            self._recorder.timings(
                run_outcome=outcome,
                total_elapsed_seconds=round(ended_at - self._started_at, 6),
                stable_wait_seconds=0.0,
                ocr_seconds=round(
                    max(0.0, self._ocr_seconds_total() - self._started_ocr_seconds),
                    6,
                ),
                steps=self._steps,
            )
        except PokemonGoCleanupError as error:
            logger.error(
                "automation_timing_write_failed",
                extra={"error": str(error), "run_outcome": outcome},
            )


@dataclass(slots=True)
class _Session:
    scan_directory: Path
    manifest_path: Path
    manifest: ScanManifest
    recorder: _DebugRecorder
    profiler: _StepProfiler
    current_step: str = "initialize"
    persist: bool = True

    def write_bytes(self, path: Path, data: bytes) -> None:
        if self.persist:
            atomic_write_bytes(path, data)

    def write_text(self, path: Path, data: str) -> None:
        if self.persist:
            atomic_write_text(path, data)


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

    def scan_one(
        self,
        *,
        debug: bool = False,
        dry_run: bool = False,
        rename_with_iv: bool = False,
        serial_number: str | None = None,
        notes: str | None = None,
    ) -> AutoScanResult:
        """Capture, navigate, recognize, and safely return from one detail page."""

        return self._scan_one(
            debug=debug,
            dry_run=dry_run,
            rename_with_iv=rename_with_iv,
            serial_number=serial_number,
            notes=notes,
        )

    def rename_iv_one(self, *, serial_number: str | None = None) -> IvOnlyRenameResult:
        """Read IVs and rename one Pokemon through the dedicated no-file workflow."""

        device = self.prepare_iv_only_device(serial_number)
        return self.process_current_iv_only(device)

    def prepare_iv_only_device(self, serial_number: str | None = None) -> Device:
        """Resolve and preflight one device before one or many IV-only operations."""

        device = self._adb.resolve_device(serial_number)
        resolution = self._adb.get_resolution(device.serial_number)
        if (resolution.width, resolution.height) != (
            self._automation.width,
            self._automation.height,
        ):
            raise AutomationError(
                f"IV-only naming requires {self._automation.width}x"
                f"{self._automation.height}; device reported {resolution}."
            )
        self._adb.ensure_unicode_input_available(device.serial_number)
        return device

    def process_current_iv_only(self, device: Device) -> IvOnlyRenameResult:
        """Process the currently visible Pokemon after one shared device preflight."""

        started_monotonic = self._monotonic()
        started_at = self._clock()
        if started_at.tzinfo is None:
            started_at = started_at.astimezone()
        run_id = f"iv_only_{started_at.strftime('%Y%m%d_%H%M%S_%f')}_{self._token_factory()}"
        virtual_directory = self._config.scan_root / ".iv-only" / run_id
        manifest = ScanManifest(
            scan_id=run_id,
            started_at=started_at,
            device_serial=device.serial_number,
            device_model=device.model_name,
            screen_resolution=ScreenResolution(
                width=self._automation.width,
                height=self._automation.height,
            ),
            workflow_mode="automatic",
            application_version=self._application_version,
            scan_status="in_progress",
        )
        recorder = _DebugRecorder(virtual_directory, False)
        profiler = _StepProfiler(
            recorder,
            self._monotonic,
            self._ocr_seconds_total,
            started_monotonic,
        )
        session = _Session(
            scan_directory=virtual_directory,
            manifest_path=virtual_directory / "manifest.json",
            manifest=manifest,
            recorder=recorder,
            profiler=profiler,
            persist=False,
        )
        deadline = self._monotonic() + self._automation.total_timeout_seconds
        session.profiler.record_completed(
            "initialize",
            started_monotonic,
            self._monotonic(),
        )
        try:
            result = self._run_iv_only(session, device.serial_number, deadline)
        except KeyboardInterrupt:
            session.profiler.finish("interrupted")
            raise
        except PokemonGoCleanupError as error:
            session.profiler.finish("failed")
            raise self._fail(session, error) from error
        except Exception as error:
            session.profiler.finish("failed")
            wrapped = AutomationError(f"Unexpected IV-only automation failure: {error}")
            raise self._fail(session, wrapped) from error
        session.profiler.finish("complete")
        return result

    def _scan_one(
        self,
        *,
        debug: bool = False,
        dry_run: bool = False,
        rename_with_iv: bool = False,
        serial_number: str | None = None,
        notes: str | None = None,
    ) -> AutoScanResult:
        scan_started_monotonic = self._monotonic()
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
        if rename_with_iv and not dry_run:
            self._adb.ensure_unicode_input_available(device.serial_number)
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
        recorder = _DebugRecorder(scan_directory, debug)
        profiler = _StepProfiler(
            recorder,
            self._monotonic,
            self._ocr_seconds_total,
            scan_started_monotonic,
        )
        session = _Session(
            scan_directory=scan_directory,
            manifest_path=manifest_path,
            manifest=manifest,
            recorder=recorder,
            profiler=profiler,
        )
        actions = planned_actions(self._automation, rename_with_iv=rename_with_iv)
        session.recorder.write_plan(actions)
        deadline = self._monotonic() + self._automation.total_timeout_seconds
        session.profiler.record_completed(
            "initialize",
            scan_started_monotonic,
            self._monotonic(),
        )
        try:
            result = self._run(
                session,
                device.serial_number,
                deadline,
                actions,
                debug=debug,
                dry_run=dry_run,
                rename_with_iv=rename_with_iv,
            )
        except KeyboardInterrupt:
            session.profiler.finish("interrupted")
            self._mark_incomplete_after_interrupt(session)
            raise
        except PokemonGoCleanupError as error:
            session.profiler.finish("failed")
            raise self._fail(session, error) from error
        except Exception as error:
            session.profiler.finish("failed")
            wrapped = AutomationError(f"Unexpected automation failure: {error}")
            raise self._fail(session, wrapped) from error
        session.profiler.finish("dry_run" if result.dry_run else "complete")
        return result

    def _run_iv_only(
        self,
        session: _Session,
        serial: str,
        deadline: float,
    ) -> IvOnlyRenameResult:
        session.current_step = "verify_detail_page_lightweight"
        with session.profiler.step("verify_detail_page_lightweight"):
            initial = self._adb.capture_screen(serial)
            detail = self._wait_for_lightweight_detail(
                session,
                serial,
                "verify_initial_detail",
                deadline,
                initial=initial,
            )
            if detail.outcome != "reached":
                raise AutomationError(
                    "The lightweight detector did not confirm a Pokemon detail page. "
                    "No input was sent."
                )

        appraisal, appraisal_detection = self._capture_appraisal_bars(
            session,
            serial,
            deadline,
            lightweight_detail=True,
        )
        session.current_step = "read_appraisal_ivs"
        with session.profiler.step("read_appraisal_ivs"):
            attack, defense, hp = self._iv_values_from_appraisal(
                appraisal,
                appraisal_detection,
            )

        self._check_deadline(deadline)
        session.current_step = "exit_appraisal"
        with session.profiler.step("exit_appraisal"):
            self._exit_appraisal(
                session,
                serial,
                before=appraisal,
                before_detection=appraisal_detection,
            )

        session.current_step = "rename_with_iv"
        return self._rename_iv_only(
            session,
            serial,
            attack,
            defense,
            hp,
            deadline,
        )

    def _capture_appraisal_bars(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        *,
        lightweight_detail: bool,
    ) -> tuple[bytes, PageDetection]:
        self._check_deadline(deadline)
        session.current_step = "open_action_menu"
        with session.profiler.step("open_action_menu"):
            menu_screen, menu_detection = self._open_action_menu(
                session,
                serial,
                deadline,
                lightweight_detail=lightweight_detail,
            )
        if menu_detection.appraisal_target is None:
            raise AutomationError(
                "Action menu did not provide a safe OCR target for 調查寶可夢."
            )

        self._check_deadline(deadline)
        session.current_step = "open_appraisal"
        appraisal_target = menu_detection.appraisal_target
        with session.profiler.step("open_appraisal_tap"):
            self._require_state(menu_detection, ("action_menu",))
            session.recorder.screen("before_open_appraisal", menu_screen)
            session.recorder.action(
                name="open_appraisal",
                kind="tap",
                coordinates=asdict(appraisal_target),
                executed=True,
                before_state=menu_detection.state,
            )
            self._adb.tap(serial, appraisal_target.x, appraisal_target.y)

        with session.profiler.step("wait_for_appraisal_entry"):
            entry_result = self._wait_for_appraisal_entry(session, serial)
        appraisal_screen = entry_result.png_bytes
        appraisal_detection = entry_result.detection
        if entry_result.outcome != "reached":
            session.write_bytes(
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
            with session.profiler.step("advance_appraisal_dialogue"):
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
            with session.profiler.step("wait_for_appraisal_bars"):
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
                session.write_bytes(
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
        with session.profiler.step("capture_appraisal"):
            appraisal = self._adb.capture_screen(serial)
            appraisal_detection = self._detect(
                session,
                "appraisal_capture",
                appraisal,
                ("appraisal_bars",),
            )
            self._require_state(appraisal_detection, ("appraisal_bars",))
            self._save_capture(session, "appraisal", appraisal)
        return appraisal, appraisal_detection

    def _run(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        actions: tuple[dict[str, object], ...],
        *,
        debug: bool,
        dry_run: bool,
        rename_with_iv: bool,
    ) -> AutoScanResult:
        session.current_step = "verify_detail_summary"
        with session.profiler.step("verify_detail_summary"):
            initial = self._adb.capture_screen(serial)
            session.recorder.screen("00_initial", initial)
            initial, summary_detection = self._verify_detail_summary(
                session, serial, initial, deadline
            )
        session.current_step = "capture_summary"
        with session.profiler.step("capture_summary"):
            self._save_capture(session, "summary", initial)

        if dry_run:
            session.current_step = "dry_run"
            with session.profiler.step("finalize_dry_run"):
                session.manifest = session.manifest.model_copy(
                    update={"scan_status": "incomplete", "failed_step": "dry_run"}
                )
                self._persist_manifest(session)
                result = self._result(session, None, True, actions)
            return result

        self._check_deadline(deadline)
        session.current_step = "scroll_to_moves"
        with session.profiler.step("scroll_to_moves"):
            moves, moves_detection = self._scroll_to_moves(
                session,
                serial,
                initial,
                summary_detection,
                deadline,
            )
        session.current_step = "capture_moves"
        with session.profiler.step("capture_moves"):
            self._save_capture(session, "moves", moves)

        appraisal, appraisal_detection = self._capture_appraisal_bars(
            session,
            serial,
            deadline,
            lightweight_detail=False,
        )

        self._check_deadline(deadline)
        session.current_step = "exit_appraisal"
        with session.profiler.step("exit_appraisal"):
            self._exit_appraisal(
                session,
                serial,
                before=appraisal,
                before_detection=appraisal_detection,
            )

        self._check_deadline(deadline)
        session.current_step = "recognize_scan"
        recognition: RecognitionResult | None
        with session.profiler.step("recognize_scan"):
            evidence = self._complete_recognition_evidence(
                summary_detection,
                moves_detection,
                appraisal_detection,
            )
            recognition = (
                self._reader.read_evidence(
                    session.scan_directory,
                    evidence,
                    debug=debug,
                )
                if evidence is not None
                else None
            )
            if recognition is None:
                recognition = self._reader.read_scan(session.scan_directory, debug=debug)
        nickname_change: NicknameRenameResult | None = None
        if rename_with_iv:
            self._check_deadline(deadline)
            session.current_step = "rename_with_iv"
            nickname_change, recognition = self._rename_with_iv(
                session,
                serial,
                recognition,
                deadline,
            )
        with session.profiler.step("finalize_scan"):
            session.manifest = session.manifest.model_copy(
                update={"scan_status": "complete", "failed_step": None}
            )
            self._persist_manifest(session)
            logger.info(
                "automatic_scan_completed",
                extra={
                    "scan_id": session.manifest.scan_id,
                    "scan_directory": str(session.scan_directory.resolve()),
                },
            )
            result = self._result(
                session,
                recognition,
                False,
                actions,
                nickname_change=nickname_change,
            )
        return result

    def _rename_iv_only(
        self,
        session: _Session,
        serial: str,
        attack: int,
        defense: int,
        hp: int,
        deadline: float,
    ) -> IvOnlyRenameResult:
        """Reset to the game default and append IVs without nickname OCR."""

        with session.profiler.step("rename_validate_iv_suffix"):
            iv_suffix = circled_iv_suffix(attack, defense, hp)
        with session.profiler.step("rename_open_editor_reset"):
            editor = self._open_nickname_editor_lightweight(
                session,
                serial,
                deadline,
                "open_nickname_editor_reset",
            )
        with session.profiler.step("rename_clear_to_default"):
            self._clear_nickname(
                session,
                serial,
                "clear_nickname_for_default",
                editor.state,
            )
        with session.profiler.step("rename_confirm_default"):
            self._confirm_nickname_editor(
                session,
                serial,
                deadline,
                "confirm_default_nickname",
                lightweight_detail=True,
            )
        with session.profiler.step("rename_open_editor_append_iv"):
            editor = self._open_nickname_editor_lightweight(
                session,
                serial,
                deadline,
                "open_nickname_editor_append_iv",
            )
        with session.profiler.step("rename_append_iv_suffix"):
            self._append_nickname_text(
                session,
                serial,
                iv_suffix,
                "append_iv_suffix",
                editor.state,
            )
        with session.profiler.step("rename_confirm_iv"):
            renamed_detail = self._confirm_nickname_editor(
                session,
                serial,
                deadline,
                "confirm_iv_nickname",
                lightweight_detail=True,
            )

        return IvOnlyRenameResult(
            attack_iv=attack,
            defense_iv=defense,
            hp_iv=hp,
            iv_suffix=iv_suffix,
            detail_png=renamed_detail.png_bytes,
            detail_detection=renamed_detail.detection,
        )

    @staticmethod
    def _iv_values_from_appraisal(
        appraisal_png: bytes,
        appraisal: PageDetection,
    ) -> tuple[int, int, int]:
        evidence = appraisal.recognition_evidence
        if (
            not isinstance(evidence, AppraisalRecognitionEvidence)
            or evidence.png_sha256 != sha256(appraisal_png).hexdigest()
        ):
            raise AutomationError(
                "Verified IV evidence from the actual appraisal frame is required "
                "before naming."
            )
        bars = evidence.bars
        return bars[0].value, bars[1].value, bars[2].value

    def _rename_with_iv(
        self,
        session: _Session,
        serial: str,
        recognition: RecognitionResult,
        deadline: float,
    ) -> tuple[NicknameRenameResult, RecognitionResult]:
        """Restore the game-provided Chinese name, then append recognized IVs."""

        with session.profiler.step("rename_validate_iv_suffix"):
            attack = recognition.attack_iv
            defense = recognition.defense_iv
            hp = recognition.hp_iv
            if attack is None or defense is None or hp is None:
                raise AutomationError(
                    "Cannot rename with IV because attack, defense, or HP IV was not recognized."
                )
            circled_iv_suffix(attack, defense, hp)
        with session.profiler.step("rename_open_editor_reset"):
            editor, recognition = self._open_nickname_editor_reset(
                session,
                serial,
                deadline,
                recognition,
            )
        with session.profiler.step("rename_clear_to_default"):
            self._clear_nickname(
                session, serial, "clear_nickname_for_default", editor.state
            )
        with session.profiler.step("rename_confirm_default"):
            default_summary = self._confirm_nickname_editor(
                session,
                serial,
                deadline,
                "confirm_default_nickname",
            )
        with session.profiler.step("rename_read_default_name"):
            default_nickname = compact_editor_nickname_text(
                self._detector.read_summary_nickname(default_summary.png_bytes)
            )
            if not default_nickname:
                raise AutomationError(
                    "Default nickname could not be read from the dedicated wide "
                    "summary row after resetting it; no IV suffix was sent."
                )
            session.recorder.detection(
                "verified_default_nickname_wide",
                PageDetection(
                    state="detail_summary",
                    confidence=default_summary.detection.confidence,
                    matched_texts=(default_nickname,),
                    details={
                        "nickname_evidence": "wide_summary_row",
                        "source_state": default_summary.detection.state,
                    },
                ),
            )
            built_nickname = build_iv_nickname(
                default_nickname,
                attack,
                defense,
                hp,
            )
            expected_nickname = built_nickname.nickname
            iv_suffix = built_nickname.iv_suffix
        with session.profiler.step("rename_open_editor_append_iv"):
            editor = self._open_nickname_editor(
                session, serial, deadline, "open_nickname_editor_append_iv"
            )
        with session.profiler.step("rename_append_iv_suffix"):
            self._append_nickname_text(
                session, serial, iv_suffix, "append_iv_suffix", editor.state
            )
        with session.profiler.step("rename_confirm_iv"):
            renamed_summary = self._confirm_nickname_editor(
                session,
                serial,
                deadline,
                "confirm_iv_nickname",
                expected_nickname=expected_nickname,
            )
        with session.profiler.step("rename_verify_final_summary"):
            session.write_bytes(
                session.scan_directory / "renamed_summary.png",
                renamed_summary.png_bytes,
            )
            summary_observed = self._verify_final_summary_nickname(
                renamed_summary.png_bytes,
                expected_nickname,
            )
            editor_candidates = renamed_summary.detection.details.get(
                "verified_editor_nickname_candidates"
            )
            editor_observed: str | None = None
            if isinstance(editor_candidates, list):
                editor_observed = next(
                    (
                        value
                        for value in editor_candidates
                        if isinstance(value, str)
                        and compact_editor_nickname_text(value) == expected_nickname
                    ),
                    None,
                )
            if editor_observed is None:
                raise AutomationError(
                    "Verified editor nickname evidence was lost before persistence."
                )
        with session.profiler.step("rename_persist_evidence"):
            evidence = NicknameRenameResult(
                nickname_before=recognition.pokemon_name.value or "",
                default_nickname=default_nickname,
                expected_nickname=expected_nickname,
                editor_observed_nickname=editor_observed,
                summary_observed_nickname=summary_observed,
                summary_png=renamed_summary.png_bytes,
                summary_detection=renamed_summary.detection,
            )
            session.write_text(
                session.scan_directory / "nickname_change.json",
                json.dumps(
                    {
                        "nickname_before": evidence.nickname_before,
                        "default_nickname": evidence.default_nickname,
                        "nickname_after": evidence.expected_nickname,
                        "editor_observed_nickname": evidence.editor_observed_nickname,
                        "summary_observed_nickname": evidence.summary_observed_nickname,
                        "summary_filename": "renamed_summary.png",
                        "status": "verified",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        return evidence, recognition

    def _verify_final_summary_nickname(
        self,
        png_bytes: bytes,
        expected_nickname: str,
    ) -> str:
        summary_observed = self._detector.read_summary_nickname_for_expected(
            png_bytes,
            expected_nickname,
        )
        if nickname_text_skeleton(summary_observed) != nickname_text_skeleton(
            expected_nickname
        ):
            raise AutomationError(
                "The renamed summary did not show the expected nickname characters. "
                "No further input was sent."
            )
        return summary_observed

    def _open_nickname_editor_lightweight(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        label: str,
    ) -> PageDetection:
        self._check_deadline(deadline)
        screen = self._adb.capture_screen(serial)
        detection = self._detector.detect_detail_page_lightweight(screen)
        session.recorder.detection(f"{label}_before", detection)
        self._require_state(detection, ("detail_ready",))
        return self._tap_nickname_editor(
            session,
            serial,
            deadline,
            label,
            screen,
            detection,
            nickname_controls_only=True,
        )

    def _open_nickname_editor(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        label: str,
    ) -> PageDetection:
        screen, detection = self._capture_nickname_editor_prestate(
            session,
            serial,
            deadline,
            label,
        )
        return self._tap_nickname_editor(
            session,
            serial,
            deadline,
            label,
            screen,
            detection,
        )

    def _open_nickname_editor_reset(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        recognition: RecognitionResult,
    ) -> tuple[PageDetection, RecognitionResult]:
        label = "open_nickname_editor_reset"
        screen, detection = self._capture_nickname_editor_prestate(
            session,
            serial,
            deadline,
            label,
        )
        recognition = self._resolve_rename_cp_disagreement(
            session,
            serial,
            deadline,
            recognition,
            detection,
        )
        editor = self._tap_nickname_editor(
            session,
            serial,
            deadline,
            label,
            screen,
            detection,
        )
        return editor, recognition

    def _capture_nickname_editor_prestate(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        label: str,
    ) -> tuple[bytes, PageDetection]:
        self._check_deadline(deadline)
        screen = self._adb.capture_screen(serial)
        detection = self._detect(session, f"{label}_before", screen, ("detail_summary",))
        self._require_state(detection, ("detail_summary",))
        return screen, detection

    def _tap_nickname_editor(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        label: str,
        screen: bytes,
        detection: PageDetection,
        *,
        nickname_controls_only: bool = False,
    ) -> PageDetection:
        target = self._automation.nickname_edit
        detection = replace(detection, nickname_edit_target=target)
        session.recorder.detection(f"{label}_target", detection)
        session.recorder.screen(f"before_{label}", screen)
        session.recorder.action(
            name=label,
            kind="tap",
            coordinates=asdict(target),
            executed=True,
            before_state=detection.state,
        )
        self._adb.tap(
            serial,
            target.x,
            target.y,
        )
        result = self._wait_for_rename_dialog(
            session,
            serial,
            label,
            deadline,
            nickname_controls_only=nickname_controls_only,
        )
        if result.outcome != "reached":
            raise AutomationError(
                "Nickname editor did not expose an OCR-confirmed editor state. "
                "No text input was sent."
            )
        return result.detection

    def _resolve_rename_cp_disagreement(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        recognition: RecognitionResult,
        checkpoint: PageDetection,
    ) -> RecognitionResult:
        baseline_cp = recognition.cp.value
        checkpoint_cp = self._cp_from_summary_detection(checkpoint)
        if (
            baseline_cp is None
            or checkpoint_cp is None
            or baseline_cp == checkpoint_cp
        ):
            return recognition

        maximum_frames = self._automation.rename_cp_consensus_max_frames
        required_matches = self._automation.rename_cp_consensus_required_matches
        if maximum_frames < 1 or required_matches < 2:
            raise AutomationError(
                "Rename CP consensus configuration is unsafe. No nickname editor tap "
                "was sent."
            )

        samples: list[int | None] = []
        counts: dict[int, int] = {}
        trusted_cp: int | None = None
        for sample_index in range(1, maximum_frames + 1):
            self._check_deadline(deadline)
            if sample_index > 1:
                self._sleeper(self._automation.rename_cp_consensus_interval_seconds)
                self._check_deadline(deadline)
            screenshot = self._adb.capture_screen(serial)
            session.recorder.screen(
                f"rename_cp_consensus_{sample_index:02d}",
                screenshot,
            )
            cp = self._detector.read_summary_cp(screenshot)
            samples.append(cp)
            if cp is None or cp < 10:
                continue
            counts[cp] = counts.get(cp, 0) + 1
            if counts[cp] >= required_matches:
                trusted_cp = cp
                break

        session.recorder.rename_cp_consensus(
            baseline_cp=baseline_cp,
            checkpoint_cp=checkpoint_cp,
            samples=samples,
            required_matches=required_matches,
            trusted_cp=trusted_cp,
        )
        if trusted_cp is None:
            raise AutomationError(
                "Conflicting CP checkpoints did not produce a repeated complete CP "
                "within the bounded CP-only retry. The nickname editor was not tapped."
            )

        warning = (
            f"cp changed from {baseline_cp} to {trusted_cp} after bounded pre-rename "
            f"CP-only consensus ({required_matches} matching frames)."
        )
        updated = recognition.model_copy(
            update={
                "cp": RecognizedInteger(
                    value=trusted_cp,
                    raw=None,
                    confidence=None,
                ),
                "warnings": (*recognition.warnings, warning),
            }
        )
        session.write_text(
            session.scan_directory / "recognition.json",
            updated.model_dump_json(indent=2) + "\n",
        )
        return updated

    @staticmethod
    def _cp_from_summary_detection(detection: PageDetection) -> int | None:
        raw = detection.details.get("cp")
        if not isinstance(raw, str):
            return None
        normalized = normalize_cp_candidate(raw)
        return int(normalized[2:]) if normalized is not None else None

    def _clear_nickname(
        self,
        session: _Session,
        serial: str,
        label: str,
        before_state: PageState,
    ) -> None:
        session.recorder.action(
            name=label,
            kind="keyevent",
            coordinates={
                "end": 123,
                "delete": 67,
                "delete_count": self._automation.nickname_maximum_characters,
            },
            executed=True,
            before_state=before_state,
        )
        self._adb.press_key(serial, 123)  # KEYCODE_MOVE_END
        for _ in range(self._automation.nickname_maximum_characters):
            self._adb.press_key(serial, 67)  # KEYCODE_DEL

    def _append_nickname_text(
        self,
        session: _Session,
        serial: str,
        value: str,
        label: str,
        before_state: PageState,
    ) -> None:
        session.recorder.action(
            name=label,
            kind="text",
            coordinates={"end": 123, "unicode_text": value},
            executed=True,
            before_state=before_state,
        )
        self._adb.press_key(serial, 123)  # Ensure the IVs become a suffix.
        self._adb.input_text(serial, value)

    def _confirm_nickname_editor(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        label: str,
        *,
        expected_nickname: str | None = None,
        lightweight_detail: bool = False,
    ) -> _StateWaitResult:
        result = self._poll_for_state(
            session,
            serial,
            f"{label}_ready",
            target=("rename_keyboard", "rename_dialog"),
            expected=("rename_keyboard", "rename_dialog", "unknown"),
            deadline=deadline,
            nickname_controls_only=lightweight_detail,
        )
        if result.outcome != "reached":
            raise AutomationError("Nickname editor was not ready for confirmation.")
        verified_candidates: list[str] = []
        if expected_nickname is not None:
            raw_candidates = result.detection.details.get("nickname_text_candidates")
            candidates = (
                raw_candidates
                if isinstance(raw_candidates, list)
                and all(isinstance(value, str) for value in raw_candidates)
                else []
            )
            verified_candidates = [
                value
                for value in candidates
                if compact_editor_nickname_text(value) == expected_nickname
            ]
            if not verified_candidates:
                raise AutomationError(
                    "Nickname editor text did not exactly match the expected IV nickname. "
                    "The keyboard and dialog confirmations were not tapped."
                )
        if result.detection.state == "rename_keyboard":
            self._dismiss_nickname_keyboard(session, serial, result.detection, label)
            result = self._wait_for_visible_rename_dialog(
                session,
                serial,
                label,
                deadline,
                nickname_controls_only=lightweight_detail,
            )
        detection = result.detection
        self._require_state(detection, ("rename_dialog",))
        target = detection.rename_confirm_target
        if target is None:
            raise AutomationError("Nickname editor has no OCR-confirmed confirmation target.")
        session.recorder.screen(f"before_{label}", result.png_bytes)
        session.recorder.action(
            name=label,
            kind="tap",
            coordinates=asdict(target),
            executed=True,
            before_state=detection.state,
        )
        self._adb.tap(serial, target.x, target.y)
        summary = (
            self._wait_for_lightweight_detail(
                session,
                serial,
                f"{label}_detail_returned",
                deadline,
            )
            if lightweight_detail
            else self._wait_for_detail_summary(session, serial, label, deadline)
        )
        if summary.outcome != "reached":
            raise AutomationError(
                "Nickname confirmation did not return to a lightweight-confirmed "
                "detail page before timeout."
            )
        if verified_candidates:
            summary.detection.details["verified_editor_nickname_candidates"] = list(
                verified_candidates
            )
        return summary

    def _dismiss_nickname_keyboard(
        self,
        session: _Session,
        serial: str,
        detection: PageDetection,
        label: str,
    ) -> None:
        target = detection.rename_keyboard_target
        if target is None:
            raise AutomationError(
                "Nickname keyboard has no OCR-confirmed right-side confirmation target."
            )
        session.recorder.action(
            name=f"{label}_hide_keyboard",
            kind="tap",
            coordinates=asdict(target),
            executed=True,
            before_state="rename_keyboard",
        )
        self._adb.tap(serial, target.x, target.y)

    def _wait_for_rename_dialog(
        self,
        session: _Session,
        serial: str,
        label: str,
        deadline: float,
        *,
        nickname_controls_only: bool = False,
    ) -> _StateWaitResult:
        return self._poll_for_state(
            session,
            serial,
            label,
            target=("rename_keyboard", "rename_dialog"),
            expected=("rename_keyboard", "rename_dialog", "detail_summary", "unknown"),
            deadline=deadline,
            nickname_controls_only=nickname_controls_only,
        )

    def _wait_for_visible_rename_dialog(
        self,
        session: _Session,
        serial: str,
        label: str,
        deadline: float,
        *,
        nickname_controls_only: bool = False,
    ) -> _StateWaitResult:
        return self._poll_for_state(
            session,
            serial,
            f"{label}_keyboard_hidden",
            target="rename_dialog",
            expected=("rename_dialog", "rename_keyboard", "detail_summary", "unknown"),
            deadline=deadline,
            nickname_controls_only=nickname_controls_only,
        )

    def _wait_for_detail_summary(
        self,
        session: _Session,
        serial: str,
        label: str,
        deadline: float,
    ) -> _StateWaitResult:
        result = self._poll_for_state(
            session,
            serial,
            label,
            target="detail_summary",
            expected=("detail_summary", "rename_dialog", "unknown"),
            deadline=deadline,
        )
        if result.outcome != "reached":
            raise AutomationError(
                "Nickname confirmation did not return to detail_summary before timeout."
            )
        return result

    def _wait_for_lightweight_detail(
        self,
        session: _Session,
        serial: str,
        label: str,
        deadline: float,
        *,
        initial: bytes | None = None,
    ) -> _StateWaitResult:
        """Poll detail-button geometry without running name, CP, HP, or move OCR."""

        interval = self._automation.rename_poll_interval_seconds
        timeout = min(
            self._automation.rename_wait_timeout_seconds,
            deadline - self._monotonic(),
        )
        if timeout <= 0:
            self._check_deadline(deadline)
        started = self._monotonic()
        screen = initial if initial is not None else self._adb.capture_screen(serial)
        sample_index = 0
        while True:
            detection = self._detector.detect_detail_page_lightweight(screen)
            session.recorder.detection(f"{label}_{sample_index:02d}", detection)
            session.recorder.screen(f"{label}_{sample_index:02d}", screen)
            if detection.state == "detail_ready":
                return _StateWaitResult(
                    screen,
                    detection,
                    "reached",
                    self._monotonic() - started,
                )
            elapsed = self._monotonic() - started
            if elapsed >= timeout:
                return _StateWaitResult(screen, detection, "timeout", timeout)
            self._sleeper(min(interval, timeout - elapsed))
            self._check_deadline(deadline)
            screen = self._adb.capture_screen(serial)
            sample_index += 1

    def _poll_for_state(
        self,
        session: _Session,
        serial: str,
        label: str,
        *,
        target: PageState | tuple[PageState, ...],
        expected: ExpectedStates,
        deadline: float,
        nickname_controls_only: bool = False,
    ) -> _StateWaitResult:
        target_states = target if isinstance(target, tuple) else (target,)
        interval = self._automation.rename_poll_interval_seconds
        timeout = min(self._automation.rename_wait_timeout_seconds, deadline - self._monotonic())
        if timeout <= 0:
            self._check_deadline(deadline)
        started = self._monotonic()
        def detect(screen: bytes, sample_label: str) -> PageDetection:
            if not nickname_controls_only:
                return self._detect(session, sample_label, screen, expected)
            detection = self._detector.detect_nickname_controls(screen)
            session.recorder.detection(sample_label, detection)
            return detection

        last_screen = self._adb.capture_screen(serial)
        last_detection = detect(last_screen, f"{label}_wait_00")
        for sample_index in range(math.ceil(timeout / interval) + 1):
            if last_detection.state in target_states:
                return _StateWaitResult(
                    last_screen,
                    last_detection,
                    "reached",
                    self._monotonic() - started,
                )
            elapsed = self._monotonic() - started
            if elapsed >= timeout:
                break
            self._sleeper(min(interval, timeout - elapsed))
            last_screen = self._adb.capture_screen(serial)
            last_detection = detect(
                last_screen,
                f"{label}_wait_{sample_index + 1:02d}",
            )
            session.recorder.screen(f"{label}_wait_{sample_index + 1:02d}", last_screen)
        return _StateWaitResult(last_screen, last_detection, "timeout", timeout)

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

    def _detail_gate_detection(
        self,
        session: _Session,
        label: str,
        screen: bytes,
        *,
        lightweight: bool,
        full_states: ExpectedStates,
    ) -> PageDetection:
        if not lightweight:
            return self._detect(session, label, screen, full_states)
        detection = self._detector.detect_detail_page_lightweight(screen)
        session.recorder.detection(label, detection)
        return detection

    def _menu_wait_detection(
        self,
        session: _Session,
        label: str,
        screen: bytes,
        expected: ExpectedStates,
        *,
        lightweight_detail: bool,
    ) -> PageDetection:
        if not lightweight_detail:
            return self._detect(session, label, screen, expected)
        detection = self._detect(
            session,
            label,
            screen,
            ("action_menu", "appraisal_dialogue", "appraisal_bars"),
        )
        if detection.state != "unknown":
            return detection
        detail = self._detector.detect_detail_page_lightweight(screen)
        session.recorder.detection(f"{label}_detail", detail)
        return detail

    def _open_action_menu(
        self,
        session: _Session,
        serial: str,
        deadline: float,
        *,
        lightweight_detail: bool = False,
    ) -> tuple[bytes, PageDetection]:
        detail_states: ExpectedStates = ("detail_moves", "detail_summary")
        before = self._adb.capture_screen(serial)
        session.recorder.screen("before_open_action_menu", before)
        before_detection = self._detail_gate_detection(
            session,
            "before_open_action_menu",
            before,
            lightweight=lightweight_detail,
            full_states=detail_states,
        )
        allowed_detail_states: ExpectedStates = (
            ("detail_ready",) if lightweight_detail else detail_states
        )
        self._require_state(before_detection, allowed_detail_states)
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
                before_detection = self._detail_gate_detection(
                    session,
                    f"before_open_action_menu_attempt_{attempt}",
                    before,
                    lightweight=lightweight_detail,
                    full_states=detail_states,
                )
                self._require_state(before_detection, allowed_detail_states)
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
                lightweight_detail=lightweight_detail,
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
        *,
        lightweight_detail: bool = False,
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
            detection = self._menu_wait_detection(
                session,
                f"{label}_wait_{sample_index:02d}",
                screen,
                expected,
                lightweight_detail=lightweight_detail,
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
            waiting_states: ExpectedStates = (
                ("detail_ready",)
                if lightweight_detail
                else ("detail_moves", "detail_summary")
            )
            if detection.state not in waiting_states:
                return _StateWaitResult(screen, detection, "unexpected", elapsed)
            if measured_elapsed >= timeout:
                break

        if last_screen is None or last_detection is None:
            last_screen = self._adb.capture_screen(serial)
            last_detection = self._menu_wait_detection(
                session,
                f"{label}_timeout",
                last_screen,
                expected,
                lightweight_detail=lightweight_detail,
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
            detection = self._detector.detect_returned_from_appraisal(screen)
            session.recorder.detection(label, detection)

            if detection.state == "detail_returned":
                session.recorder.screen("after_exit_appraisal", screen)
                return

        if last_screen is None:
            last_screen = self._adb.capture_screen(serial)

        session.write_bytes(
            session.scan_directory / "exit_appraisal_timeout.png",
            last_screen,
        )
        session.recorder.screen("exit_appraisal_timeout", last_screen)
        diagnostic = self._detect(
            session,
            "exit_appraisal_timeout_diagnostic",
            last_screen,
            (
                "appraisal_bars",
                "appraisal_dialogue",
                "action_menu",
                "detail_moves",
                "detail_summary",
            ),
        )

        raise AutomationError(
            "The lightweight detector did not confirm return to a detail page within "
            "15 seconds after exiting appraisal. The final full diagnostic state was "
            f"{diagnostic.state}. No additional input was sent."
        )

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

    def _ocr_seconds_total(self) -> float:
        value = getattr(self._reader, "ocr_seconds_total", 0.0)
        return float(value) if isinstance(value, int | float) else 0.0

    @staticmethod
    def _complete_recognition_evidence(
        summary: PageDetection,
        moves: PageDetection,
        appraisal: PageDetection,
    ) -> RecognitionEvidence | None:
        summary_evidence = summary.recognition_evidence
        moves_evidence = moves.recognition_evidence
        appraisal_evidence = appraisal.recognition_evidence
        if not isinstance(summary_evidence, SummaryRecognitionEvidence):
            return None
        if not isinstance(moves_evidence, MovesRecognitionEvidence):
            return None
        if not isinstance(appraisal_evidence, AppraisalRecognitionEvidence):
            return None
        return RecognitionEvidence(
            summary=summary_evidence,
            moves=moves_evidence,
            appraisal=appraisal_evidence,
        )

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
        if not session.persist:
            return
        destination = session.scan_directory / f"{step}.png"
        session.write_bytes(destination, png_bytes)
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
        self._persist_manifest(session)

    def _persist_manifest(self, session: _Session) -> None:
        if session.persist:
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
            self._persist_manifest(session)
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
        if not session.persist:
            return AutomationError(
                f"IV naming failed at '{session.current_step}': {cause} No files were saved."
            )
        manifest_error: PokemonGoCleanupError | None = None
        try:
            session.manifest = session.manifest.model_copy(
                update={
                    "scan_status": "incomplete",
                    "failed_step": session.current_step,
                }
            )
            self._persist_manifest(session)
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
        *,
        nickname_change: NicknameRenameResult | None = None,
    ) -> AutoScanResult:
        return AutoScanResult(
            scan_directory=session.scan_directory.resolve(),
            manifest_path=session.manifest_path.resolve(),
            manifest=session.manifest,
            recognition=recognition,
            dry_run=dry_run,
            planned_actions=actions,
            nickname_change=nickname_change,
        )
