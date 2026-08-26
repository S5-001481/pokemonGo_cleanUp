"""Focused tests for OCR normalization and IV endpoint quantization."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from pokemon_go_cleanup.recognition import (
    CP_RECT,
    MOVE_ANCHOR_RECT,
    SPECIAL_MOVE_ANCHOR_RECT,
    OcrCandidate,
    RecognitionResult,
    RecognitionService,
    RecognizedInteger,
    RecognizedText,
    extract_move_candidates,
    normalize_ocr_text,
    parse_cp_raw,
    quantize_iv_endpoint,
    write_inventory_csv,
)


class FakeMoveCv2:
    INTER_CUBIC = 1

    @staticmethod
    def resize(
        source: object,
        _destination_size: None,
        *,
        fx: float,
        fy: float,
        interpolation: int,
    ) -> object:
        assert fx == 2
        assert fy == 2
        assert interpolation == FakeMoveCv2.INTER_CUBIC
        return source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CP1761", 1761),
        (" cp 276 ", 276),
        ("066d0", 660),
        ("no digits", None),
    ],
)
def test_parse_cp_raw(raw: str, expected: int | None) -> None:
    assert parse_cp_raw(raw) == expected


def test_normalize_ocr_text_preserves_traditional_chinese() -> None:
    raw = "  污泥　攻擊" + chr(0xFF0C) + " "
    assert normalize_ocr_text(raw) == "污泥 攻擊"


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        (202, 1),
        (331, 5),
        (499, 10),
        (603, 13),
        (665, 15),
    ],
)
def test_quantize_iv_endpoint(endpoint: int, expected: int) -> None:
    assert quantize_iv_endpoint(endpoint) == expected


def test_extract_move_candidates_ignores_ui_labels() -> None:
    candidates = (
        OcrCandidate("道館對戰&團體戰", 0.99, (1, 10, 10, 20)),
        OcrCandidate("電光一閃", 0.98, (1, 30, 10, 40)),
        OcrCandidate("新攻撃招式", 0.99, (1, 80, 10, 88)),
        OcrCandidate("暗影獎勵", 0.99, (1, 45, 10, 55)),
        OcrCandidate("極巨拳鬥", 0.99, (1, 50, 10, 58)),
        OcrCandidate("放電", 0.97, (1, 60, 10, 70)),
        OcrCandidate("新攻擊招式", 0.99, (1, 90, 10, 100)),
    )

    selected = extract_move_candidates(candidates)

    assert [candidate.raw for candidate in selected] == ["電光一閃", "放電"]


@pytest.mark.parametrize(
    ("special_label", "expected_remark"),
    (("暗影獎勵", "类型：暗影"), ("極巨招式", "类型：极巨化")),
)
def test_moves_uses_special_layout_only_after_normal_anchor_miss(
    monkeypatch: pytest.MonkeyPatch,
    special_label: str,
    expected_remark: str,
) -> None:
    reader = object.__new__(RecognitionService)
    reader._cv2 = FakeMoveCv2()
    observed_rectangles: list[tuple[int, int, int, int]] = []
    special_candidates = (
        OcrCandidate("道館對戰&團體戰", 0.98, (320, 890, 650, 940)),
        OcrCandidate("躍起", 0.99, (190, 1020, 340, 1110)),
        OcrCandidate(special_label, 0.99, (310, 1140, 475, 1190)),
        OcrCandidate("遷怒", 0.99, (190, 1250, 340, 1330)),
        OcrCandidate("天氣優勢", 0.99, (310, 1360, 475, 1415)),
    )
    move_candidates = (
        OcrCandidate("躍起", 0.99, (190, 1020, 340, 1110)),
        OcrCandidate(special_label, 0.99, (310, 1140, 475, 1190)),
        OcrCandidate("遷怒", 0.99, (190, 1250, 340, 1330)),
        OcrCandidate("天氣優勢", 0.99, (310, 1360, 475, 1415)),
    )

    monkeypatch.setattr(reader, "_crop", lambda _image, rectangle: rectangle)
    monkeypatch.setattr(reader, "_sharpen", lambda crop: crop)
    monkeypatch.setattr(reader, "_candidate_crop", lambda _image, item: item.raw)

    def run_ocr(
        _prepared: object,
        rectangle: tuple[int, int, int, int],
        scale: float,
    ) -> tuple[OcrCandidate, ...]:
        assert scale == 2.0
        observed_rectangles.append(rectangle)
        if rectangle == MOVE_ANCHOR_RECT:
            return ()
        if rectangle == SPECIAL_MOVE_ANCHOR_RECT:
            return special_candidates
        return move_candidates

    monkeypatch.setattr(reader, "_run_ocr", run_ocr)
    warnings: list[str] = []

    moves, debug, _fallback = reader._moves(object(), warnings)

    assert [candidate.raw for candidate in moves] == ["躍起", "遷怒"]
    assert debug == ("躍起", "遷怒")
    assert warnings == [expected_remark]
    assert observed_rectangles[:2] == [MOVE_ANCHOR_RECT, SPECIAL_MOVE_ANCHOR_RECT]


def test_moves_normal_anchor_skips_shadow_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = object.__new__(RecognitionService)
    reader._cv2 = FakeMoveCv2()
    observed_rectangles: list[tuple[int, int, int, int]] = []
    anchor = OcrCandidate("道館對戰&團體戰", 0.98, (320, 1200, 650, 1250))

    monkeypatch.setattr(reader, "_crop", lambda _image, rectangle: rectangle)
    monkeypatch.setattr(reader, "_sharpen", lambda crop: crop)
    monkeypatch.setattr(reader, "_candidate_crop", lambda _image, item: item.raw)

    def run_ocr(
        _prepared: object,
        rectangle: tuple[int, int, int, int],
        _scale: float,
    ) -> tuple[OcrCandidate, ...]:
        observed_rectangles.append(rectangle)
        if rectangle == MOVE_ANCHOR_RECT:
            return (anchor,)
        if rectangle == SPECIAL_MOVE_ANCHOR_RECT:
            raise AssertionError("normal moves must not run shadow fallback OCR")
        return (
            OcrCandidate("火焰旋渦", 0.99, (190, 1320, 390, 1400)),
            OcrCandidate("噴射火焰", 0.99, (190, 1450, 390, 1530)),
        )

    monkeypatch.setattr(reader, "_run_ocr", run_ocr)
    warnings: list[str] = []

    moves, _debug, _fallback = reader._moves(object(), warnings)

    assert [candidate.raw for candidate in moves] == ["火焰旋渦", "噴射火焰"]
    assert warnings == []
    assert SPECIAL_MOVE_ANCHOR_RECT not in observed_rectangles


def test_moves_rejects_upper_anchor_without_shadow_modifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = object.__new__(RecognitionService)
    reader._cv2 = FakeMoveCv2()
    monkeypatch.setattr(reader, "_crop", lambda _image, rectangle: rectangle)
    monkeypatch.setattr(reader, "_sharpen", lambda crop: crop)

    def run_ocr(
        _prepared: object,
        rectangle: tuple[int, int, int, int],
        _scale: float,
    ) -> tuple[OcrCandidate, ...]:
        if rectangle == MOVE_ANCHOR_RECT:
            return ()
        return (
            OcrCandidate("道館對戰&團體戰", 0.98, (320, 890, 650, 940)),
            OcrCandidate("躍起", 0.99, (190, 1020, 340, 1110)),
        )

    monkeypatch.setattr(reader, "_run_ocr", run_ocr)
    warnings: list[str] = []

    moves, debug, fallback = reader._moves(object(), warnings)

    assert moves == ()
    assert debug == ()
    assert fallback == SPECIAL_MOVE_ANCHOR_RECT
    assert warnings == ["Move section anchor was not found; moves are null."]


def test_cp_variants_stop_before_fallback_when_ordinary_cp_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = object.__new__(RecognitionService)
    ordinary = (OcrCandidate("CP1217", 0.93, (450, 270, 890, 448)),)
    fallback_calls = 0
    hsv_calls = 0

    def ordinary_variants(
        image: object,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[tuple[OcrCandidate, ...], object]:
        assert rectangle == CP_RECT
        return ordinary, image

    def fallback_variants(image: object) -> tuple[OcrCandidate, ...]:
        nonlocal fallback_calls
        fallback_calls += 1
        return (OcrCandidate("CP121", 0.99, (430, 250, 850, 460)),)

    def hsv_variants(image: object) -> tuple[OcrCandidate, ...]:
        nonlocal hsv_calls
        hsv_calls += 1
        return ()

    monkeypatch.setattr(reader, "_ocr_variants", ordinary_variants)
    monkeypatch.setattr(reader, "_ocr_cp_hsv_variants", hsv_variants)
    monkeypatch.setattr(reader, "_ocr_cp_fallback_variants", fallback_variants)
    image = object()

    candidates, debug_image = reader._ocr_cp_variants(image)

    assert candidates == ordinary
    assert debug_image is image
    assert fallback_calls == 0
    assert hsv_calls == 0
    assert reader._best_cp(candidates) == ordinary[0]


def test_cp_variants_run_hsv_only_when_ordinary_cp_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = object.__new__(RecognitionService)
    hsv = (OcrCandidate("CP300", 0.97, (500, 300, 650, 360)),)
    fallback_calls = 0

    monkeypatch.setattr(
        reader,
        "_ocr_variants",
        lambda image, rectangle: (
            (OcrCandidate("300", 0.99, (500, 300, 650, 360)),),
            image,
        ),
    )

    def fallback_variants(image: object) -> tuple[OcrCandidate, ...]:
        nonlocal fallback_calls
        fallback_calls += 1
        return (OcrCandidate("CP999", 0.99, (500, 300, 650, 360)),)

    monkeypatch.setattr(reader, "_ocr_cp_hsv_variants", lambda _image: hsv)
    monkeypatch.setattr(reader, "_ocr_cp_fallback_variants", fallback_variants)

    candidates, _ = reader._ocr_cp_variants(object())

    assert fallback_calls == 0
    assert reader._best_cp(candidates) == hsv[0]


def test_cp_variants_run_threshold_only_after_hsv_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = object.__new__(RecognitionService)
    fallback = (OcrCandidate("CP300", 0.97, (500, 300, 650, 360)),)
    monkeypatch.setattr(reader, "_ocr_variants", lambda _image, _rectangle: ((), object()))
    monkeypatch.setattr(reader, "_ocr_cp_hsv_variants", lambda _image: ())
    monkeypatch.setattr(reader, "_ocr_cp_fallback_variants", lambda _image: fallback)

    candidates, _ = reader._ocr_cp_variants(object())

    assert reader._best_cp(candidates) == fallback[0]


@pytest.mark.parametrize(
    "candidate",
    (
        OcrCandidate("10", 0.999, (600, 300, 760, 430)),
        OcrCandidate("458", 0.94, (472, 273, 857, 448)),
        OcrCandidate("000", 0.51, (349, 220, 923, 480)),
        OcrCandidate("000", 0.999, (472, 273, 857, 448)),
    ),
)
def test_hsv_cp_rejects_background_or_weak_bare_numbers(
    candidate: OcrCandidate,
) -> None:
    assert RecognitionService._validated_hsv_cp_candidate(candidate) is None


def test_hsv_cp_accepts_strong_full_width_digits_in_cp_band() -> None:
    candidate = OcrCandidate("458", 0.9999, (472, 273, 857, 448))

    assert RecognitionService._validated_hsv_cp_candidate(candidate) == OcrCandidate(
        "CP458",
        0.9999,
        (472, 273, 857, 448),
    )


def test_run_ocr_accumulates_only_engine_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = object.__new__(RecognitionService)
    reader._ocr_seconds_total = 0.0
    reader._ocr = lambda image: type("EmptyOcrResult", (), {"txts": None})()
    samples = iter((10.0, 10.25))
    monkeypatch.setattr(
        "pokemon_go_cleanup.recognition.time.monotonic",
        lambda: next(samples),
    )

    result = reader._run_ocr(object(), CP_RECT, 1.0)

    assert result == ()
    assert reader.ocr_seconds_total == 0.25


def test_inventory_csv_warnings_include_special_type_remarks(tmp_path: Path) -> None:
    def result(scan_id: str, remark: str) -> RecognitionResult:
        return RecognitionResult(
            scan_id=scan_id,
            pokemon_name=RecognizedText(value="豪力", raw="豪力", confidence=0.99),
            cp=RecognizedInteger(value=1123, raw="CP1123", confidence=0.99),
            fast_move=RecognizedText(value="踢倒", raw="踢倒", confidence=0.99),
            charged_move_1=RecognizedText(
                value="地獄翻滾",
                raw="地獄翻滾",
                confidence=0.99,
            ),
            charged_move_2=RecognizedText(value=None, raw=None, confidence=None),
            attack_iv=13,
            defense_iv=11,
            hp_iv=12,
            warnings=(remark,),
        )

    destination = write_inventory_csv(
        tmp_path / "inventory.csv",
        (result("shadow", "类型：暗影"), result("dynamax", "类型：极巨化")),
    )

    with destination.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert [row["warnings"] for row in rows] == ["类型：暗影", "类型：极巨化"]
