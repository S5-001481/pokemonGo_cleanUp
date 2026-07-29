"""Focused tests for bounded batch persistence and switching."""

from __future__ import annotations

import csv
import io
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from pokemon_go_cleanup.automation import AutoScanResult, PageDetection
from pokemon_go_cleanup.batch import (
    BatchScanService,
    HuaweiMate30BatchConfig,
    page_fingerprint,
)
from pokemon_go_cleanup.models import Device, ScanManifest, ScreenResolution
from pokemon_go_cleanup.recognition import (
    RecognitionResult,
    RecognizedInteger,
    RecognizedText,
)
from pokemon_go_cleanup.storage import atomic_write_bytes


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
    def __init__(self, detections: list[PageDetection]) -> None:
        self.detections = deque(detections)

    def detect(
        self,
        png_bytes: bytes,
        *,
        expected: tuple[str, ...],
    ) -> PageDetection:
        assert expected
        return self.detections.popleft()


class FakeScanner:
    def __init__(self, root: Path, results: list[RecognitionResult]) -> None:
        self.root = root
        self.results = deque(results)
        self.calls: list[tuple[bool, str | None]] = []

    def scan_one(
        self,
        *,
        debug: bool = False,
        dry_run: bool = False,
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
        self.calls.append((debug, serial_number))
        return AutoScanResult(
            scan_directory=directory,
            manifest_path=directory / "manifest.json",
            manifest=manifest,
            recognition=recognition,
            dry_run=False,
            planned_actions=(),
        )


def _recognition(scan_id: str, name: str, cp: int) -> RecognitionResult:
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
    )


def test_batch_scans_two_distinct_pokemon_and_persists_each_row(
    tmp_path: Path,
) -> None:
    clock = FakeTime()
    scanner = FakeScanner(
        tmp_path / "scans",
        [_recognition("scan-a", "甲", 100), _recognition("scan-b", "乙", 200)],
    )
    adb = FakeBatchAdb([PNG_A, PNG_B])
    detector = QueueDetector(
        [
            PageDetection("detail_summary", 0.99, ("CP100", "甲")),
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
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
    assert adb.swipes == [(260, 1500, 1180, 1500, 600)]
    assert scanner.calls == [(True, "ABC"), (True, "ABC")]
    with destination.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [row["pokemon_name"] for row in rows] == ["甲", "乙"]
    assert [row["cp"] for row in rows] == ["100", "200"]
    assert not list(tmp_path.rglob("*.tmp"))


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
            PageDetection("detail_summary", 0.99, ("CP200", "乙")),
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
    assert len(adb.swipes) == 2
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
