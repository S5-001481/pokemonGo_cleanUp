"""Focused tests for OCR normalization and IV endpoint quantization."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from pokemon_go_cleanup.recognition import (
    BAR_TOP,
    BAR_UPWARD_FALLBACK_TOP,
    CP_RECT,
    MOVE_ANCHOR_RECT,
    NAME_RECT,
    SPECIAL_MOVE_ANCHOR_RECT,
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
    extract_move_candidates,
    normalize_ocr_text,
    parse_cp_raw,
    quantize_iv_endpoint,
    write_inventory_csv,
)

REAL_SHIFTED_APPRAISAL_DIRECTORY = (
    Path(__file__).parents[1]
    / "data/scans/2026-08-27/20260827_224510_336106_4d6062a9fed44d7eb678fa76bcea62f2"
    / "debug/automation"
)
REAL_SHIFTED_APPRAISAL_FRAMES = tuple(
    sorted(REAL_SHIFTED_APPRAISAL_DIRECTORY.glob("appraisal_bars_wait_*.png"))
)
REAL_SHIFTED_APPRAISAL_NEGATIVE_FRAMES = tuple(
    REAL_SHIFTED_APPRAISAL_DIRECTORY / name
    for name in (
        "after_open_action_menu_attempt_1.png",
        "before_open_appraisal.png",
        "before_advance_appraisal_dialogue_1.png",
        "appraisal_entry_wait_01.png",
        "appraisal_entry_wait_02.png",
        "appraisal_entry_wait_03.png",
        "appraisal_entry_wait_04.png",
    )
)
REAL_STANDARD_APPRAISAL = (
    Path(__file__).parents[1]
    / "data/scans/2026-08-27/20260827_221858_671677_db82e2a07bfb4ff68068efcd79ce89e3"
    / "appraisal.png"
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


class FakeClahe:
    @staticmethod
    def apply(_source: object) -> str:
        return "contrast"


class FakeVariantCv2:
    COLOR_BGR2GRAY = 1
    COLOR_GRAY2BGR = 2
    INTER_CUBIC = 3

    @staticmethod
    def cvtColor(source: object, conversion: int) -> object:
        if conversion == FakeVariantCv2.COLOR_BGR2GRAY:
            return "gray"
        assert conversion == FakeVariantCv2.COLOR_GRAY2BGR
        assert source == "contrast"
        return "contrast_bgr"

    @staticmethod
    def createCLAHE(*, clipLimit: float, tileGridSize: tuple[int, int]) -> FakeClahe:
        assert clipLimit == 2.0
        assert tileGridSize == (8, 8)
        return FakeClahe()

    @staticmethod
    def resize(
        source: object,
        _destination_size: None,
        *,
        fx: float,
        fy: float,
        interpolation: int,
    ) -> tuple[object, float]:
        assert fy == fx
        assert interpolation == FakeVariantCv2.INTER_CUBIC
        return source, fx


def progressive_variant_reader(
    monkeypatch: pytest.MonkeyPatch,
    responses: tuple[tuple[OcrCandidate, ...], ...],
) -> tuple[RecognitionService, list[float]]:
    reader = object.__new__(RecognitionService)
    reader._cv2 = FakeVariantCv2()
    calls: list[float] = []
    monkeypatch.setattr(reader, "_crop", lambda _image, _rectangle: "crop")
    monkeypatch.setattr(reader, "_sharpen", lambda _crop: "sharpened")

    def run_ocr(
        _prepared: object,
        _rectangle: tuple[int, int, int, int],
        scale: float,
    ) -> tuple[OcrCandidate, ...]:
        calls.append(scale)
        return responses[len(calls) - 1]

    monkeypatch.setattr(reader, "_run_ocr", run_ocr)
    return reader, calls


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


@pytest.mark.skipif(
    len(REAL_SHIFTED_APPRAISAL_FRAMES) != 9,
    reason="local ignored shifted 来悲茶 appraisal frames are unavailable",
)
def test_real_shifted_appraisal_frames_use_upward_fallback_for_14_11_14(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = RecognitionService()
    original = reader._bar_rows
    observed_tops: list[int] = []

    def tracked_bar_rows(
        image: object,
        *,
        top: int,
        require_unique: bool,
    ) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None:
        observed_tops.append(top)
        return original(image, top=top, require_unique=require_unique)

    monkeypatch.setattr(reader, "_bar_rows", tracked_bar_rows)

    for frame in REAL_SHIFTED_APPRAISAL_FRAMES:
        bars, _, _ = reader._ivs(reader._image(frame))

        assert bars is not None
        assert tuple(bar.value for bar in bars) == (14, 11, 14)
        assert tuple(bar.y_start for bar in bars) == (2136, 2269, 2400)

    assert observed_tops == [BAR_TOP, BAR_UPWARD_FALLBACK_TOP] * 9


@pytest.mark.skipif(
    not REAL_STANDARD_APPRAISAL.is_file(),
    reason="local ignored standard appraisal frame is unavailable",
)
def test_real_standard_appraisal_does_not_run_upward_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = RecognitionService()
    original = reader._bar_rows
    observed_tops: list[int] = []

    def tracked_bar_rows(
        image: object,
        *,
        top: int,
        require_unique: bool,
    ) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None:
        observed_tops.append(top)
        return original(image, top=top, require_unique=require_unique)

    monkeypatch.setattr(reader, "_bar_rows", tracked_bar_rows)

    bars, _, _ = reader._ivs(reader._image(REAL_STANDARD_APPRAISAL))

    assert bars is not None
    assert tuple(bar.value for bar in bars) == (12, 13, 14)
    assert observed_tops == [BAR_TOP]


@pytest.mark.skipif(
    not all(frame.is_file() for frame in REAL_SHIFTED_APPRAISAL_NEGATIVE_FRAMES),
    reason="local ignored 来悲茶 pre-bar frames are unavailable",
)
def test_upward_fallback_rejects_real_menu_and_dialogue_frames() -> None:
    reader = RecognitionService()

    for frame in REAL_SHIFTED_APPRAISAL_NEGATIVE_FRAMES:
        bars, _, _ = reader._ivs(reader._image(frame))

        assert bars is None, frame.name


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


def test_ordinary_cp_first_variant_success_skips_later_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cp = OcrCandidate("CP1217", 0.93, (450, 270, 890, 448))
    reader, calls = progressive_variant_reader(monkeypatch, ((cp,),))

    candidates, _ = reader._ocr_variants(object(), CP_RECT)

    assert candidates == (cp,)
    assert calls == [2.0]


def test_ordinary_cp_second_variant_success_skips_third_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = OcrCandidate("1217", 0.999, (450, 270, 890, 448))
    cp = OcrCandidate("CP1217", 0.93, (450, 270, 890, 448))
    reader, calls = progressive_variant_reader(monkeypatch, ((invalid,), (cp,)))

    candidates, _ = reader._ocr_variants(object(), CP_RECT)

    assert candidates == (invalid, cp)
    assert calls == [2.0, 3.0]


def test_ordinary_cp_illegal_high_confidence_never_exits_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    illegal = OcrCandidate("1217", 0.999, (450, 270, 890, 448))
    reader, calls = progressive_variant_reader(
        monkeypatch,
        ((illegal,), (illegal,), (illegal,)),
    )

    candidates, _ = reader._ocr_variants(object(), CP_RECT)

    assert candidates == (illegal, illegal, illegal)
    assert calls == [2.0, 3.0, 4.0]


def test_all_ordinary_cp_variants_fail_then_existing_fallbacks_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    illegal = OcrCandidate("1217", 0.999, (450, 270, 890, 448))
    fallback = OcrCandidate("CP1217", 0.97, (450, 270, 890, 448))
    reader, calls = progressive_variant_reader(
        monkeypatch,
        ((illegal,), (illegal,), (illegal,)),
    )
    hsv_calls = 0
    threshold_calls = 0

    def hsv_variants(_image: object) -> tuple[OcrCandidate, ...]:
        nonlocal hsv_calls
        hsv_calls += 1
        return ()

    def threshold_variants(_image: object) -> tuple[OcrCandidate, ...]:
        nonlocal threshold_calls
        threshold_calls += 1
        return (fallback,)

    monkeypatch.setattr(reader, "_ocr_cp_hsv_variants", hsv_variants)
    monkeypatch.setattr(reader, "_ocr_cp_fallback_variants", threshold_variants)

    candidates, _ = reader._ocr_cp_variants(object())

    assert calls == [2.0, 3.0, 4.0]
    assert hsv_calls == 1
    assert threshold_calls == 1
    assert reader._best_cp(candidates) == fallback


def test_ordinary_name_first_variant_success_skips_later_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = OcrCandidate("冰雪龍", 0.99, (600, 1350, 840, 1450))
    reader, calls = progressive_variant_reader(monkeypatch, ((name,),))

    candidates, _ = reader._ocr_variants(object(), NAME_RECT)

    assert candidates == (name,)
    assert calls == [2.0]


def test_ordinary_name_second_variant_success_skips_third_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fragment = OcrCandidate("怒", 0.98, (600, 1350, 680, 1450))
    name = OcrCandidate("怒鸚哥", 0.99, (600, 1350, 840, 1450))
    reader, calls = progressive_variant_reader(monkeypatch, ((fragment,), (name,)))

    candidates, _ = reader._ocr_variants(object(), NAME_RECT)

    assert candidates == (fragment, name)
    assert calls == [2.0, 3.0]
    assert reader._best_text(candidates) == name


def test_ordinary_name_single_character_never_exits_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fragment = OcrCandidate("怒", 0.999, (600, 1350, 680, 1450))
    reader, calls = progressive_variant_reader(
        monkeypatch,
        ((fragment,), (fragment,), (fragment,)),
    )

    candidates, _ = reader._ocr_variants(object(), NAME_RECT)

    assert candidates == (fragment, fragment, fragment)
    assert calls == [2.0, 3.0, 4.0]


def test_ordinary_name_split_tokens_require_another_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    split = (
        OcrCandidate("怒", 0.98, (560, 1350, 640, 1450)),
        OcrCandidate("鸚哥", 0.97, (650, 1350, 840, 1450)),
    )
    name = OcrCandidate("怒鸚哥", 0.999, (560, 1350, 840, 1450))
    reader, calls = progressive_variant_reader(monkeypatch, (split, (name,)))

    candidates, _ = reader._ocr_variants(object(), NAME_RECT)

    assert candidates == (*split, name)
    assert calls == [2.0, 3.0]
    assert reader._best_text(candidates) == name


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


@pytest.mark.parametrize("remark", ("类型：暗影", "类型：极巨化"))
def test_complete_evidence_matches_read_scan_output_and_special_remarks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    remark: str,
) -> None:
    reader = object.__new__(RecognitionService)
    name = OcrCandidate("冰雪龍", 0.99, (600, 1350, 840, 1450))
    cp = OcrCandidate("CP1217", 0.98, (450, 270, 890, 448))
    moves = (
        OcrCandidate("冰息", 0.97, (400, 1500, 600, 1600)),
        OcrCandidate("遷怒", 0.96, (400, 1700, 600, 1800)),
    )
    bars = (
        BarDetection(2150, 2180, None, 0),
        BarDetection(2290, 2320, 602, 13),
        BarDetection(2430, 2460, 665, 15),
    )
    debug_image = object()
    manifest = type("Manifest", (), {"scan_id": "evidence-equivalence"})()
    monkeypatch.setattr(reader, "_manifest", lambda _path: manifest)
    monkeypatch.setattr(reader, "_image", lambda _path: object())
    monkeypatch.setattr(
        reader,
        "_ocr_variants",
        lambda _image, _rectangle: ((name,), debug_image),
    )
    monkeypatch.setattr(
        reader,
        "_ocr_cp_variants",
        lambda _image: ((cp,), debug_image),
    )

    def read_moves(
        _image: object,
        warnings: list[str],
    ) -> tuple[tuple[OcrCandidate, ...], tuple[object, ...], object]:
        warnings.append(remark)
        return moves, (debug_image, debug_image), debug_image

    monkeypatch.setattr(reader, "_moves", read_moves)
    monkeypatch.setattr(
        reader,
        "_ivs",
        lambda _image: (bars, debug_image, debug_image),
    )
    scan_directory = tmp_path / "evidence-equivalence"
    scan_directory.mkdir()
    frame_bytes = {
        "summary": b"summary-frame",
        "moves": b"moves-frame",
        "appraisal": b"appraisal-frame",
    }
    for stem, content in frame_bytes.items():
        (scan_directory / f"{stem}.png").write_bytes(content)

    old_result = reader.read_scan(scan_directory)
    evidence = RecognitionEvidence(
        summary=SummaryRecognitionEvidence(
            png_sha256=hashlib.sha256(frame_bytes["summary"]).hexdigest(),
            name_candidates=(name,),
            cp_candidates=(cp,),
            name_debug=debug_image,
            cp_debug=debug_image,
        ),
        moves=MovesRecognitionEvidence(
            png_sha256=hashlib.sha256(frame_bytes["moves"]).hexdigest(),
            candidates=moves,
            warnings=(remark,),
            debug=(debug_image, debug_image),
            fallback_debug=debug_image,
        ),
        appraisal=AppraisalRecognitionEvidence(
            png_sha256=hashlib.sha256(frame_bytes["appraisal"]).hexdigest(),
            bars=bars,
            bars_debug=debug_image,
            detection_debug=debug_image,
        ),
    )

    evidence_result = reader.read_evidence(scan_directory, evidence)

    assert evidence_result == old_result
    assert evidence_result is not None
    assert evidence_result.warnings == (remark,)
    assert evidence_result.attack_iv == 0


def test_frame_hash_mismatch_declines_live_evidence(tmp_path: Path) -> None:
    reader = object.__new__(RecognitionService)
    scan_directory = tmp_path / "frame-mismatch"
    scan_directory.mkdir()
    for stem in ("summary", "moves", "appraisal"):
        (scan_directory / f"{stem}.png").write_bytes(stem.encode())
    cp = OcrCandidate("CP1", 0.99, (450, 270, 890, 448))
    move = OcrCandidate("拍擊", 0.99, (400, 1500, 600, 1600))
    bar = BarDetection(2150, 2180, None, 0)
    evidence = RecognitionEvidence(
        summary=SummaryRecognitionEvidence(
            png_sha256=hashlib.sha256(b"different-summary-frame").hexdigest(),
            name_candidates=(OcrCandidate("夢幻", 0.99, (600, 1350, 840, 1450)),),
            cp_candidates=(cp,),
            name_debug=object(),
            cp_debug=object(),
        ),
        moves=MovesRecognitionEvidence(
            png_sha256=hashlib.sha256(b"moves").hexdigest(),
            candidates=(move, move),
            warnings=(),
            debug=(),
            fallback_debug=object(),
        ),
        appraisal=AppraisalRecognitionEvidence(
            png_sha256=hashlib.sha256(b"appraisal").hexdigest(),
            bars=(bar, bar, bar),
            bars_debug=object(),
            detection_debug=object(),
        ),
    )

    assert reader.read_evidence(scan_directory, evidence) is None
    assert not (scan_directory / "recognition.json").exists()
