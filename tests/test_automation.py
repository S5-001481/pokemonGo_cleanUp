"""Focused tests for the fixed one-Pokémon automation safety gates."""

from __future__ import annotations

import io
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from pokemon_go_cleanup.automation import (
    AutoScanService,
    HuaweiMate30AutomationConfig,
    PageDetection,
    Point,
    StableScreenResult,
    appraisal_target_is_safe,
    match_move_name_power_rows,
    wait_for_stable_screen,
)
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import AutomationError
from pokemon_go_cleanup.models import Device, ScreenResolution
from pokemon_go_cleanup.recognition import (
    OcrCandidate,
    RecognitionResult,
    RecognizedInteger,
    RecognizedText,
)
from pokemon_go_cleanup.storage import atomic_write_text

JST = timezone(timedelta(hours=9))


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), color).save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png((255, 255, 255))


def _summary_detection(
    evidence: str = "name_cp",
) -> PageDetection:
    """Return a confirmed summary fixture using the current evidence contract."""

    return PageDetection(
        "detail_summary",
        0.99,
        details={"summary_evidence": evidence},
    )


class FakeAutomationAdb:
    def __init__(self) -> None:
        self.inputs: list[tuple[object, ...]] = []
        self.capture_count = 0

    def resolve_device(self, serial_number: str | None = None) -> Device:
        return Device(
            serial_number=serial_number or "ABC123",
            state="device",
            properties={"model": "HUAWEI_Mate_30"},
        )

    def get_resolution(self, serial_number: str) -> ScreenResolution:
        return ScreenResolution(width=1440, height=3120)

    def capture_screen(self, serial_number: str) -> bytes:
        self.capture_count += 1
        return PNG

    def tap(self, serial_number: str, x: int, y: int) -> None:
        self.inputs.append(("tap", x, y))

    def swipe(
        self,
        serial_number: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int,
    ) -> None:
        self.inputs.append(("swipe", x1, y1, x2, y2, duration_ms))

    def press_back(self, serial_number: str) -> None:
        self.inputs.append(("back",))


class QueueDetector:
    def __init__(self, detections: list[PageDetection]) -> None:
        self._detections = deque(detections)

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: tuple[str, ...],
    ) -> PageDetection:
        assert png_bytes == PNG
        assert expected
        return self._detections.popleft()


class MovesAwareQueueDetector(QueueDetector):
    def __init__(self, detections: list[PageDetection]) -> None:
        super().__init__(detections)
        self.moves_seen = False

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: tuple[str, ...],
    ) -> PageDetection:
        detection = super().detect(png_bytes, expected=expected)
        if detection.state == "detail_moves":
            self.moves_seen = True
        return detection


class RejectStabilityBeforeMoves:
    def __init__(self, detector: MovesAwareQueueDetector) -> None:
        self.detector = detector
        self.calls = 0

    def __call__(self, capture: object) -> StableScreenResult:
        assert callable(capture)
        assert self.detector.moves_seen, (
            "full-screen stability was requested before detail_moves appeared"
        )
        self.calls += 1
        return StableScreenResult(capture(), (1.0,))


class InterruptingDetector:
    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: tuple[str, ...],
    ) -> PageDetection:
        assert png_bytes == PNG
        assert expected
        raise KeyboardInterrupt


class FakeReader:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, bool]] = []

    def read_scan(self, directory: Path, *, debug: bool = False) -> RecognitionResult:
        self.calls.append((directory, debug))
        result = RecognitionResult(
            scan_id=directory.name,
            pokemon_name=RecognizedText(value="妙蛙花", raw="妙蛙花", confidence=0.99),
            cp=RecognizedInteger(value=1761, raw="CP1761", confidence=0.99),
            fast_move=RecognizedText(value="藤鞭", raw="藤鞭", confidence=0.99),
            charged_move_1=RecognizedText(value="污泥攻擊", raw="污泥攻擊", confidence=0.99),
            charged_move_2=RecognizedText(value=None, raw=None, confidence=None),
            attack_iv=15,
            defense_iv=13,
            hp_iv=11,
        )
        atomic_write_text(
            directory / "recognition.json",
            result.model_dump_json(indent=2) + "\n",
        )
        return result


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 29, 12, 0, tzinfo=JST)

    def __call__(self) -> datetime:
        result = self.current
        self.current += timedelta(seconds=1)
        return result


def _stable(capture: object) -> StableScreenResult:
    assert callable(capture)
    return StableScreenResult(capture(), (0.0,))


def test_wait_for_stable_screen_requires_consecutive_low_differences() -> None:
    frames = iter(
        [
            _png((0, 0, 0)),
            _png((255, 255, 255)),
            _png((255, 255, 255)),
            _png((255, 255, 255)),
            _png((255, 255, 255)),
        ]
    )

    result = wait_for_stable_screen(
        lambda: next(frames),
        interval_seconds=0.1,
        consecutive_samples=3,
        timeout_seconds=2,
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    assert result.png_bytes == _png((255, 255, 255))
    assert result.differences[0] > 0.9
    assert result.differences[-3:] == (0.0, 0.0, 0.0)


def test_wait_for_stable_screen_reports_timeout() -> None:
    frames = deque([_png((0, 0, 0)), _png((255, 255, 255))])

    def capture() -> bytes:
        frame = frames[0]
        frames.rotate(-1)
        return frame

    with pytest.raises(AutomationError, match="did not stabilize"):
        wait_for_stable_screen(
            capture,
            interval_seconds=0.1,
            consecutive_samples=2,
            timeout_seconds=0.3,
            monotonic=lambda: 0.0,
            sleeper=lambda _: None,
        )


def test_appraisal_target_rejects_transfer_proximity_and_forbidden_band() -> None:
    assert appraisal_target_is_safe(Point(700, 2400), (Point(700, 2800),))
    assert not appraisal_target_is_safe(Point(700, 2650), (Point(700, 2740),))
    assert not appraisal_target_is_safe(Point(700, 2800), ())


def test_detail_moves_matches_name_and_same_row_power_without_anchor() -> None:
    candidates = (
        OcrCandidate("吸取", 0.98, (190, 1190, 340, 1270)),
        OcrCandidate("20", 0.99, (1250, 1195, 1340, 1270)),
        OcrCandidate("種子炸彈", 0.97, (190, 1320, 465, 1400)),
        OcrCandidate("55", 0.99, (1250, 1325, 1340, 1395)),
    )

    matches = match_move_name_power_rows(candidates)

    assert [(name.raw, power.raw) for name, power in matches] == [
        ("吸取", "20"),
        ("種子炸彈", "55"),
    ]


def test_detail_moves_does_not_treat_appraisal_labels_as_move_rows() -> None:
    candidates = (
        OcrCandidate("睡睡菇", 0.99, (560, 1395, 875, 1515)),
        OcrCandidate("67/67 HP", 0.99, (625, 1585, 815, 1630)),
        OcrCandidate("攻擊", 0.99, (160, 2160, 260, 2220)),
        OcrCandidate("防禦", 0.99, (160, 2295, 260, 2360)),
    )

    assert match_move_name_power_rows(candidates) == ()


def test_dry_run_captures_summary_but_sends_no_input(tmp_path: Path) -> None:
    adb = FakeAutomationAdb()
    reader = FakeReader()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        QueueDetector([_summary_detection()]),
        reader,
        clock=Clock(),
        token_factory=lambda: "dryrun",
        stable_waiter=_stable,
        monotonic=lambda: 0.0,
    )

    result = service.scan_one(debug=True, dry_run=True)

    assert result.dry_run
    assert adb.inputs == []
    assert reader.calls == []
    assert (result.scan_directory / "summary.png").is_file()
    assert not (result.scan_directory / "moves.png").exists()
    assert result.manifest.scan_status == "incomplete"
    assert result.manifest.failed_step == "dry_run"
    debug_directory = result.scan_directory / "debug" / "automation"
    assert (debug_directory / "00_initial.png").is_file()
    assert (debug_directory / "plan.json").is_file()


def test_unknown_initial_page_stops_before_any_input(tmp_path: Path) -> None:
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        QueueDetector(
            [
                PageDetection("unknown", 0.0),
                PageDetection("unknown", 0.0),
            ]
        ),
        FakeReader(),
        automation=HuaweiMate30AutomationConfig(
            summary_wait_timeout_seconds=0.5,
        ),
        clock=Clock(),
        token_factory=lambda: "unknown",
        stable_waiter=_stable,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(AutomationError, match="verify_detail_summary"):
        service.scan_one()

    assert adb.inputs == []
    manifest_path = next((tmp_path / "scans").glob("*/*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["scan_status"] == "incomplete"
    assert manifest["failed_step"] == "verify_detail_summary"


def test_ctrl_c_marks_manifest_incomplete_without_input(tmp_path: Path) -> None:
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        InterruptingDetector(),
        FakeReader(),
        clock=Clock(),
        token_factory=lambda: "interrupt",
        stable_waiter=_stable,
        monotonic=lambda: 0.0,
    )

    with pytest.raises(KeyboardInterrupt):
        service.scan_one()

    assert adb.inputs == []
    manifest_path = next((tmp_path / "scans").glob("*/*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["scan_status"] == "incomplete"
    assert manifest["failed_step"] == "verify_detail_summary"


def test_live_flow_sends_only_gated_single_scan_actions(tmp_path: Path) -> None:
    menu = PageDetection(
        "action_menu",
        0.99,
        matched_texts=("調查寶可夢", "傳送"),
        appraisal_target=Point(700, 2400),
    )
    detector = MovesAwareQueueDetector(
        [
            _summary_detection(),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            menu,
            PageDetection("appraisal_dialogue", 0.95),
            PageDetection("appraisal_dialogue", 0.95),
            PageDetection("appraisal_bars", 1.0),
            PageDetection("appraisal_bars", 1.0),
            _summary_detection(),
        ]
    )
    adb = FakeAutomationAdb()
    reader = FakeReader()
    stability = RejectStabilityBeforeMoves(detector)
    app_config = AppConfig(data_dir=tmp_path)
    assert not hasattr(app_config, "appraisal_exit")
    service = AutoScanService(
        app_config,
        adb,
        detector,
        reader,
        clock=Clock(),
        token_factory=lambda: "live",
        stable_waiter=stability,
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    result = service.scan_one(debug=True)

    assert result.manifest.scan_status == "complete"
    assert result.recognition is not None
    assert stability.calls == 0
    assert [action[0] for action in adb.inputs] == [
        "swipe",
        "tap",
        "tap",
        "tap",
        "tap",
    ]
    assert adb.inputs[1] == ("tap", 1244, 2772)
    assert adb.inputs[2] == ("tap", 700, 2400)
    assert adb.inputs[-1] == ("tap", 720, 1560)
    assert ("back",) not in adb.inputs
    assert all(
        (result.scan_directory / name).is_file()
        for name in ("summary.png", "moves.png", "appraisal.png", "recognition.json")
    )
    actions_path = result.scan_directory / "debug" / "automation" / "actions.json"
    actions = json.loads(actions_path.read_text(encoding="utf-8"))["actions"]
    assert len(actions) == 5
    exit_action = next(action for action in actions if action["name"] == "exit_appraisal")
    assert exit_action == {
        "name": "exit_appraisal",
        "kind": "tap",
        "coordinates": {"x": 720, "y": 1560},
        "executed": True,
        "before_state": "appraisal_bars",
    }
    assert not any("scroll_to_menu" in str(action) for action in actions)
    assert not any("傳送" in str(action) for action in actions)
    debug_directory = result.scan_directory / "debug" / "automation"
    assert (debug_directory / "before_open_action_menu.png").is_file()
    assert (debug_directory / "after_open_action_menu_attempt_1.png").is_file()
    assert (debug_directory / "open_action_menu_attempt_1_state.json").is_file()
    assert (debug_directory / "open_action_menu_attempt_1_wait.json").is_file()
    assert (debug_directory / "before_scroll_to_moves_attempt_1.png").is_file()
    assert (debug_directory / "scroll_to_moves_attempt_1_poll_01.png").is_file()
    assert (debug_directory / "scroll_to_moves_attempt_1_states.json").is_file()
    assert not (debug_directory / "stability_scroll_to_moves.json").exists()


def test_menu_open_retries_once_only_after_detail_state_timeout(
    tmp_path: Path,
) -> None:
    menu = PageDetection(
        "action_menu",
        0.99,
        matched_texts=("調查寶可夢",),
        appraisal_target=Point(700, 2400),
    )
    detector = QueueDetector(
        [
            _summary_detection(),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            menu,
            PageDetection("appraisal_bars", 1.0),
            PageDetection("appraisal_bars", 1.0),
            PageDetection("detail_moves", 0.99),
        ]
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(),
        automation=HuaweiMate30AutomationConfig(
            menu_wait_timeout_seconds=1.0,
        ),
        clock=Clock(),
        token_factory=lambda: "retry",
        stable_waiter=_stable,
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    result = service.scan_one(debug=True)

    menu_taps = [item for item in adb.inputs if item == ("tap", 1244, 2772)]
    assert len(menu_taps) == 2
    debug_directory = result.scan_directory / "debug" / "automation"
    assert (debug_directory / "after_open_action_menu_attempt_2.png").is_file()
    assert (debug_directory / "open_action_menu_attempt_2_state.json").is_file()
    assert (debug_directory / "open_action_menu_attempt_2_wait.json").is_file()


def test_scroll_to_moves_retries_only_after_summary_timeout(tmp_path: Path) -> None:
    menu = PageDetection(
        "action_menu",
        0.99,
        matched_texts=("調查寶可夢",),
        appraisal_target=Point(700, 2400),
    )
    detector = QueueDetector(
        [
            _summary_detection(),
            PageDetection("unknown", 0.0),
            _summary_detection(),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            menu,
            PageDetection("appraisal_bars", 1.0),
            PageDetection("appraisal_bars", 1.0),
            _summary_detection(),
        ]
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(),
        automation=HuaweiMate30AutomationConfig(moves_wait_timeout_seconds=0.5),
        clock=Clock(),
        token_factory=lambda: "moves-retry",
        stable_waiter=_stable,
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    result = service.scan_one(debug=True)

    scrolls = [item for item in adb.inputs if item[0] == "swipe"]
    assert len(scrolls) == 2
    debug_directory = result.scan_directory / "debug" / "automation"
    assert (debug_directory / "before_scroll_to_moves_attempt_2.png").is_file()
    assert (debug_directory / "scroll_to_moves_attempt_2_states.json").is_file()


def test_scroll_to_moves_unknown_timeout_never_blindly_retries(
    tmp_path: Path,
) -> None:
    detector = QueueDetector(
        [
            _summary_detection(),
            PageDetection("unknown", 0.0),
            PageDetection("unknown", 0.0),
            PageDetection("unknown", 0.0),
        ]
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(),
        automation=HuaweiMate30AutomationConfig(moves_wait_timeout_seconds=0.5),
        clock=Clock(),
        token_factory=lambda: "moves-unknown",
        stable_waiter=_stable,
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    with pytest.raises(AutomationError, match="No second swipe was sent"):
        service.scan_one(debug=True)

    assert [item[0] for item in adb.inputs] == ["swipe"]
    manifest_path = next((tmp_path / "scans").glob("*/*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["failed_step"] == "scroll_to_moves"
    debug_directory = manifest_path.parent / "debug" / "automation"
    assert (debug_directory / "scroll_to_moves_attempt_1_poll_01.png").is_file()
    assert (debug_directory / "scroll_to_moves_attempt_1_states.json").is_file()
