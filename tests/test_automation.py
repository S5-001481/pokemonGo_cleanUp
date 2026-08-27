"""Focused tests for the fixed one-Pokémon automation safety gates."""

from __future__ import annotations

import io
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from pokemon_go_cleanup.automation import (
    AutoScanService,
    HuaweiMate30AutomationConfig,
    HuaweiMate30PageDetector,
    PageDetection,
    Point,
    appraisal_target_is_safe,
    build_iv_nickname,
    compact_editor_nickname_text,
    compact_iv_suffix,
    match_move_name_power_rows,
    planned_actions,
)
from pokemon_go_cleanup.config import AppConfig
from pokemon_go_cleanup.exceptions import AutomationError
from pokemon_go_cleanup.models import Device, ScreenResolution
from pokemon_go_cleanup.recognition import (
    CP_RECT,
    NAME_RECT,
    AppraisalRecognitionEvidence,
    BarDetection,
    MovesRecognitionEvidence,
    OcrCandidate,
    RecognitionEvidence,
    RecognitionResult,
    RecognitionService,
    RecognizedInteger,
    RecognizedText,
    SummaryRecognitionEvidence,
)
from pokemon_go_cleanup.storage import atomic_write_text

JST = timezone(timedelta(hours=9))
REAL_GOLD_RESUME_SCREEN = (
    Path(__file__).parents[1]
    / "data/scans/2026-08-26/20260826_191504_866082_20bedd09a527482db74183ff7c2ecf6e"
    / "debug/batch/resume_current.png"
)
REAL_MOUSE_RENAMED_SUMMARY = (
    Path(__file__).parents[1]
    / "data/scans/2026-08-27/20260827_185253_573760_81b3c80b7b094ff29dd3c90e31790069"
    / "renamed_summary.png"
)
REAL_MOUSE_SECOND_RENAMED_SUMMARY = (
    Path(__file__).parents[1]
    / "data/scans/2026-08-27/20260827_211507_528118_e137f8b37b254daf8616110674bccb73"
    / "renamed_summary.png"
)
REAL_SHIFTED_APPRAISAL = (
    Path(__file__).parents[1]
    / "data/scans/2026-08-27/20260827_224510_336106_4d6062a9fed44d7eb678fa76bcea62f2"
    / "debug/automation/appraisal_bars_wait_01.png"
)


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


def _summary_detection_with_recognition() -> PageDetection:
    name = OcrCandidate("妙蛙花", 0.99, (600, 1350, 840, 1450))
    cp = OcrCandidate("CP1761", 0.99, (450, 270, 890, 448))
    return PageDetection(
        "detail_summary",
        0.99,
        matched_texts=(cp.raw, name.raw),
        details={"summary_evidence": "name_cp"},
        recognition_evidence=SummaryRecognitionEvidence(
            png_sha256=sha256(PNG).hexdigest(),
            name_candidates=(name,),
            cp_candidates=(cp,),
            name_debug=object(),
            cp_debug=object(),
        ),
    )


def _moves_detection_with_recognition() -> PageDetection:
    moves = (
        OcrCandidate("藤鞭", 0.99, (400, 1500, 600, 1600)),
        OcrCandidate("污泥攻擊", 0.99, (400, 1700, 700, 1800)),
    )
    return PageDetection(
        "detail_moves",
        0.99,
        matched_texts=tuple(move.raw for move in moves),
        recognition_evidence=MovesRecognitionEvidence(
            png_sha256=sha256(PNG).hexdigest(),
            candidates=moves,
            warnings=(),
            debug=(object(), object()),
            fallback_debug=object(),
        ),
    )


def _appraisal_detection_with_recognition() -> PageDetection:
    bars = (
        BarDetection(2150, 2180, 665, 15),
        BarDetection(2290, 2320, 602, 13),
        BarDetection(2430, 2460, 537, 11),
    )
    return PageDetection(
        "appraisal_bars",
        1.0,
        recognition_evidence=AppraisalRecognitionEvidence(
            png_sha256=sha256(PNG).hexdigest(),
            bars=bars,
            bars_debug=object(),
            detection_debug=object(),
        ),
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

    def press_key(self, serial_number: str, keycode: int) -> None:
        self.inputs.append(("keyevent", keycode))

    def input_text(self, serial_number: str, value: str) -> None:
        self.inputs.append(("text", value))


class QueueDetector:
    def __init__(
        self,
        detections: list[PageDetection],
        *,
        summary_nickname: str = "妙蛙花15/13/11",
        summary_nicknames: tuple[str, ...] = (),
        summary_cps: tuple[int | None, ...] = (),
        returned_detections: tuple[PageDetection, ...] = (),
    ) -> None:
        self._detections = deque(detections)
        self.summary_nickname = summary_nickname
        self._summary_nicknames = deque(summary_nicknames)
        self._summary_cps = deque(summary_cps)
        self._returned_detections = deque(returned_detections)
        self.returned_calls = 0
        self.summary_nickname_calls = 0
        self.summary_cp_calls = 0
        self.summary_hp_calls = 0

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: tuple[str, ...],
    ) -> PageDetection:
        assert png_bytes == PNG
        assert expected
        return self._detections.popleft()

    def detect_returned_from_appraisal(self, png_bytes: bytes) -> PageDetection:
        assert png_bytes == PNG
        self.returned_calls += 1
        if self._returned_detections:
            return self._returned_detections.popleft()
        detection = self._detections.popleft()
        if detection.state in ("detail_summary", "detail_moves"):
            return PageDetection(
                "detail_returned",
                detection.confidence,
                details={"test_evidence": "fixed_detail_buttons"},
            )
        return detection

    def read_summary_nickname(self, png_bytes: bytes) -> str:
        assert png_bytes == PNG
        self.summary_nickname_calls += 1
        if self._summary_nicknames:
            return self._summary_nicknames.popleft()
        return self.summary_nickname

    def read_summary_nickname_for_expected(
        self,
        png_bytes: bytes,
        expected_nickname: str,
    ) -> str:
        return self.read_summary_nickname(png_bytes)

    def read_summary_cp(self, png_bytes: bytes) -> int | None:
        assert png_bytes == PNG
        self.summary_cp_calls += 1
        return self._summary_cps.popleft() if self._summary_cps else None

    def read_summary_hp(self, png_bytes: bytes) -> str | None:
        assert png_bytes == PNG
        self.summary_hp_calls += 1
        return None

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

    def detect_returned_from_appraisal(self, png_bytes: bytes) -> PageDetection:
        raise KeyboardInterrupt

    def read_summary_nickname(self, png_bytes: bytes) -> str:
        raise KeyboardInterrupt

    def read_summary_nickname_for_expected(
        self,
        png_bytes: bytes,
        expected_nickname: str,
    ) -> str:
        raise KeyboardInterrupt

    def read_summary_cp(self, png_bytes: bytes) -> int | None:
        raise KeyboardInterrupt

    def read_summary_hp(self, png_bytes: bytes) -> str | None:
        raise KeyboardInterrupt


class ProgressiveSummaryReader:
    def __init__(
        self,
        *,
        fast_cp: tuple[OcrCandidate, ...],
        name: tuple[OcrCandidate, ...],
        hsv_cp: tuple[OcrCandidate, ...] = (),
        fallback_cp: tuple[OcrCandidate, ...] = (),
    ) -> None:
        self.fast_cp = fast_cp
        self.name = name
        self.hsv_cp = hsv_cp
        self.fallback_cp = fallback_cp
        self.variant_calls: list[tuple[int, int, int, int]] = []
        self.hsv_calls = 0
        self.fallback_calls = 0

    def _ocr_variants(
        self,
        image: object,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[tuple[OcrCandidate, ...], object]:
        self.variant_calls.append(rectangle)
        candidates = self.fast_cp if rectangle == CP_RECT else self.name
        return candidates, image

    def _ocr_cp_fallback_variants(
        self,
        image: object,
    ) -> tuple[OcrCandidate, ...]:
        self.fallback_calls += 1
        return self.fallback_cp

    def _ocr_cp_hsv_variants(
        self,
        image: object,
    ) -> tuple[OcrCandidate, ...]:
        self.hsv_calls += 1
        return self.hsv_cp

    _best_cp = staticmethod(RecognitionService._best_cp)
    _best_text = staticmethod(RecognitionService._best_text)
    _ordinary_cp_candidate_is_reliable = staticmethod(
        RecognitionService._ordinary_cp_candidate_is_reliable
    )


class ProgressiveSummaryDetector(HuaweiMate30PageDetector):
    def __init__(
        self,
        reader: ProgressiveSummaryReader,
        hp_candidates: tuple[OcrCandidate, ...] = (),
    ) -> None:
        self._reader = reader  # type: ignore[assignment]
        self._config = HuaweiMate30AutomationConfig()
        self.hp_candidates = hp_candidates
        self.hp_calls = 0

    def _decode(self, png_bytes: bytes) -> object:
        assert png_bytes == PNG
        return object()

    def _ocr_rectangle(
        self,
        image: object,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[OcrCandidate, ...]:
        self.hp_calls += 1
        return self.hp_candidates


class FakeReturnedReader:
    def __init__(self, *, appraisal_bars_present: bool) -> None:
        self.appraisal_bars_present = appraisal_bars_present

    def _ivs(
        self,
        _image: object,
    ) -> tuple[
        tuple[BarDetection, BarDetection, BarDetection] | None,
        object,
        object,
    ]:
        bars = None
        if self.appraisal_bars_present:
            bar = BarDetection(y_start=2150, y_end=2180, endpoint=300, value=5)
            bars = (bar, bar, bar)
        return bars, object(), object()


def returned_detector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    appraisal_bars_present: bool,
    button_metrics: tuple[dict[str, float], ...] = (),
) -> tuple[HuaweiMate30PageDetector, list[Point]]:
    detector = object.__new__(HuaweiMate30PageDetector)
    detector._reader = FakeReturnedReader(  # type: ignore[assignment]
        appraisal_bars_present=appraisal_bars_present
    )
    detector._config = HuaweiMate30AutomationConfig()
    observed_centers: list[Point] = []
    metrics = deque(button_metrics)
    monkeypatch.setattr(detector, "_decode", lambda _png: object())

    def detail_button_metrics(
        _image: object,
        center: Point,
        _radius: int,
    ) -> dict[str, float]:
        observed_centers.append(center)
        return metrics.popleft()

    monkeypatch.setattr(detector, "_detail_button_metrics", detail_button_metrics)
    return detector, observed_centers


class FakeReader:
    def __init__(
        self,
        *,
        pokemon_name: str = "妙蛙花",
        cp: int = 1761,
        attack_iv: int = 15,
        defense_iv: int = 13,
        hp_iv: int = 11,
    ) -> None:
        self.calls: list[tuple[Path, bool]] = []
        self.evidence_calls: list[tuple[Path, RecognitionEvidence, bool]] = []
        self.accept_evidence = False
        self.ocr_seconds_total = 0.0
        self.pokemon_name = pokemon_name
        self.cp = cp
        self.attack_iv = attack_iv
        self.defense_iv = defense_iv
        self.hp_iv = hp_iv

    def _result(self, directory: Path) -> RecognitionResult:
        result = RecognitionResult(
            scan_id=directory.name,
            pokemon_name=RecognizedText(
                value=self.pokemon_name,
                raw=self.pokemon_name,
                confidence=0.99,
            ),
            cp=RecognizedInteger(value=self.cp, raw=f"CP{self.cp}", confidence=0.99),
            fast_move=RecognizedText(value="藤鞭", raw="藤鞭", confidence=0.99),
            charged_move_1=RecognizedText(value="污泥攻擊", raw="污泥攻擊", confidence=0.99),
            charged_move_2=RecognizedText(value=None, raw=None, confidence=None),
            attack_iv=self.attack_iv,
            defense_iv=self.defense_iv,
            hp_iv=self.hp_iv,
        )
        return result

    def read_scan(self, directory: Path, *, debug: bool = False) -> RecognitionResult:
        self.calls.append((directory, debug))
        self.ocr_seconds_total += 1.25
        result = self._result(directory)
        atomic_write_text(
            directory / "recognition.json",
            result.model_dump_json(indent=2) + "\n",
        )
        return result

    def read_evidence(
        self,
        directory: Path,
        evidence: RecognitionEvidence,
        *,
        debug: bool = False,
    ) -> RecognitionResult | None:
        self.evidence_calls.append((directory, evidence, debug))
        if not self.accept_evidence:
            return None
        result = self._result(directory)
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


class AdvancingMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


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


def test_summary_nickname_reader_uses_tight_row_and_combines_split_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the real long-name crop that retains a slash-separated middle IV."""

    detector = object.__new__(HuaweiMate30PageDetector)
    detector._config = HuaweiMate30AutomationConfig()
    observed_rectangles: list[tuple[int, int, int, int]] = []
    candidates = (
        OcrCandidate("飄飄球12", 0.999, (339, 1379, 803, 1531)),
        OcrCandidate("2", 0.999, (855, 1403, 928, 1503)),
        OcrCandidate("5", 0.999, (1001, 1400, 1076, 1502)),
        OcrCandidate("✎", 0.9, (1110, 1510, 1180, 1580)),
    )

    monkeypatch.setattr(detector, "_decode", lambda _png: object())

    def ocr_rectangle(
        _image: object,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[OcrCandidate, ...]:
        observed_rectangles.append(rectangle)
        return candidates

    monkeypatch.setattr(detector, "_ocr_rectangle", ocr_rectangle)

    assert detector.read_summary_nickname(PNG) == "飄飄球1225"
    assert observed_rectangles == [(150, 1300, 1290, 1550)]


def test_summary_nickname_reader_excludes_date_badge_and_small_row_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = object.__new__(HuaweiMate30PageDetector)
    detector._config = HuaweiMate30AutomationConfig()
    candidates = (
        OcrCandidate("1", 0.9679, (1259, 1325, 1289, 1377)),
        OcrCandidate("哈力栗", 0.99847, (540, 1374, 895, 1536)),
        OcrCandidate("7", 0.99, (1100, 1424, 1130, 1476)),
    )

    monkeypatch.setattr(detector, "_decode", lambda _png: object())
    monkeypatch.setattr(
        detector,
        "_ocr_rectangle",
        lambda _image, _rectangle: candidates,
    )

    assert detector.read_summary_nickname(PNG) == "哈力栗"


def progressive_wide_nickname_detector(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sharpened: tuple[OcrCandidate, ...],
    raw_color: tuple[OcrCandidate, ...],
) -> tuple[HuaweiMate30PageDetector, list[str]]:
    detector = object.__new__(HuaweiMate30PageDetector)
    detector._config = HuaweiMate30AutomationConfig()
    calls: list[str] = []
    monkeypatch.setattr(detector, "_decode", lambda _png: object())

    def ocr_rectangle(
        _image: object,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[OcrCandidate, ...]:
        assert rectangle == detector._config.nickname_summary_rect
        calls.append("sharpened")
        return sharpened

    def ocr_raw_color(_image: object) -> tuple[OcrCandidate, ...]:
        calls.append("raw_color")
        return raw_color

    monkeypatch.setattr(detector, "_ocr_rectangle", ocr_rectangle)
    monkeypatch.setattr(detector, "_ocr_wide_nickname_raw_color", ocr_raw_color)
    return detector, calls


def test_final_wide_nickname_skips_raw_fallback_after_sharpened_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "一對鼠14/15/15"
    detector, calls = progressive_wide_nickname_detector(
        monkeypatch,
        sharpened=(OcrCandidate(expected, 0.95, (419, 1373, 1105, 1541)),),
        raw_color=(),
    )

    assert detector.read_summary_nickname_for_expected(PNG, expected) == expected
    assert calls == ["sharpened"]


def test_final_wide_nickname_uses_lower_confidence_raw_exact_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "一對鼠14/15/15"
    detector, calls = progressive_wide_nickname_detector(
        monkeypatch,
        sharpened=(OcrCandidate("-對鼠14/15/15", 0.999, (419, 1375, 1104, 1533)),),
        raw_color=(OcrCandidate(expected, 0.80, (419, 1373, 1105, 1541)),),
    )

    assert detector.read_summary_nickname_for_expected(PNG, expected) == expected
    assert calls == ["sharpened", "raw_color"]


@pytest.mark.parametrize(
    "raw_value",
    ["錯誤14/15/15", "一對鼠14/15/1"],
    ids=("both_wrong", "raw_prefix_only"),
)
def test_final_wide_nickname_fails_closed_when_raw_does_not_fully_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_value: str,
) -> None:
    expected = "一對鼠14/15/15"
    detector, calls = progressive_wide_nickname_detector(
        monkeypatch,
        sharpened=(OcrCandidate("-對鼠14/15/15", 0.99, (419, 1375, 1104, 1533)),),
        raw_color=(OcrCandidate(raw_value, 0.98, (419, 1373, 1105, 1541)),),
    )
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        FakeAutomationAdb(),
        detector,
        FakeReader(),
    )

    with pytest.raises(AutomationError, match="did not show the expected nickname"):
        service._verify_final_summary_nickname(PNG, expected)

    assert calls == ["sharpened", "raw_color"]


@pytest.mark.skipif(
    not REAL_MOUSE_RENAMED_SUMMARY.is_file(),
    reason="local ignored 一對鼠 renamed summary is unavailable",
)
def test_real_mouse_final_wide_nickname_replays_through_raw_fallback() -> None:
    detector = HuaweiMate30PageDetector(RecognitionService())
    screenshot = REAL_MOUSE_RENAMED_SUMMARY.read_bytes()
    expected = "一對鼠14/15/15"

    assert detector.read_summary_nickname(screenshot) == "-對鼠14/15/15"
    assert detector.read_summary_nickname_for_expected(screenshot, expected) == expected


@pytest.mark.skipif(
    not REAL_MOUSE_SECOND_RENAMED_SUMMARY.is_file(),
    reason="local ignored second 一對鼠 renamed summary is unavailable",
)
def test_second_real_mouse_final_wide_nickname_replays_at_raw_two_times() -> None:
    detector = HuaweiMate30PageDetector(RecognitionService())
    screenshot = REAL_MOUSE_SECOND_RENAMED_SUMMARY.read_bytes()
    expected = "一對鼠15/14/12"

    assert detector.read_summary_nickname(screenshot) == "-對鼠15/14/12"
    assert detector.read_summary_nickname_for_expected(screenshot, expected) == expected


def test_appraisal_greeting_is_a_direct_dialogue_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = object.__new__(HuaweiMate30PageDetector)
    detector._config = HuaweiMate30AutomationConfig()
    observed_rectangles: list[tuple[int, int, int, int]] = []

    def ocr_rectangle(
        _image: object,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[OcrCandidate, ...]:
        observed_rectangles.append(rectangle)
        return (OcrCandidate("你好", 0.999, (100, 2500, 250, 2580)),)

    monkeypatch.setattr(detector, "_ocr_rectangle", ocr_rectangle)

    result = detector._detect_appraisal_greeting(object())

    assert result == PageDetection(
        state="appraisal_dialogue",
        confidence=0.999,
        matched_texts=("你好",),
        details={
            "dialogue_evidence": "greeting",
            "greeting_roi": [80, 2450, 420, 2615],
        },
    )
    assert observed_rectangles == [(80, 2450, 420, 2615)]


@pytest.mark.skipif(
    not REAL_SHIFTED_APPRAISAL.is_file(),
    reason="local ignored shifted 来悲茶 appraisal frame is unavailable",
)
def test_real_shifted_appraisal_has_priority_over_dialogue_ocr() -> None:
    detector = HuaweiMate30PageDetector(RecognitionService())

    result = detector.detect(
        REAL_SHIFTED_APPRAISAL.read_bytes(),
        expected=("appraisal_bars", "appraisal_dialogue"),
    )

    assert result.state == "appraisal_bars"
    assert result.details == {
        "iv_values": [14, 11, 14],
        "iv_geometry_path": "upward_fallback",
    }


def test_appraisal_entry_checks_greeting_before_action_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingBarsReader:
        @staticmethod
        def _ivs(_image: object) -> tuple[None, object, object]:
            return None, object(), object()

    detector = object.__new__(HuaweiMate30PageDetector)
    detector._reader = MissingBarsReader()  # type: ignore[assignment]
    detector._config = HuaweiMate30AutomationConfig()
    greeting = PageDetection(
        "appraisal_dialogue",
        0.999,
        matched_texts=("你好",),
    )
    monkeypatch.setattr(detector, "_decode", lambda _png: object())
    monkeypatch.setattr(detector, "_detect_appraisal_greeting", lambda _image: greeting)

    def unexpected_menu_check(_image: object) -> None:
        raise AssertionError("menu OCR must not run after greeting success")

    monkeypatch.setattr(detector, "_detect_action_menu", unexpected_menu_check)

    assert detector.detect(
        PNG,
        expected=("appraisal_bars", "appraisal_dialogue", "action_menu"),
    ) == greeting


def test_action_menu_polling_does_not_pay_for_greeting_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector = object.__new__(HuaweiMate30PageDetector)
    detector._config = HuaweiMate30AutomationConfig()
    menu = PageDetection("action_menu", 0.99)
    monkeypatch.setattr(detector, "_decode", lambda _png: object())

    def unexpected_greeting_check(_image: object) -> None:
        raise AssertionError("ordinary menu polling must not run greeting OCR")

    monkeypatch.setattr(
        detector,
        "_detect_appraisal_greeting",
        unexpected_greeting_check,
    )
    monkeypatch.setattr(detector, "_detect_action_menu", lambda _image: menu)

    assert detector.detect(
        PNG,
        expected=("action_menu", "appraisal_dialogue"),
    ) == menu


@pytest.mark.parametrize(
    ("raw", "confidence"),
    (("你女子", 0.999), ("你好", 0.84)),
)
def test_appraisal_greeting_requires_exact_text_and_confidence(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    confidence: float,
) -> None:
    detector = object.__new__(HuaweiMate30PageDetector)
    detector._config = HuaweiMate30AutomationConfig()
    monkeypatch.setattr(
        detector,
        "_ocr_rectangle",
        lambda _image, _rectangle: (
            OcrCandidate(raw, confidence, (100, 2500, 250, 2580)),
        ),
    )

    assert detector._detect_appraisal_greeting(object()) is None


def test_summary_fast_name_cp_skips_enhanced_cp_and_hp() -> None:
    reader = ProgressiveSummaryReader(
        fast_cp=(OcrCandidate("CP300", 0.98, (500, 300, 650, 360)),),
        name=(OcrCandidate("冰雪龍", 0.99, (600, 1350, 840, 1450)),),
    )
    detector = ProgressiveSummaryDetector(reader)

    result = detector.detect(PNG, expected=("detail_summary",))

    assert result.state == "detail_summary"
    assert result.matched_texts == ("CP300", "冰雪龍")
    assert result.details == {
        "cp": "CP300",
        "summary_evidence": "name_cp",
        "summary_ocr_path": "fast_name_cp",
    }
    assert reader.variant_calls == [CP_RECT, NAME_RECT]
    assert reader.hsv_calls == 0
    assert reader.fallback_calls == 0
    assert detector.hp_calls == 0


def test_summary_runs_hsv_cp_only_after_fast_cp_fails() -> None:
    reader = ProgressiveSummaryReader(
        fast_cp=(),
        name=(OcrCandidate("冰雪龍", 0.99, (600, 1350, 840, 1450)),),
        hsv_cp=(OcrCandidate("CP300", 0.97, (500, 300, 650, 360)),),
    )
    detector = ProgressiveSummaryDetector(reader)

    result = detector.detect(PNG, expected=("detail_summary",))

    assert result.state == "detail_summary"
    assert result.details["summary_evidence"] == "name_cp"
    assert result.details["summary_ocr_path"] == "hsv_cp_fallback"
    assert reader.variant_calls == [CP_RECT, NAME_RECT]
    assert reader.hsv_calls == 1
    assert reader.fallback_calls == 0
    assert detector.hp_calls == 0


def test_summary_runs_threshold_cp_only_after_hsv_cp_fails() -> None:
    reader = ProgressiveSummaryReader(
        fast_cp=(),
        name=(OcrCandidate("冰雪龍", 0.99, (600, 1350, 840, 1450)),),
        fallback_cp=(OcrCandidate("CP300", 0.97, (500, 300, 650, 360)),),
    )
    detector = ProgressiveSummaryDetector(reader)

    result = detector.detect(PNG, expected=("detail_summary",))

    assert result.state == "detail_summary"
    assert result.details["summary_ocr_path"] == "enhanced_cp_fallback"
    assert reader.hsv_calls == 1
    assert reader.fallback_calls == 1
    assert detector.hp_calls == 0


def test_summary_runs_hp_only_after_all_cp_paths_fail() -> None:
    reader = ProgressiveSummaryReader(
        fast_cp=(),
        name=(OcrCandidate("冰雪龍", 0.99, (600, 1350, 840, 1450)),),
    )
    detector = ProgressiveSummaryDetector(
        reader,
        hp_candidates=(
            OcrCandidate("57 / 57 HP", 0.96, (550, 1530, 880, 1610)),
        ),
    )

    result = detector.detect(PNG, expected=("detail_summary",))

    assert result.state == "detail_summary"
    assert result.matched_texts == ("冰雪龍",)
    assert result.details == {
        "hp": "57/57HP",
        "summary_evidence": "name_hp",
        "summary_ocr_path": "hp_fallback",
    }
    assert reader.variant_calls == [CP_RECT, NAME_RECT]
    assert reader.hsv_calls == 1
    assert reader.fallback_calls == 1
    assert detector.hp_calls == 1


@pytest.mark.skipif(
    not REAL_GOLD_RESUME_SCREEN.is_file(),
    reason="local ignored real-device replay screenshot is unavailable",
)
def test_real_gold_resume_summary_recovers_name_hp_and_cp458() -> None:
    detector = HuaweiMate30PageDetector(RecognitionService())
    screenshot = REAL_GOLD_RESUME_SCREEN.read_bytes()

    result = detector.detect(screenshot, expected=("detail_summary",))

    assert result.state == "detail_summary"
    assert result.matched_texts == ("CP458", "索財靈")
    assert result.details["summary_ocr_path"] == "hsv_cp_fallback"
    assert detector.read_summary_hp(screenshot) == "73/73HP"


def test_summary_skips_cp_and_hp_fallback_when_name_is_missing() -> None:
    reader = ProgressiveSummaryReader(fast_cp=(), name=())
    detector = ProgressiveSummaryDetector(reader)

    result = detector.detect(PNG, expected=("detail_summary",))

    assert result.state == "unknown"
    assert reader.variant_calls == [CP_RECT, NAME_RECT]
    assert reader.fallback_calls == 0
    assert detector.hp_calls == 0


def test_returned_from_appraisal_requires_absent_iv_bars_and_two_detail_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector, observed_centers = returned_detector(
        monkeypatch,
        appraisal_bars_present=False,
        button_metrics=(
            {
                "disk_teal_ratio": 0.96,
                "ring_teal_ratio": 0.09,
                "ring_contrast": 0.87,
            },
            {
                "disk_teal_ratio": 0.95,
                "ring_teal_ratio": 0.39,
                "ring_contrast": 0.56,
            },
        ),
    )

    result = detector.detect_returned_from_appraisal(PNG)

    assert result.state == "detail_returned"
    assert result.details["iv_appraisal_geometry_absent"] is True
    assert result.details["appraisal_overlay_absent"] is True
    assert observed_centers == [Point(720, 2772), Point(1244, 2772)]


def test_returned_from_appraisal_rejects_visible_iv_bars_before_button_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector, observed_centers = returned_detector(
        monkeypatch,
        appraisal_bars_present=True,
    )

    result = detector.detect_returned_from_appraisal(PNG)

    assert result.state == "unknown"
    assert result.details == {
        "returned_from_appraisal": False,
        "appraisal_overlay_absent": False,
        "iv_appraisal_geometry_absent": False,
        "reason": "iv_bars_present",
    }
    assert observed_centers == []


@pytest.mark.parametrize(
    "button_metrics",
    (
        (
            {
                "disk_teal_ratio": 0.0,
                "ring_teal_ratio": 0.0,
                "ring_contrast": 0.0,
            },
            {
                "disk_teal_ratio": 0.0,
                "ring_teal_ratio": 0.0,
                "ring_contrast": 0.0,
            },
        ),
        (
            {
                "disk_teal_ratio": 1.0,
                "ring_teal_ratio": 1.0,
                "ring_contrast": 0.0,
            },
            {
                "disk_teal_ratio": 0.99,
                "ring_teal_ratio": 1.0,
                "ring_contrast": -0.01,
            },
        ),
        (
            {
                "disk_teal_ratio": 0.96,
                "ring_teal_ratio": 0.09,
                "ring_contrast": 0.87,
            },
            {
                "disk_teal_ratio": 0.30,
                "ring_teal_ratio": 0.05,
                "ring_contrast": 0.25,
            },
        ),
    ),
    ids=("black_screen", "full_teal_action_menu", "one_button_animation_frame"),
)
def test_returned_from_appraisal_fails_closed_without_both_button_geometries(
    monkeypatch: pytest.MonkeyPatch,
    button_metrics: tuple[dict[str, float], dict[str, float]],
) -> None:
    detector, _ = returned_detector(
        monkeypatch,
        appraisal_bars_present=False,
        button_metrics=button_metrics,
    )

    result = detector.detect_returned_from_appraisal(PNG)

    assert result.state == "unknown"
    assert result.details["returned_from_appraisal"] is False
    assert result.details["appraisal_overlay_absent"] is False
    assert result.details["reason"] == "detail_buttons_not_confirmed"


def test_rename_plan_uses_fixed_name_row_center() -> None:
    actions = planned_actions(rename_with_iv=True)
    rename_actions = actions[-2:]

    for action in rename_actions:
        coordinates = action["coordinates"]
        assert isinstance(coordinates, dict)
        assert coordinates["edit"] == {"x": 720, "y": 1460}


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
    app_config = AppConfig(data_dir=tmp_path)
    assert not hasattr(app_config, "appraisal_exit")
    service = AutoScanService(
        app_config,
        adb,
        detector,
        reader,
        clock=Clock(),
        token_factory=lambda: "live",
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    result = service.scan_one(debug=True)

    assert result.manifest.scan_status == "complete"
    assert detector.returned_calls == 1
    assert not hasattr(service, "_stable_waiter")
    assert result.recognition is not None
    assert reader.calls == [(result.scan_directory, True)]
    assert reader.evidence_calls == []
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


@pytest.mark.parametrize(
    ("accept_evidence", "expected_read_scan_calls"),
    ((True, 0), (False, 1)),
)
def test_live_complete_evidence_skips_or_falls_back_to_read_scan(
    tmp_path: Path,
    accept_evidence: bool,
    expected_read_scan_calls: int,
) -> None:
    menu = PageDetection(
        "action_menu",
        0.99,
        matched_texts=("調查寶可夢",),
        appraisal_target=Point(700, 2400),
    )
    detector = MovesAwareQueueDetector(
        [
            _summary_detection_with_recognition(),
            _moves_detection_with_recognition(),
            PageDetection("detail_moves", 0.99),
            menu,
            PageDetection("appraisal_dialogue", 0.95),
            PageDetection("appraisal_dialogue", 0.95),
            PageDetection("appraisal_bars", 1.0),
            _appraisal_detection_with_recognition(),
            _summary_detection(),
        ]
    )
    reader = FakeReader()
    reader.accept_evidence = accept_evidence
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        FakeAutomationAdb(),
        detector,
        reader,
        clock=Clock(),
        token_factory=lambda: "live-evidence",
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    result = service.scan_one(debug=True)

    assert result.manifest.scan_status == "complete"
    assert len(reader.calls) == expected_read_scan_calls
    assert len(reader.evidence_calls) == 1
    assert reader.evidence_calls[0][0] == result.scan_directory
    assert reader.evidence_calls[0][2] is True


def test_exit_appraisal_timeout_runs_one_full_diagnostic_without_more_input(
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
            menu,
            PageDetection("appraisal_bars", 1.0),
            PageDetection("appraisal_bars", 1.0),
            PageDetection("action_menu", 0.99),
        ],
        returned_detections=tuple(
            PageDetection("unknown", 0.0) for _ in range(20)
        ),
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(),
        automation=HuaweiMate30AutomationConfig(total_timeout_seconds=1000.0),
        clock=Clock(),
        token_factory=lambda: "exit-timeout",
        monotonic=AdvancingMonotonic(),
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(
        AutomationError,
        match="final full diagnostic state was action_menu",
    ):
        service.scan_one(debug=True)

    assert detector.returned_calls > 1
    assert adb.inputs[-1] == ("tap", 720, 1560)
    assert adb.inputs.count(("tap", 720, 1560)) == 1
    scan_directory = next((tmp_path / "scans" / "2026-07-29").iterdir())
    assert (scan_directory / "exit_appraisal_timeout.png").is_file()
    debug_directory = scan_directory / "debug" / "automation"
    assert (debug_directory / "exit_appraisal_timeout.png").is_file()
    diagnostic_states = tuple(
        debug_directory.glob("state_*_exit_appraisal_timeout_diagnostic.json")
    )
    assert len(diagnostic_states) == 1
    diagnostic = json.loads(diagnostic_states[0].read_text(encoding="utf-8"))
    assert diagnostic["state"] == "action_menu"


def test_rename_with_iv_resets_default_name_then_appends_suffix(tmp_path: Path) -> None:
    rename_dialog = PageDetection(
        "rename_dialog",
        0.99,
        matched_texts=("取消", "確定"),
        rename_confirm_target=Point(1120, 1860),
    )
    rename_keyboard = PageDetection(
        "rename_keyboard",
        0.99,
        matched_texts=("設定暱稱", "确定", "GIF", "1", "2", "3", "4"),
        rename_keyboard_target=Point(1248, 1712),
        details={"nickname_text_candidates": ["妙蛙花15/13/11"]},
    )
    detector = QueueDetector(
        [
            _summary_detection(),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            PageDetection(
                "action_menu",
                0.99,
                matched_texts=("調查寶可夢",),
                appraisal_target=Point(700, 2400),
            ),
            PageDetection("appraisal_bars", 1.0),
            PageDetection("appraisal_bars", 1.0),
            PageDetection(
                "detail_summary",
                0.99,
                matched_texts=("CP1761", "妙"),
                details={"summary_evidence": "name_cp"},
            ),
            PageDetection(
                "detail_summary",
                0.99,
                matched_texts=("CP1761", "妙蛙花"),
                details={"cp": "CP1761", "summary_evidence": "name_cp"},
            ),
            rename_dialog,
            rename_keyboard,
            rename_dialog,
            PageDetection(
                "detail_summary",
                0.99,
                matched_texts=("CP1761", "妙蛙花"),
                details={"summary_evidence": "name_cp"},
            ),
            _summary_detection(),
            rename_dialog,
            rename_keyboard,
            rename_dialog,
            _summary_detection(),
        ],
        summary_nicknames=("妙蛙花", "妙蛙花15/13/11"),
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(),
        automation=HuaweiMate30AutomationConfig(nickname_maximum_characters=2),
        clock=Clock(),
        token_factory=lambda: "rename",
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    result = service.scan_one(rename_with_iv=True, debug=True)

    assert result.manifest.scan_status == "complete"
    assert result.nickname_change is not None
    assert result.nickname_change.expected_nickname == "妙蛙花15/13/11"
    assert detector.summary_cp_calls == 0
    assert (result.scan_directory / "renamed_summary.png").is_file()
    assert (result.scan_directory / "nickname_change.json").is_file()
    assert adb.inputs[-11:] == [
        ("tap", 720, 1460),
        ("keyevent", 123),
        ("keyevent", 67),
        ("keyevent", 67),
        ("tap", 1248, 1712),
        ("tap", 1120, 1860),
        ("tap", 720, 1460),
        ("keyevent", 123),
        ("text", "15/13/11"),
        ("tap", 1248, 1712),
        ("tap", 1120, 1860),
    ]
    actions = json.loads(
        (result.scan_directory / "debug" / "automation" / "actions.json").read_text(
            encoding="utf-8"
        )
    )["actions"]
    assert [action["name"] for action in actions[-8:]] == [
        "open_nickname_editor_reset",
        "clear_nickname_for_default",
        "confirm_default_nickname_hide_keyboard",
        "confirm_default_nickname",
        "open_nickname_editor_append_iv",
        "append_iv_suffix",
        "confirm_iv_nickname_hide_keyboard",
        "confirm_iv_nickname",
    ]
    append_action = next(
        action for action in actions if action["name"] == "append_iv_suffix"
    )
    assert append_action["kind"] == "text"
    assert append_action["coordinates"] == {
        "end": 123,
        "ascii_text": "15/13/11",
    }
    default_name_state = json.loads(
        next(
            (result.scan_directory / "debug" / "automation").glob(
                "state_*_verified_default_nickname_wide.json"
            )
        ).read_text(encoding="utf-8")
    )
    assert default_name_state["matched_texts"] == ["妙蛙花"]
    assert default_name_state["details"] == {
        "nickname_evidence": "wide_summary_row",
        "source_state": "detail_summary",
    }
    timings = json.loads(
        (result.scan_directory / "debug" / "automation" / "timings.json").read_text(
            encoding="utf-8"
        )
    )
    assert timings["run_outcome"] == "complete"
    assert timings["total_elapsed_seconds"] >= 0
    assert timings["stable_wait_seconds"] == 0
    assert timings["ocr_seconds"] == 1.25
    timing_steps = timings["steps"]
    assert [step["sequence"] for step in timing_steps] == list(
        range(1, len(timing_steps) + 1)
    )
    assert all(step["duration_seconds"] >= 0 for step in timing_steps)
    assert all(step["stable_wait_seconds"] == 0 for step in timing_steps)
    assert sum(step["ocr_seconds"] for step in timing_steps) == 1.25
    assert {
        "verify_detail_summary",
        "scroll_to_moves",
        "wait_for_appraisal_entry",
        "recognize_scan",
        "rename_open_editor_reset",
        "rename_clear_to_default",
        "rename_append_iv_suffix",
        "rename_verify_final_summary",
    }.issubset({step["step"] for step in timing_steps})
    assert all(step["outcome"] == "completed" for step in timing_steps)


def _cp_disagreement_rename_detections(
    checkpoint_cp: int,
    expected_nickname: str,
) -> list[PageDetection]:
    rename_dialog = PageDetection(
        "rename_dialog",
        0.99,
        matched_texts=("取消", "確定"),
        rename_confirm_target=Point(1120, 1860),
    )
    rename_keyboard = PageDetection(
        "rename_keyboard",
        0.99,
        matched_texts=("設定暱稱", expected_nickname, "确定"),
        rename_keyboard_target=Point(1248, 1712),
        details={"nickname_text_candidates": [expected_nickname]},
    )
    return [
        _summary_detection(),
        PageDetection("detail_moves", 0.99),
        PageDetection("detail_moves", 0.99),
        PageDetection(
            "action_menu",
            0.99,
            matched_texts=("調查寶可夢",),
            appraisal_target=Point(700, 2400),
        ),
        PageDetection("appraisal_bars", 1.0),
        PageDetection("appraisal_bars", 1.0),
        _summary_detection(),
        PageDetection(
            "detail_summary",
            0.99,
            matched_texts=(f"CP{checkpoint_cp}", "湧躍鴨"),
            details={"cp": f"CP{checkpoint_cp}", "summary_evidence": "name_cp"},
        ),
        rename_dialog,
        rename_keyboard,
        rename_dialog,
        _summary_detection(),
        _summary_detection(),
        rename_dialog,
        rename_keyboard,
        rename_dialog,
        _summary_detection(),
    ]


def test_rename_cp_disagreement_uses_bounded_cp_only_consensus(
    tmp_path: Path,
) -> None:
    expected_nickname = "湧躍鴨12/15/15"
    detector = QueueDetector(
        _cp_disagreement_rename_detections(97, expected_nickname),
        summary_nicknames=("湧躍鴨", expected_nickname),
        summary_cps=(997, None, 997),
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(
            pokemon_name="湧躍鴨",
            cp=67,
            attack_iv=12,
            defense_iv=15,
            hp_iv=15,
        ),
        automation=HuaweiMate30AutomationConfig(
            nickname_maximum_characters=1,
            rename_cp_consensus_interval_seconds=0.0,
        ),
        clock=Clock(),
        token_factory=lambda: "rename-cp-consensus",
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    result = service.scan_one(rename_with_iv=True, debug=True)

    assert result.recognition is not None
    assert result.recognition.cp.value == 997
    assert result.recognition.cp.raw is None
    assert detector.summary_cp_calls == 3
    assert detector.summary_nickname_calls == 2
    assert detector.summary_hp_calls == 0
    persisted = json.loads(
        (result.scan_directory / "recognition.json").read_text(encoding="utf-8")
    )
    assert persisted["cp"]["value"] == 997
    consensus = json.loads(
        (
            result.scan_directory
            / "debug"
            / "automation"
            / "rename_cp_consensus.json"
        ).read_text(encoding="utf-8")
    )
    assert consensus == {
        "baseline_cp": 67,
        "checkpoint_cp": 97,
        "samples": [997, None, 997],
        "required_matches": 2,
        "trusted_cp": 997,
    }


def test_rename_cp_disagreement_without_consensus_stops_before_editor(
    tmp_path: Path,
) -> None:
    expected_nickname = "湧躍鴨12/15/15"
    detector = QueueDetector(
        _cp_disagreement_rename_detections(97, expected_nickname),
        summary_nicknames=("湧躍鴨", expected_nickname),
        summary_cps=(997, None, 99),
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(
            pokemon_name="湧躍鴨",
            cp=67,
            attack_iv=12,
            defense_iv=15,
            hp_iv=15,
        ),
        automation=HuaweiMate30AutomationConfig(
            rename_cp_consensus_interval_seconds=0.0,
            rename_cp_consensus_max_frames=3,
        ),
        clock=Clock(),
        token_factory=lambda: "rename-cp-no-consensus",
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    with pytest.raises(AutomationError, match="did not produce a repeated complete CP"):
        service.scan_one(rename_with_iv=True, debug=True)

    assert detector.summary_cp_calls == 3
    assert detector.summary_nickname_calls == 0
    assert detector.summary_hp_calls == 0
    assert ("tap", 720, 1460) not in adb.inputs


def test_editor_nickname_compaction_preserves_character_width() -> None:
    assert compact_editor_nickname_text("妙蛙花 15/13/11") == "妙蛙花15/13/11"
    full_width = "妙蛙花\uff11\uff15\uff0f\uff11\uff13\uff0f\uff11\uff11"
    assert compact_editor_nickname_text(full_width) != "妙蛙花15/13/11"


def test_build_iv_nickname_keeps_pretty_format_within_limit() -> None:
    result = build_iv_nickname("毛崖蟹", 11, 11, 15)

    assert result.nickname == "毛崖蟹11/11/15"
    assert result.format == "pretty"
    assert len(result.nickname) <= 12


def test_build_iv_nickname_compacts_five_character_name() -> None:
    result = build_iv_nickname("赫拉克羅斯", 15, 14, 13)

    assert result.nickname == "赫拉克羅斯151413"
    assert result.format == "compact_iv"


def test_compact_iv_suffix_zero_pads_each_value() -> None:
    assert compact_iv_suffix(1, 11, 1) == "011101"
    assert compact_iv_suffix(0, 0, 0) == "000000"
    assert compact_iv_suffix(0, 0, 15) == "000015"


@pytest.mark.parametrize("name", ["卡璞・鳴鳴", "屬性：空", "3D龍2"])
def test_build_iv_nickname_uses_python_unicode_length(name: str) -> None:
    result = build_iv_nickname(name, 15, 14, 13)

    expected_pretty = f"{name}15/14/13"
    expected_compact = f"{name}151413"
    assert result.nickname == (
        expected_pretty if len(expected_pretty) <= 12 else expected_compact
    )


def test_build_iv_nickname_rejects_compact_value_over_limit_without_truncation() -> None:
    with pytest.raises(AutomationError, match="exceeds the 12-character game limit"):
        build_iv_nickname("人工超長寶可夢名", 15, 14, 13)


def test_fixed_nickname_row_tap_stops_before_text_when_editor_does_not_open(
    tmp_path: Path,
) -> None:
    detector = QueueDetector(
        [
            _summary_detection(),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            PageDetection(
                "action_menu",
                0.99,
                matched_texts=("調查寶可夢",),
                appraisal_target=Point(700, 2400),
            ),
            PageDetection("appraisal_bars", 1.0),
            PageDetection("appraisal_bars", 1.0),
            _summary_detection(),
            _summary_detection(),
            _summary_detection(),
            _summary_detection(),
            _summary_detection(),
        ]
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(),
        automation=HuaweiMate30AutomationConfig(rename_wait_timeout_seconds=0.5),
        clock=Clock(),
        token_factory=lambda: "rename-fixed-center-no-editor",
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    with pytest.raises(AutomationError, match="did not expose an OCR-confirmed"):
        service.scan_one(rename_with_iv=True, debug=True)

    assert adb.inputs[-1] == ("tap", 720, 1460)
    assert not any(action[0] in ("keyevent", "text") for action in adb.inputs)
    scan_directory = next((tmp_path / "scans" / "2026-07-29").iterdir())
    timings = json.loads(
        (scan_directory / "debug" / "automation" / "timings.json").read_text(
            encoding="utf-8"
        )
    )
    assert timings["run_outcome"] == "failed"
    assert timings["steps"][-1]["step"] == "rename_open_editor_reset"
    assert timings["steps"][-1]["outcome"] == "failed"


def test_rename_with_iv_rejects_wrong_editor_text_before_final_confirmation(
    tmp_path: Path,
) -> None:
    rename_dialog = PageDetection(
        "rename_dialog",
        0.99,
        matched_texts=("取消", "確定"),
        rename_confirm_target=Point(1120, 1860),
    )
    wrong_keyboard = PageDetection(
        "rename_keyboard",
        0.99,
        matched_texts=("設定暱稱", "赫拉克羅斯15141", "确定"),
        rename_keyboard_target=Point(1248, 1712),
        details={"nickname_text_candidates": ["赫拉克羅斯15141"]},
    )
    detector = QueueDetector(
        [
            _summary_detection(),
            PageDetection("detail_moves", 0.99),
            PageDetection("detail_moves", 0.99),
            PageDetection(
                "action_menu",
                0.99,
                matched_texts=("調查寶可夢",),
                appraisal_target=Point(700, 2400),
            ),
            PageDetection("appraisal_bars", 1.0),
            PageDetection("appraisal_bars", 1.0),
            _summary_detection(),
            _summary_detection(),
            rename_dialog,
            wrong_keyboard,
            rename_dialog,
            PageDetection(
                "detail_summary",
                0.99,
                matched_texts=("CP1761", "妙蛙花"),
                details={"summary_evidence": "name_cp"},
            ),
            _summary_detection(),
            rename_dialog,
            wrong_keyboard,
        ],
        summary_nicknames=("赫拉克羅斯",),
    )
    adb = FakeAutomationAdb()
    service = AutoScanService(
        AppConfig(data_dir=tmp_path),
        adb,
        detector,
        FakeReader(
            pokemon_name="赫拉克羅斯",
            attack_iv=15,
            defense_iv=14,
            hp_iv=13,
        ),
        automation=HuaweiMate30AutomationConfig(nickname_maximum_characters=1),
        clock=Clock(),
        token_factory=lambda: "rename-mismatch",
        monotonic=lambda: 0.0,
        sleeper=lambda _: None,
    )

    with pytest.raises(AutomationError, match="did not exactly match"):
        service.scan_one(rename_with_iv=True, debug=True)

    assert adb.inputs.count(("tap", 1248, 1712)) == 1
    assert adb.inputs.count(("tap", 1120, 1860)) == 1
    assert ("text", "151413") in adb.inputs


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
