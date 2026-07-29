"""Focused tests for OCR normalization and IV endpoint quantization."""

from __future__ import annotations

import pytest

from pokemon_go_cleanup.recognition import (
    OcrCandidate,
    extract_move_candidates,
    normalize_ocr_text,
    parse_cp_raw,
    quantize_iv_endpoint,
)


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
        OcrCandidate("放電", 0.97, (1, 60, 10, 70)),
        OcrCandidate("新攻擊招式", 0.99, (1, 90, 10, 100)),
    )

    selected = extract_move_candidates(candidates)

    assert [candidate.raw for candidate in selected] == ["電光一閃", "放電"]
