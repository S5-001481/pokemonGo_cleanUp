"""Focused tests for bounded batch persistence and switching."""

from __future__ import annotations

import csv
import io
import json
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pokemon_go_cleanup.automation import (
    AutoScanResult,
    NicknameRenameResult,
    PageDetection,
)
from pokemon_go_cleanup.batch import (
    BATCH_CSV_COLUMNS,
    LEGACY_BATCH_CSV_COLUMNS,
    BatchScanService,
    ExpectedNicknameTransition,
    HuaweiMate30BatchConfig,
    SummaryIdentity,
    _same_switch_identity,
    fingerprint_distance,
    matches_expected_nickname_transition,
    matches_verified_renamed_identity,
    page_fingerprint,
)
from pokemon_go_cleanup.exceptions import BatchAutomationError
from pokemon_go_cleanup.models import Device, ScanManifest, ScreenResolution
from pokemon_go_cleanup.recognition import (
    RecognitionResult,
    RecognizedInteger,
    RecognizedText,
)
from pokemon_go_cleanup.storage import atomic_write_bytes, atomic_write_text


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1440, 3120), color).save(output, format="PNG")
    return output.getvalue()


PNG_A = _png((240, 240, 240))
PNG_B = _png((120, 120, 120))


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeBatchAdb:
    def __init__(self, captures: list[bytes]) -> None:
        self.captures = deque(captures)
        self.swipes: list[tuple[int, int, int, int, int]] = []

    def resolve_device(self, serial_number: str | None = None) -> Device:
        return Device(serial_number="ABC", state="device")

    def capture_screen(self, serial_number: str) -> bytes:
        return self.captures.popleft()

    def swipe(
        self,
        serial_number: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int,
    ) -> None:
        self.swipes.append((x1, y1, x2, y2, duration_ms))


class QueueDetector:
    def __init__(
        self,
        detections: list[PageDetection],
        *,
        summary_nickname: str = "",
        summary_nicknames: list[str] | None = None,
        summary_cps: list[int | None] | None = None,
        summary_hps: list[str | None] | None = None,
    ) -> None:
        self.detections = deque(detections)
        self.summary_nickname = summary_nickname
        self.summary_nicknames = deque(summary_nicknames or [])
        self.summary_cps = deque(summary_cps or [])
        self.summary_hps = deque(summary_hps or [])
        self.cp_calls = 0
        self.hp_calls = 0

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: tuple[str, ...],
    ) -> PageDetection:
        assert expected
        return self.detections.popleft()

    def read_summary_nickname(self, png_bytes: bytes) -> str:
        if self.summary_nicknames:
            return self.summary_nicknames.popleft()
        return self.summary_nickname

    def read_summary_cp(self, png_bytes: bytes) -> int | None:
        self.cp_calls += 1
        return self.summary_cps.popleft() if self.summary_cps else None

    def read_summary_hp(self, png_bytes: bytes) -> str | None:
        self.hp_calls += 1
        return self.summary_hps.popleft() if self.summary_hps else None


class FakeScanner:
    def __init__(self, root: Path, results: list[RecognitionResult]) -> None:
        self.root = root
        self.results = deque(results)
        self.calls: list[tuple[bool, str | None, bool]] = []

    def scan_one(
        self,
        *,
        debug: bool = False,
        dry_run: bool = False,
        rename_with_iv: bool = False,
        serial_number: str | None = None,
        notes: str | None = None,
    ) -> AutoScanResult:
        assert not dry_run
        assert notes is None
        recognition = self.results.popleft()
        directory = self.root / recognition.scan_id
        directory.mkdir(parents=True)
        atomic_write_bytes(
            directory / "summary.png",
            PNG_A if recognition.cp.value == 100 else PNG_B,
        )
        manifest = ScanManifest(
            scan_id=recognition.scan_id,
            started_at=datetime.now(UTC),
            device_serial="ABC",
            screen_resolution=ScreenResolution(width=1440, height=3120),
            workflow_mode="automatic",
            application_version="0.1.0",
            scan_status="complete",
        )
        atomic_write_text(
            directory / "manifest.json",
            manifest.model_dump_json(indent=2) + "\n",
        )
        nickname_change: NicknameRenameResult | None = None
        if rename_with_iv:
            values = (recognition.attack_iv, recognition.defense_iv, recognition.hp_iv)
            assert all(value is not None for value in values)
            suffix = "/".join(str(value) for value in values)
            default_name = recognition.pokemon_name.value or ""
            expected = f"{default_name}{suffix}"
            renamed_png = PNG_A if recognition.cp.value == 100 else PNG_B
            renamed_detection = PageDetection(
                "detail_summary",
                0.99,
                (f"CP{recognition.cp.value}", expected),
            )
            nickname_change = NicknameRenameResult(
                nickname_before=default_name,
                default_nickname=default_name,
                expected_nickname=expected,
                editor_observed_nickname=expected,
                summary_observed_nickname=expected,
                summary_png=renamed_png,
                summary_detection=renamed_detection,
            )
            atomic_write_bytes(directory / "renamed_summary.png", renamed_png)
            atomic_write_text(
                directory / "nickname_change.json",
                json.dumps(
                    {
                        "nickname_before": default_name,
                        "default_nickname": default_name,
                        "nickname_after": expected,
                        "editor_observed_nickname": expected,
                        "summary_observed_nickname": expected,
                        "summary_filename": "renamed_summary.png",
                        "status": "verified",
                    },
                    ensure_ascii=False,
                ),
            )
        self.calls.append((debug, serial_number, rename_with_iv))
        return AutoScanResult(
            scan_directory=directory,
            manifest_path=directory / "manifest.json",
            manifest=manifest,
            recognition=recognition,
            dry_run=False,
            planned_actions=(),
            nickname_change=nickname_change,
        )


class BadTransitionScanner(FakeScanner):
    def scan_one(
        self,
        *,
        debug: bool = False,
        dry_run: bool = False,
        rename_with_iv: bool = False,
        serial_number: str | None = None,
        notes: str | None = None,
    ) -> AutoScanResult:
        result = super().scan_one(
            debug=debug,
            dry_run=dry_run,
            rename_with_iv=rename_with_iv,
            serial_number=serial_number,
            notes=notes,
        )
        assert result.nickname_change is not None
        bad_detection = replace(
            result.nickname_change.summary_detection,
            matched_texts=("CP999", result.nickname_change.expected_nickname),
        )
        return replace(
            result,
            nickname_change=replace(
                result.nickname_change,
                summary_detection=bad_detection,
            ),
        )


def _recognition(
    scan_id: str,
    name: str,
    cp: int,
    *,
    warnings: tuple[str, ...] = (),
) -> RecognitionResult:
    return RecognitionResult(
        scan_id=scan_id,
        pokemon_name=RecognizedText(value=name, raw=name, confidence=0.99),
        cp=RecognizedInteger(value=cp, raw=f"CP{cp}", confidence=0.99),
        fast_move=RecognizedText(value="招式甲", raw="招式甲", confidence=0.99),
        charged_move_1=RecognizedText(value="招式乙", raw="招式乙", confidence=0.99),
        charged_move_2=RecognizedText(value=None, raw=None, confidence=None),
        attack_iv=15,
        defense_iv=14,
        hp_iv=13,
        warnings=warnings,
    )


def test_batch_scans_two_distinct_pokemon_and_persists_each_row(
    tmp_path: Path,
) -> None:
    clock = FakeTime()
    scanner = FakeScanner(
        tmp_path / "scans",
        [
            _recognition("scan-a", "甲", 100, warnings=("类型：暗影",)),
            _recognition("scan-b", "乙", 200, warnings=("类型：极巨化",)),
        ],
    )
    adb = FakeBatchAdb([PNG_A, PNG_B])
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
        ]
    )
    destination = tmp_path / "batch.csv"
    service = BatchScanService(
        adb,
        scanner,
        detector,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    result = service.scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=False,
        delay_seconds=2,
    )

    assert result.stop_reason == "limit_reached"
    assert len(result.new_rows) == 2
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]
    assert all(x1 > x2 for x1, _, x2, _, _ in adb.swipes)
    assert scanner.calls == [(True, "ABC", False), (True, "ABC", False)]
    with destination.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [row["pokemon_name"] for row in rows] == ["甲", "乙"]
    assert [row["cp"] for row in rows] == ["100", "200"]
    assert [row["warnings"] for row in rows] == ["类型：暗影", "类型：极巨化"]
    assert not list(tmp_path.rglob("*.tmp"))


def test_batch_rename_verifies_transition_and_switches_from_post_identity(
    tmp_path: Path,
) -> None:
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-a", "甲", 100), _recognition("scan-b", "乙", 200)],
    )
    adb = FakeBatchAdb([PNG_A, PNG_B])
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲15/14/13")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
        ]
    )
    destination = tmp_path / "renamed.csv"

    result = BatchScanService(adb, scanner, detector).scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=False,
        delay_seconds=0,
        rename_with_iv=True,
    )

    assert result.stop_reason == "limit_reached"
    assert scanner.calls == [(True, "ABC", True), (True, "ABC", True)]
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]
    with destination.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [row["nickname_after"] for row in rows] == [
        "甲15/14/13",
        "乙15/14/13",
    ]
    assert [row["rename_status"] for row in rows] == ["verified", "verified"]


def test_verified_rename_switch_recovers_cp_before_using_strict_identity(
    tmp_path: Path,
) -> None:
    clock = FakeTime()
    expected = "索財靈10/15/15"
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                (expected,),
                details={"hp": "73/73HP", "summary_evidence": "name_hp"},
            ),
            PageDetection("detail_summary", 0.99, ("CP200", "下一隻")),
        ],
        summary_nickname=expected,
        summary_cps=[458],
    )
    adb = FakeBatchAdb([PNG_A, PNG_A, PNG_B])
    scan_directory = tmp_path / "scan"
    scan_directory.mkdir()
    service = BatchScanService(
        adb,
        FakeScanner(tmp_path / "scans", []),
        detector,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    result = service._switch_to_next(
        "ABC",
        SummaryIdentity(expected, 458, page_fingerprint(PNG_A)),
        scan_directory,
        debug=True,
        expected_nickname=expected,
    )

    assert result.outcome == "changed"
    assert result.identity is not None
    assert result.identity.cp == 200
    assert detector.cp_calls == 1
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]
    retry_state = json.loads(
        (
            scan_directory
            / "debug"
            / "batch"
            / "before_switch_cp_retry_state.json"
        ).read_text(encoding="utf-8")
    )
    assert retry_state["attempts"] == [
        {
            "attempt": 1,
            "cp": 458,
            "previous_frame_fingerprint_distance": 0,
            "baseline_fingerprint_distance": 0,
            "page_changed": False,
        }
    ]


def test_verified_rename_switch_uses_one_strong_no_cp_fallback_swipe(
    tmp_path: Path,
) -> None:
    clock = FakeTime()
    expected = "索財靈10/15/15"
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                (expected,),
                details={"hp": "73/73HP", "summary_evidence": "name_hp"},
            ),
            PageDetection("detail_summary", 0.99, ("CP200", "下一隻")),
        ],
        summary_nickname=expected,
        summary_cps=[None, None],
        summary_hps=["73/73HP", "73/73HP"],
    )
    adb = FakeBatchAdb([PNG_A, PNG_A, PNG_A, PNG_B])
    scan_directory = tmp_path / "scan"
    scan_directory.mkdir()
    atomic_write_bytes(scan_directory / "renamed_summary.png", PNG_A)
    service = BatchScanService(
        adb,
        FakeScanner(tmp_path / "scans", []),
        detector,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    result = service._switch_to_next(
        "ABC",
        SummaryIdentity(expected, 458, page_fingerprint(PNG_A)),
        scan_directory,
        debug=True,
        expected_nickname=expected,
    )

    assert result.outcome == "changed"
    assert detector.cp_calls == 2
    assert detector.hp_calls == 2
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]
    fallback_state = json.loads(
        (
            scan_directory
            / "debug"
            / "batch"
            / "before_switch_verified_rename_fallback_state.json"
        ).read_text(encoding="utf-8")
    )
    assert fallback_state["nickname_matches"] is True
    assert fallback_state["hp_matches"] is True
    assert fallback_state["fingerprint_distance"] == 0
    assert fallback_state["matched"] is True


@pytest.mark.parametrize(
    ("observed_nickname", "current_hp", "baseline_hp"),
    [
        ("錯誤暱稱", "73/73HP", "73/73HP"),
        ("索財靈10/15/15", "72/73HP", "73/73HP"),
        ("索財靈10/15/15", "73/73HP", None),
    ],
)
def test_verified_rename_switch_rejects_weak_no_cp_fallback_without_swipe(
    tmp_path: Path,
    observed_nickname: str,
    current_hp: str,
    baseline_hp: str | None,
) -> None:
    clock = FakeTime()
    expected = "索財靈10/15/15"
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                (expected,),
                details={"hp": current_hp, "summary_evidence": "name_hp"},
            )
        ],
        summary_nickname=observed_nickname,
        summary_cps=[None, None],
        summary_hps=[baseline_hp, current_hp],
    )
    adb = FakeBatchAdb([PNG_A, PNG_A, PNG_A])
    scan_directory = tmp_path / "scan"
    scan_directory.mkdir()
    atomic_write_bytes(scan_directory / "renamed_summary.png", PNG_A)
    service = BatchScanService(
        adb,
        FakeScanner(tmp_path / "scans", []),
        detector,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    with pytest.raises(BatchAutomationError, match="exact nickname, HP"):
        service._switch_to_next(
            "ABC",
            SummaryIdentity(expected, 458, page_fingerprint(PNG_A)),
            scan_directory,
            debug=True,
            expected_nickname=expected,
        )

    assert adb.swipes == []


def test_verified_rename_switch_cp_retry_rejects_static_page_change(
    tmp_path: Path,
) -> None:
    changed = Image.new("RGB", (1440, 3120), (240, 240, 240))
    ImageDraw.Draw(changed).rectangle((720, 1550, 1340, 2300), fill=(10, 10, 10))
    output = io.BytesIO()
    changed.save(output, format="PNG")
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                ("索財靈10/15/15",),
                details={"hp": "73/73HP", "summary_evidence": "name_hp"},
            )
        ]
    )
    adb = FakeBatchAdb([PNG_A, output.getvalue()])
    scan_directory = tmp_path / "scan"
    scan_directory.mkdir()

    with pytest.raises(BatchAutomationError, match="page changed during"):
        BatchScanService(
            adb,
            FakeScanner(tmp_path / "scans", []),
            detector,
            sleeper=lambda _: None,
        )._switch_to_next(
            "ABC",
            SummaryIdentity(
                "索財靈10/15/15",
                458,
                page_fingerprint(PNG_A),
            ),
            scan_directory,
            debug=True,
            expected_nickname="索財靈10/15/15",
        )

    assert detector.cp_calls == 0
    assert adb.swipes == []


def test_non_rename_switch_does_not_use_cp_missing_fallback(tmp_path: Path) -> None:
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                ("索財靈",),
                details={"hp": "73/73HP", "summary_evidence": "name_hp"},
            )
        ]
    )
    adb = FakeBatchAdb([PNG_A])
    scan_directory = tmp_path / "scan"
    scan_directory.mkdir()

    with pytest.raises(BatchAutomationError, match="reliable Pokémon name and CP"):
        BatchScanService(adb, FakeScanner(tmp_path / "scans", []), detector)._switch_to_next(
            "ABC",
            SummaryIdentity("索財靈", 458, page_fingerprint(PNG_A)),
            scan_directory,
            debug=True,
        )

    assert detector.cp_calls == 0
    assert adb.swipes == []


def test_renamed_batch_wraps_on_exact_wide_nickname_and_immutable_identity(
    tmp_path: Path,
) -> None:
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-a", "甲", 100), _recognition("scan-b", "乙", 200)],
    )
    adb = FakeBatchAdb([PNG_A, PNG_B, PNG_B, PNG_A])
    expected_first = "甲15/14/13"
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP100", expected_first)),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙15/14/13")),
            PageDetection("detail_summary", 0.99, ("CP100", "不同OCR碎片")),
        ],
        summary_nicknames=["乙", expected_first],
    )

    result = BatchScanService(adb, scanner, detector).scan(
        limit=3,
        csv_path=tmp_path / "renamed-wrap.csv",
        debug=True,
        resume=False,
        delay_seconds=0,
        rename_with_iv=True,
    )

    assert result.stop_reason == "wrapped_to_first"
    assert len(result.rows) == 2
    assert scanner.calls == [(True, "ABC", True), (True, "ABC", True)]
    assert adb.swipes == [
        (1180, 1500, 260, 1500, 600),
        (1180, 1500, 260, 1500, 600),
    ]
    wrap_state = json.loads(
        (
            tmp_path
            / "scans"
            / "scan-b"
            / "debug"
            / "batch"
            / "wrap_comparison_state.json"
        ).read_text(encoding="utf-8")
    )
    assert wrap_state["observed_nickname"] == expected_first
    assert wrap_state["wrapped_to_first"] is True


def test_failed_expected_rename_transition_appends_nothing_and_sends_no_swipe(
    tmp_path: Path,
) -> None:
    scanner = BadTransitionScanner(
        tmp_path / "scans",
        [_recognition("scan-a", "甲", 100)],
    )
    adb = FakeBatchAdb([])
    detector = QueueDetector(
        [PageDetection("detail_summary", 0.99, ("CP100", "甲"))]
    )
    destination = tmp_path / "rejected.csv"

    with pytest.raises(BatchAutomationError, match="Expected nickname transition failed"):
        BatchScanService(adb, scanner, detector).scan(
            limit=1,
            csv_path=destination,
            debug=True,
            resume=False,
            delay_seconds=0,
            rename_with_iv=True,
        )

    assert not destination.exists()
    assert adb.swipes == []


def test_switch_retries_once_when_name_and_cp_stay_the_same(tmp_path: Path) -> None:
    clock = FakeTime()
    config = HuaweiMate30BatchConfig(switch_timeout_seconds=1.0)
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-a", "甲", 100), _recognition("scan-b", "乙", 200)],
    )
    adb = FakeBatchAdb([PNG_A, PNG_A, PNG_A, PNG_A, PNG_B])
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
        ]
    )
    service = BatchScanService(
        adb,
        scanner,
        detector,
        config=config,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    )

    result = service.scan(
        limit=2,
        csv_path=tmp_path / "retry.csv",
        debug=True,
        resume=False,
        delay_seconds=0,
    )

    assert result.stop_reason == "limit_reached"
    assert adb.swipes == [
        (1180, 1500, 260, 1500, 600),
        (1300, 1500, 140, 1500, 850),
    ]
    assert all(x1 > x2 for x1, _, x2, _, _ in adb.swipes)
    debug_directory = tmp_path / "scans" / "scan-a" / "debug" / "batch"
    assert (debug_directory / "switch_attempt_1_wait.json").is_file()
    assert (debug_directory / "switch_attempt_2_wait.json").is_file()


class SummaryFirstDetector:
    def __init__(self) -> None:
        self.expected_calls: list[tuple[str, ...]] = []

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: tuple[str, ...],
    ) -> PageDetection:
        self.expected_calls.append(expected)
        if expected == ("detail_summary",):
            return PageDetection("detail_summary", 0.95, ("CP431", "睡睡菇"))
        return PageDetection("appraisal_bars", 1.0)

    def read_summary_nickname(self, png_bytes: bytes) -> str:
        return "睡睡菇"

    def read_summary_cp(self, png_bytes: bytes) -> int | None:
        return 431

    def read_summary_hp(self, png_bytes: bytes) -> str | None:
        return None


def test_switch_detection_prioritizes_summary_over_iv_false_positive(
    tmp_path: Path,
) -> None:
    detector = SummaryFirstDetector()
    service = BatchScanService(
        FakeBatchAdb([]),
        FakeScanner(tmp_path, []),
        detector,
    )

    detection = service._detect_switch_screen(
        PNG_B,
        abnormal=("detail_moves", "action_menu", "appraisal_dialogue", "appraisal_bars"),
    )

    assert detection.state == "detail_summary"
    assert detector.expected_calls == [("detail_summary",)]


def test_page_fingerprint_is_stable_and_256_bits() -> None:
    first = page_fingerprint(PNG_A)

    assert first == page_fingerprint(PNG_A)
    assert len(first) == 64


def test_switch_identity_accepts_static_fingerprint_change() -> None:
    previous = SummaryIdentity("same", 100, "0" * 64)
    unchanged = SummaryIdentity("same", 100, "0" * 64)
    changed = SummaryIdentity("same", 100, "f" * 64)
    config = HuaweiMate30BatchConfig()

    assert _same_switch_identity(unchanged, previous, config)
    assert not _same_switch_identity(changed, previous, config)
    assert not _same_switch_identity(
        SummaryIdentity("expected-new-name", 100, "0" * 64),
        previous,
        config,
    )


def test_expected_nickname_transition_is_separate_and_keeps_identity_strict() -> None:
    before = SummaryIdentity("original", 100, "0" * 64, "50/50HP")
    after = SummaryIdentity("14", 100, "0" * 64, "50/50HP")
    transition = ExpectedNicknameTransition(
        before=before,
        after=after,
        expected_nickname="測試15/14/13",
        editor_observed_nickname="測試15/14/13",
        summary_observed_nickname="測試151413",
    )

    assert matches_expected_nickname_transition(transition)
    assert not matches_expected_nickname_transition(
        replace(transition, editor_observed_nickname="測試15/14/12")
    )
    assert not matches_expected_nickname_transition(
        replace(
            transition,
            editor_observed_nickname="測試\uff11\uff15\uff0f\uff11\uff14\uff0f\uff11\uff13",
        )
    )
    assert not matches_expected_nickname_transition(
        replace(transition, summary_observed_nickname="測試15113")
    )
    assert not matches_expected_nickname_transition(
        replace(transition, after=replace(after, cp=101))
    )
    assert not matches_expected_nickname_transition(
        replace(transition, after=replace(after, hp_text="49/50HP"))
    )
    assert not matches_expected_nickname_transition(
        replace(transition, after=replace(after, page_fingerprint="f" * 64))
    )


def test_verified_renamed_identity_ignores_only_unstable_generic_name_ocr() -> None:
    baseline = SummaryIdentity("2", 175, "0" * 64, "68/68HP")
    current = SummaryIdentity("5", 175, "0" * 64, "68/68HP")

    assert not _same_switch_identity(current, baseline, HuaweiMate30BatchConfig())
    assert matches_verified_renamed_identity(
        current,
        baseline,
        expected_nickname="飄飄球12/2/5",
        observed_nickname="飄飄球1225",
    )
    assert not matches_verified_renamed_identity(
        current,
        baseline,
        expected_nickname="飄飄球12/2/5",
        observed_nickname="飄飄球125",
    )
    assert not matches_verified_renamed_identity(
        replace(current, cp=176),
        baseline,
        expected_nickname="飄飄球12/2/5",
        observed_nickname="飄飄球1225",
    )


def _seed_resume_csv(tmp_path: Path, destination: Path) -> None:
    service = BatchScanService(
        FakeBatchAdb([]),
        FakeScanner(
            tmp_path / "scans",
            [_recognition("scan-last", "睡睡菇", 431)],
        ),
        QueueDetector([PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇"))]),
    )
    service.scan(
        limit=1,
        csv_path=destination,
        debug=False,
        resume=False,
        delay_seconds=0,
    )


def _seed_renamed_resume_csv(tmp_path: Path, destination: Path) -> None:
    service = BatchScanService(
        FakeBatchAdb([]),
        FakeScanner(
            tmp_path / "renamed-scans",
            [_recognition("scan-renamed-last", "睡睡菇", 431)],
        ),
        QueueDetector([PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇"))]),
    )
    service.scan(
        limit=1,
        csv_path=destination,
        debug=False,
        resume=False,
        delay_seconds=0,
        rename_with_iv=True,
    )


def test_legacy_batch_header_remains_resumable_without_rename(tmp_path: Path) -> None:
    destination = tmp_path / "legacy-batch.csv"
    _seed_resume_csv(tmp_path, destination)
    with destination.open(encoding="utf-8", newline="") as source:
        current_row = next(csv.DictReader(source))
    with destination.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=LEGACY_BATCH_CSV_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {column: current_row[column] for column in LEGACY_BATCH_CSV_COLUMNS}
        )

    result = BatchScanService(
        FakeBatchAdb([]),
        FakeScanner(tmp_path / "scans", []),
        QueueDetector([]),
    ).scan(
        limit=1,
        csv_path=destination,
        debug=False,
        resume=True,
        delay_seconds=0,
    )

    assert result.stop_reason == "limit_reached"
    assert tuple(current_row) == BATCH_CSV_COLUMNS


def test_resume_rejects_changing_batch_rename_mode(tmp_path: Path) -> None:
    destination = tmp_path / "rename-mode.csv"
    _seed_renamed_resume_csv(tmp_path, destination)

    with pytest.raises(BatchAutomationError, match="same --rename-with-iv setting"):
        BatchScanService(
            FakeBatchAdb([]),
            FakeScanner(tmp_path / "renamed-scans", []),
            QueueDetector([]),
        ).scan(
            limit=1,
            csv_path=destination,
            debug=False,
            resume=True,
            delay_seconds=0,
            rename_with_iv=False,
        )


def test_resume_switches_before_scanning_when_current_is_last_row(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume.csv"
    _seed_resume_csv(tmp_path, destination)
    adb = FakeBatchAdb([PNG_B, PNG_B, PNG_A])
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-next", "下一隻", 100)],
    )
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ]
    )
    service = BatchScanService(adb, scanner, detector)

    result = service.scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=True,
        delay_seconds=0,
    )

    assert result.stop_reason == "limit_reached"
    assert scanner.calls == [(True, "ABC", False)]
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]
    assert all(x1 > x2 for x1, _, x2, _, _ in adb.swipes)
    with destination.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [(row["pokemon_name"], row["cp"]) for row in rows] == [
        ("睡睡菇", "431"),
        ("下一隻", "100"),
    ]
    debug_directory = tmp_path / "scans" / "scan-last" / "debug" / "batch"
    assert (debug_directory / "resume_current.png").is_file()
    assert (debug_directory / "resume_last_summary_path.txt").is_file()
    assert (debug_directory / "resume_current_static_roi.png").is_file()
    assert (debug_directory / "resume_last_static_roi.png").is_file()
    state = json.loads((debug_directory / "resume_state.json").read_text(encoding="utf-8"))
    assert state["same_as_last"] is True
    assert state["resume_pre_switch_executed"] is True


def test_resume_does_not_switch_when_current_is_already_next(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-next.csv"
    _seed_resume_csv(tmp_path, destination)
    adb = FakeBatchAdb([PNG_A])
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-next", "下一隻", 100)],
    )
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ]
    )
    service = BatchScanService(adb, scanner, detector)

    result = service.scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=True,
        delay_seconds=0,
    )

    assert result.stop_reason == "limit_reached"
    assert adb.swipes == []
    assert scanner.calls == [(True, "ABC", False)]
    state_path = tmp_path / "scans" / "scan-last" / "debug" / "batch" / "resume_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["same_as_last"] is False
    assert state["resume_pre_switch_executed"] is False


def test_resume_different_reliable_name_does_not_require_cp_or_swipe(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-name-first.csv"
    _seed_resume_csv(tmp_path, destination)
    adb = FakeBatchAdb([PNG_A])
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-next", "下一隻", 100)],
    )
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                ("下一隻",),
                details={"hp": "60/60HP", "summary_evidence": "name_hp"},
            ),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ],
        summary_nickname="下一隻",
    )

    result = BatchScanService(adb, scanner, detector).scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=True,
        delay_seconds=0,
    )

    assert result.stop_reason == "limit_reached"
    assert detector.cp_calls == 0
    assert adb.swipes == []
    assert scanner.calls == [(True, "ABC", False)]


def test_resume_same_name_retries_cp_then_uses_existing_strict_switch(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-cp-retry.csv"
    _seed_resume_csv(tmp_path, destination)
    clock = FakeTime()
    adb = FakeBatchAdb([PNG_B, PNG_B, PNG_B, PNG_A])
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-next", "下一隻", 100)],
    )
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                ("睡睡菇",),
                details={"hp": "57/57HP", "summary_evidence": "name_hp"},
            ),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ],
        summary_nickname="睡睡菇",
        summary_cps=[431],
    )

    result = BatchScanService(
        adb,
        scanner,
        detector,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=True,
        delay_seconds=0,
    )

    assert result.stop_reason == "limit_reached"
    assert detector.cp_calls == 1
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]


def test_resume_same_name_uses_strong_no_cp_fallback_before_one_swipe(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-strong-fallback.csv"
    _seed_resume_csv(tmp_path, destination)
    clock = FakeTime()
    adb = FakeBatchAdb([PNG_B, PNG_B, PNG_B, PNG_A])
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-next", "下一隻", 100)],
    )
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                ("睡睡菇",),
                details={"hp": "57/57HP", "summary_evidence": "name_hp"},
            ),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ],
        summary_nickname="睡睡菇",
        summary_cps=[None, None],
        summary_hps=["57/57HP"],
    )

    result = BatchScanService(
        adb,
        scanner,
        detector,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=True,
        delay_seconds=0,
    )

    assert result.stop_reason == "limit_reached"
    assert detector.cp_calls == 2
    assert detector.hp_calls == 1
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]


def test_resume_cp_missing_weak_fallback_aborts_without_swipe(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-weak-fallback.csv"
    _seed_resume_csv(tmp_path, destination)
    clock = FakeTime()
    adb = FakeBatchAdb([PNG_B, PNG_B, PNG_B])
    scanner = FakeScanner(tmp_path / "scans", [])
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                ("睡睡菇",),
                details={"hp": "57/57HP", "summary_evidence": "name_hp"},
            ),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ],
        summary_nickname="睡睡菇",
        summary_cps=[None, None],
        summary_hps=["58/58HP"],
    )

    with pytest.raises(BatchAutomationError, match="could not read CP"):
        BatchScanService(
            adb,
            scanner,
            detector,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        ).scan(
            limit=2,
            csv_path=destination,
            debug=True,
            resume=True,
            delay_seconds=0,
        )

    assert adb.swipes == []
    assert scanner.calls == []


def test_resume_cp_retry_rejects_changed_static_page_without_swipe(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-cp-page-change.csv"
    _seed_resume_csv(tmp_path, destination)
    changed = Image.new("RGB", (1440, 3120), (240, 240, 240))
    ImageDraw.Draw(changed).rectangle((720, 1550, 1340, 2300), fill=(10, 10, 10))
    output = io.BytesIO()
    changed.save(output, format="PNG")
    clock = FakeTime()
    adb = FakeBatchAdb([PNG_B, output.getvalue()])
    scanner = FakeScanner(tmp_path / "scans", [])
    detector = QueueDetector(
        [
            PageDetection(
                "detail_summary",
                0.99,
                ("睡睡菇",),
                details={"hp": "57/57HP", "summary_evidence": "name_hp"},
            ),
        ],
        summary_nickname="睡睡菇",
    )

    with pytest.raises(BatchAutomationError, match="page changed"):
        BatchScanService(
            adb,
            scanner,
            detector,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        ).scan(
            limit=2,
            csv_path=destination,
            debug=True,
            resume=True,
            delay_seconds=0,
        )

    assert detector.cp_calls == 0
    assert adb.swipes == []
    assert scanner.calls == []


def test_renamed_resume_switches_only_after_expected_nickname_matches(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-renamed.csv"
    _seed_renamed_resume_csv(tmp_path, destination)
    adb = FakeBatchAdb([PNG_B, PNG_B, PNG_A])
    scanner = FakeScanner(
        tmp_path / "renamed-scans",
        [_recognition("scan-next", "下一隻", 100)],
    )
    expected = "睡睡菇15/14/13"
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ],
        summary_nickname=expected,
    )

    result = BatchScanService(adb, scanner, detector).scan(
        limit=2,
        csv_path=destination,
        debug=True,
        resume=True,
        delay_seconds=0,
        rename_with_iv=True,
    )

    assert result.stop_reason == "limit_reached"
    assert adb.swipes == [(1180, 1500, 260, 1500, 600)]
    assert scanner.calls == [(True, "ABC", True)]


def test_renamed_resume_ambiguous_nickname_stops_without_input(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-renamed-ambiguous.csv"
    _seed_renamed_resume_csv(tmp_path, destination)
    adb = FakeBatchAdb([PNG_B])
    scanner = FakeScanner(tmp_path / "renamed-scans", [])
    expected = "睡睡菇15/14/13"
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
        ],
        summary_nicknames=[expected, "意外暱稱", expected],
    )

    with pytest.raises(BatchAutomationError, match="state is ambiguous"):
        BatchScanService(adb, scanner, detector).scan(
            limit=2,
            csv_path=destination,
            debug=True,
            resume=True,
            delay_seconds=0,
            rename_with_iv=True,
        )

    assert adb.swipes == []
    assert scanner.calls == []


def test_renamed_resume_no_cp_fallback_requires_saved_wide_nickname(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "resume-renamed-no-cp.csv"
    _seed_renamed_resume_csv(tmp_path, destination)
    clock = FakeTime()
    adb = FakeBatchAdb([PNG_B, PNG_B, PNG_B])
    scanner = FakeScanner(tmp_path / "renamed-scans", [])
    expected = "睡睡菇15/14/13"
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
            PageDetection(
                "detail_summary",
                0.99,
                (expected,),
                details={"hp": "57/57HP", "summary_evidence": "name_hp"},
            ),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", expected)),
        ],
        summary_nicknames=[expected, "錯誤暱稱", expected],
        summary_cps=[None, None],
        summary_hps=["57/57HP"],
    )

    with pytest.raises(BatchAutomationError, match="could not read CP"):
        BatchScanService(
            adb,
            scanner,
            detector,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        ).scan(
            limit=2,
            csv_path=destination,
            debug=True,
            resume=True,
            delay_seconds=0,
            rename_with_iv=True,
        )

    assert adb.swipes == []
    assert scanner.calls == []


def test_adjacent_duplicate_guard_refuses_csv_append(tmp_path: Path) -> None:
    destination = tmp_path / "duplicate.csv"
    _seed_resume_csv(tmp_path, destination)
    adb = FakeBatchAdb([PNG_A])
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-duplicate", "睡睡菇", 431)],
    )
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP100", "下一隻")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
            PageDetection("detail_summary", 0.99, ("CP431", "睡睡菇")),
        ]
    )
    service = BatchScanService(adb, scanner, detector)

    with pytest.raises(BatchAutomationError, match="adjacent duplicate"):
        service.scan(
            limit=2,
            csv_path=destination,
            debug=True,
            resume=True,
            delay_seconds=0,
        )

    with destination.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 1


def test_static_fingerprint_ignores_animated_upper_screen() -> None:
    first = Image.new("RGB", (1440, 3120), (245, 245, 245))
    second = first.copy()
    ImageDraw.Draw(first).rectangle((0, 0, 1439, 1549), fill=(10, 80, 180))
    ImageDraw.Draw(second).rectangle((0, 0, 1439, 1549), fill=(220, 40, 30))
    for image in (first, second):
        draw = ImageDraw.Draw(image)
        draw.rectangle((200, 1700, 500, 1850), fill=(30, 30, 30))
        draw.rectangle((900, 2000, 1200, 2200), fill=(90, 90, 90))
    buffers: list[bytes] = []
    for image in (first, second):
        output = io.BytesIO()
        image.save(output, format="PNG")
        buffers.append(output.getvalue())

    first_hash = page_fingerprint(buffers[0])
    second_hash = page_fingerprint(buffers[1])

    assert first_hash == second_hash
    assert fingerprint_distance(first_hash, second_hash) == 0
