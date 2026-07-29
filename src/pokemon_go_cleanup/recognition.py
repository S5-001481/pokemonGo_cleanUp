"""Calibrated Huawei Mate 30 screenshot reader for the eight local scans."""

from __future__ import annotations

import csv
import importlib
import io
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pokemon_go_cleanup.exceptions import RecognitionError
from pokemon_go_cleanup.models import ScanManifest
from pokemon_go_cleanup.storage import atomic_write_text

WIDTH: Final = 1440
HEIGHT: Final = 3120
LOW_CONFIDENCE: Final = 0.85
CP_RECT: Final = (250, 220, 1150, 480)
NAME_RECT: Final = (350, 1280, 1100, 1570)
MOVE_ANCHOR_RECT: Final = (100, 1100, 1340, 2320)
BAR_START_X: Final = 171
BAR_END_X: Final = 665
BAR_TOP: Final = 2150
BAR_BOTTOM: Final = 2700
IV_ENDPOINTS: Final = (
    202,
    234,
    266,
    298,
    331,
    371,
    403,
    435,
    468,
    498,
    537,
    569,
    602,
    634,
    665,
)
CSV_COLUMNS: Final = (
    "scan_id",
    "pokemon_name",
    "cp",
    "fast_move",
    "charged_move_1",
    "charged_move_2",
    "attack_iv",
    "defense_iv",
    "hp_iv",
    "warnings",
)
_MOVE_LABELS: Final = (
    "道館",
    "團體戰",
    "訓練家對戰",
    "新攻擊招式",
    "新攻撃招式",
    "暗影獎勵",
    "天氣優勢",
    "天氣加成",
    "LOCKED",
    "等級",
)
_CP_CANDIDATE_PATTERN: Final = re.compile(r"CP\s*(\d{1,5})", re.IGNORECASE)


class RecognizedText(BaseModel):
    """OCR text together with the untouched OCR output."""

    model_config = ConfigDict(frozen=True)

    value: str | None
    raw: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)


class RecognizedInteger(BaseModel):
    """OCR integer together with the untouched OCR output."""

    model_config = ConfigDict(frozen=True)

    value: int | None
    raw: str | None
    confidence: float | None = Field(default=None, ge=0, le=1)


class RecognitionResult(BaseModel):
    """One recognition.json document."""

    model_config = ConfigDict(frozen=True)

    scan_id: str
    pokemon_name: RecognizedText
    cp: RecognizedInteger
    fast_move: RecognizedText
    charged_move_1: RecognizedText
    charged_move_2: RecognizedText
    attack_iv: int | None = Field(default=None, ge=0, le=15)
    defense_iv: int | None = Field(default=None, ge=0, le=15)
    hp_iv: int | None = Field(default=None, ge=0, le=15)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OcrCandidate:
    """One OCR line in full-screen coordinates."""

    raw: str
    confidence: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class BarDetection:
    """One appraisal bar measurement."""

    y_start: int
    y_end: int
    endpoint: int | None
    value: int


def normalize_ocr_text(value: str) -> str:
    """Normalize only whitespace and compatibility punctuation."""

    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip(" \t\r\n,." + chr(0x3002))


def parse_cp_raw(raw: str) -> int | None:
    """Extract CP, including the one calibrated CP660 glyph failure."""

    normalized = normalize_ocr_text(raw)
    match = re.search(r"(?i)CP\D*(\d+)", normalized)
    if match is not None:
        return int(match.group(1))
    if not any(character.isdigit() for character in normalized):
        return None
    translation: dict[str, str | int | None] = {
        "O": "0", "o": "0", "D": "6", "d": "6"
    }
    translated = normalized.translate(str.maketrans(translation))
    digits = "".join(character for character in translated if character.isdigit())
    if translated.startswith("06") and len(digits) >= 4:
        digits = digits[2:]
    return int(digits) if digits else None


def normalize_cp_candidate(raw: str) -> str | None:
    """Return a canonical CP value only when OCR retained the CP prefix."""

    normalized = normalize_ocr_text(raw)
    match = _CP_CANDIDATE_PATTERN.fullmatch(normalized)
    if match is None:
        return None
    return f"CP{int(match.group(1))}"


def quantize_iv_endpoint(endpoint: int) -> int:
    """Map an orange/red endpoint to the nearest integer IV."""

    index = min(
        range(len(IV_ENDPOINTS)),
        key=lambda item: abs(IV_ENDPOINTS[item] - endpoint),
    )
    return index + 1


def extract_move_candidates(
    candidates: Sequence[OcrCandidate],
) -> tuple[OcrCandidate, ...]:
    """Remove UI labels and keep up to three Traditional Chinese moves."""

    selected: list[OcrCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.box[1], item.box[0])):
        value = normalize_ocr_text(candidate.raw)
        if any(label in value for label in _MOVE_LABELS):
            continue
        if not any("\u4e00" <= character <= "\u9fff" for character in value):
            continue
        selected.append(candidate)
    return tuple(selected[:3])


def _load_dependencies() -> tuple[Any, Any, Any]:
    try:
        cv2 = importlib.import_module("cv2")
        numpy = importlib.import_module("numpy")
        rapidocr = importlib.import_module("rapidocr")
    except ImportError as error:
        raise RecognitionError(
            'OCR dependencies are missing. Run: pip install -e ".[ocr]"'
        ) from error
    return cv2, numpy, rapidocr.RapidOCR


class RecognitionService:
    """Read only the calibrated 1440x3120 Traditional Chinese layout."""

    def __init__(self) -> None:
        self._cv2, self._np, rapid_ocr = _load_dependencies()
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        self._ocr: Any = rapid_ocr()

    def read_scan(self, directory: Path, *, debug: bool = False) -> RecognitionResult:
        """Read one scan and save recognition.json."""

        directory = directory.expanduser().resolve()
        manifest = self._manifest(directory / "manifest.json")
        summary = self._image(directory / "summary.png")
        moves = self._image(directory / "moves.png")
        appraisal = self._image(directory / "appraisal.png")
        warnings: list[str] = []

        name_candidates, name_debug = self._ocr_variants(summary, NAME_RECT)
        name = self._text(
            "pokemon_name",
            self._best_text(name_candidates),
            warnings,
        )
        cp_candidates, cp_debug = self._ocr_cp_variants(summary)
        cp = self._cp(self._best_cp(cp_candidates), warnings)

        move_candidates, move_debug, move_fallback = self._moves(moves, warnings)
        fast_move = self._text(
            "fast_move",
            move_candidates[0] if len(move_candidates) > 0 else None,
            warnings,
        )
        charged_move_1 = self._text(
            "charged_move_1",
            move_candidates[1] if len(move_candidates) > 1 else None,
            warnings,
        )
        charged_move_2 = self._text(
            "charged_move_2",
            move_candidates[2] if len(move_candidates) > 2 else None,
            warnings,
            nullable=True,
        )

        bars, bars_debug, detection_debug = self._ivs(appraisal)
        if bars is None:
            warnings.append("IV bars were not found; IV values are null.")
            iv_values: tuple[int | None, int | None, int | None] = (None, None, None)
        else:
            iv_values = (bars[0].value, bars[1].value, bars[2].value)

        result = RecognitionResult(
            scan_id=manifest.scan_id,
            pokemon_name=name,
            cp=cp,
            fast_move=fast_move,
            charged_move_1=charged_move_1,
            charged_move_2=charged_move_2,
            attack_iv=iv_values[0],
            defense_iv=iv_values[1],
            hp_iv=iv_values[2],
            warnings=tuple(warnings),
        )
        atomic_write_text(
            directory / "recognition.json",
            result.model_dump_json(indent=2) + "\n",
        )
        if debug:
            debug_directory = directory / "debug"
            debug_directory.mkdir(parents=True, exist_ok=True)
            self._write(debug_directory / "summary_name.png", name_debug)
            self._write(debug_directory / "summary_cp.png", cp_debug)
            debug_names = (
                "moves_fast.png",
                "moves_charged_1.png",
                "moves_charged_2.png",
            )
            for index, name_path in enumerate(debug_names):
                image = move_debug[index] if index < len(move_debug) else move_fallback
                self._write(debug_directory / name_path, image)
            self._write(debug_directory / "appraisal_bars.png", bars_debug)
            self._write(
                debug_directory / "appraisal_detection.png",
                detection_debug,
            )
        return result

    def _manifest(self, path: Path) -> ScanManifest:
        try:
            return ScanManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as error:
            raise RecognitionError(f"Could not read manifest '{path}': {error}") from error

    def _image(self, path: Path) -> Any:
        image = self._cv2.imread(str(path), self._cv2.IMREAD_COLOR)
        if image is None:
            raise RecognitionError(f"Could not decode screenshot '{path}'.")
        if image.shape[:2] != (HEIGHT, WIDTH):
            raise RecognitionError(
                f"This MVP requires {WIDTH}x{HEIGHT}; got "
                f"{image.shape[1]}x{image.shape[0]} for '{path}'."
            )
        return image

    def _ocr_variants(
        self,
        image: Any,
        rectangle: tuple[int, int, int, int],
    ) -> tuple[tuple[OcrCandidate, ...], Any]:
        crop = self._crop(image, rectangle)
        gray = self._cv2.cvtColor(crop, self._cv2.COLOR_BGR2GRAY)
        contrast = self._cv2.createCLAHE(
            clipLimit=2.0,
            tileGridSize=(8, 8),
        ).apply(gray)
        variants = (
            (crop, 2.0),
            (self._sharpen(crop), 3.0),
            (self._cv2.cvtColor(contrast, self._cv2.COLOR_GRAY2BGR), 4.0),
        )
        candidates: list[OcrCandidate] = []
        debug_image: Any = crop
        for source, scale in variants:
            prepared = self._cv2.resize(
                source,
                None,
                fx=scale,
                fy=scale,
                interpolation=self._cv2.INTER_CUBIC,
            )
            candidates.extend(self._run_ocr(prepared, rectangle, scale))
            if scale == 3.0:
                debug_image = prepared
        return tuple(candidates), debug_image

    def _ocr_cp_variants(self, image: Any) -> tuple[tuple[OcrCandidate, ...], Any]:
        """Run the existing CP OCR plus threshold variants for bright backgrounds."""

        existing, debug_image = self._ocr_variants(image, CP_RECT)
        crop = self._crop(image, CP_RECT)
        gray = self._cv2.cvtColor(crop, self._cv2.COLOR_BGR2GRAY)
        clahe = self._cv2.createCLAHE(
            clipLimit=2.0,
            tileGridSize=(8, 8),
        ).apply(gray)
        _, otsu = self._cv2.threshold(
            gray,
            0,
            255,
            self._cv2.THRESH_BINARY + self._cv2.THRESH_OTSU,
        )
        adaptive = self._cv2.adaptiveThreshold(
            gray,
            255,
            self._cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            self._cv2.THRESH_BINARY,
            31,
            11,
        )
        variants = (
            gray,
            clahe,
            otsu,
            self._cv2.bitwise_not(otsu),
            adaptive,
            self._cv2.bitwise_not(adaptive),
        )
        candidates = list(existing)
        for source in variants:
            scale = 4.0
            prepared = self._cv2.resize(
                source,
                None,
                fx=scale,
                fy=scale,
                interpolation=self._cv2.INTER_CUBIC,
            )
            candidates.extend(self._run_ocr(prepared, CP_RECT, scale))
        return tuple(candidates), debug_image

    def _run_ocr(
        self,
        image: Any,
        rectangle: tuple[int, int, int, int],
        scale: float,
    ) -> tuple[OcrCandidate, ...]:
        result = self._ocr(image)
        texts = cast(Sequence[str] | None, getattr(result, "txts", None))
        if texts is None:
            return ()
        scores = cast(Sequence[float], result.scores)
        boxes = cast(Sequence[Sequence[Sequence[float]]], result.boxes)
        offset_x, offset_y, _, _ = rectangle
        output: list[OcrCandidate] = []
        for box, raw, score in zip(boxes, texts, scores, strict=True):
            xs = [point[0] / scale + offset_x for point in box]
            ys = [point[1] / scale + offset_y for point in box]
            output.append(
                OcrCandidate(
                    raw=raw,
                    confidence=float(score),
                    box=(
                        round(min(xs)),
                        round(min(ys)),
                        round(max(xs)),
                        round(max(ys)),
                    ),
                )
            )
        return tuple(output)

    def _moves(
        self,
        image: Any,
        warnings: list[str],
    ) -> tuple[tuple[OcrCandidate, ...], tuple[Any, ...], Any]:
        crop = self._crop(image, MOVE_ANCHOR_RECT)
        prepared = self._cv2.resize(
            self._sharpen(crop),
            None,
            fx=2,
            fy=2,
            interpolation=self._cv2.INTER_CUBIC,
        )
        anchors = self._run_ocr(prepared, MOVE_ANCHOR_RECT, 2.0)
        anchor = next(
            (
                item
                for item in anchors
                if "道館" in normalize_ocr_text(item.raw)
                or "團體戰" in normalize_ocr_text(item.raw)
            ),
            None,
        )
        if anchor is None:
            warnings.append("Move section anchor was not found; moves are null.")
            return (), (), prepared

        section = (
            130,
            anchor.box[1] + 90,
            900,
            min(HEIGHT, anchor.box[1] + 540),
        )
        section_crop = self._crop(image, section)
        section_prepared = self._cv2.resize(
            self._sharpen(section_crop),
            None,
            fx=2,
            fy=2,
            interpolation=self._cv2.INTER_CUBIC,
        )
        candidates = extract_move_candidates(
            self._run_ocr(section_prepared, section, 2.0)
        )
        debug = tuple(self._candidate_crop(image, item) for item in candidates)
        return candidates, debug, section_prepared

    def _ivs(
        self,
        image: Any,
    ) -> tuple[
        tuple[BarDetection, BarDetection, BarDetection] | None,
        Any,
        Any,
    ]:
        roi = image[BAR_TOP:BAR_BOTTOM, BAR_START_X : BAR_END_X + 1]
        counts = self._np.any(roi < 245, axis=2).sum(axis=1)
        intervals = self._intervals(counts > 350)
        rows = self._bar_sequence(intervals)
        if rows is None:
            fallback = image[BAR_TOP:BAR_BOTTOM, 120:710].copy()
            return None, fallback, fallback.copy()

        hsv = self._cv2.cvtColor(image, self._cv2.COLOR_BGR2HSV)
        detections: list[BarDetection] = []
        for y_start, y_end in rows:
            bar = hsv[y_start : y_end + 1, BAR_START_X : BAR_END_X + 1]
            hue, saturation, brightness = (
                bar[:, :, 0],
                bar[:, :, 1],
                bar[:, :, 2],
            )
            orange = (
                (hue >= 8)
                & (hue <= 30)
                & (saturation >= 50)
                & (brightness >= 150)
            )
            red = (
                ((hue >= 170) | (hue <= 5))
                & (saturation >= 40)
                & (brightness >= 150)
            )
            xs = self._np.where(orange | red)[1]
            endpoint = None if xs.size == 0 else BAR_START_X + int(xs.max())
            detections.append(
                BarDetection(
                    y_start=y_start,
                    y_end=y_end,
                    endpoint=endpoint,
                    value=0 if endpoint is None else quantize_iv_endpoint(endpoint),
                )
            )

        top, bottom, left, right = rows[0][0] - 50, rows[2][1] + 50, 120, 710
        raw_debug = image[top:bottom, left:right].copy()
        detected_debug = raw_debug.copy()
        for label, item in zip(("attack", "defense", "hp"), detections, strict=True):
            center_y = (item.y_start + item.y_end) // 2 - top
            start_x, end_x = BAR_START_X - left, BAR_END_X - left
            endpoint_x = start_x if item.endpoint is None else item.endpoint - left
            self._cv2.rectangle(
                detected_debug,
                (start_x, item.y_start - top),
                (end_x, item.y_end - top),
                (255, 0, 0),
                2,
            )
            self._cv2.circle(detected_debug, (start_x, center_y), 6, (0, 255, 0), -1)
            self._cv2.circle(detected_debug, (end_x, center_y), 6, (255, 0, 0), -1)
            self._cv2.circle(detected_debug, (endpoint_x, center_y), 7, (0, 0, 255), -1)
            self._cv2.putText(
                detected_debug,
                f"{label}={item.value}",
                (start_x, max(20, item.y_start - top - 8)),
                self._cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 0, 0),
                2,
                self._cv2.LINE_AA,
            )
        typed = cast(
            tuple[BarDetection, BarDetection, BarDetection],
            tuple(detections),
        )
        return typed, raw_debug, detected_debug

    @staticmethod
    def _intervals(active_rows: Any) -> tuple[tuple[int, int], ...]:
        output: list[tuple[int, int]] = []
        start: int | None = None
        for offset, active in enumerate(active_rows):
            if bool(active) and start is None:
                start = offset
            elif not bool(active) and start is not None:
                if 20 <= offset - start <= 40:
                    output.append((BAR_TOP + start, BAR_TOP + offset - 1))
                start = None
        return tuple(output)

    @staticmethod
    def _bar_sequence(
        intervals: Sequence[tuple[int, int]],
    ) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None:
        for index in range(len(intervals) - 2):
            rows = intervals[index : index + 3]
            gaps = (rows[1][0] - rows[0][0], rows[2][0] - rows[1][0])
            if all(125 <= gap <= 150 for gap in gaps):
                return rows[0], rows[1], rows[2]
        return None

    @staticmethod
    def _best_text(candidates: Sequence[OcrCandidate]) -> OcrCandidate | None:
        return max(candidates, key=lambda item: item.confidence, default=None)

    @staticmethod
    def _best_cp(candidates: Sequence[OcrCandidate]) -> OcrCandidate | None:
        usable = [
            item for item in candidates if normalize_cp_candidate(item.raw) is not None
        ]
        return max(usable, key=lambda item: item.confidence, default=None)

    @staticmethod
    def _text(
        field: str,
        candidate: OcrCandidate | None,
        warnings: list[str],
        *,
        nullable: bool = False,
    ) -> RecognizedText:
        if candidate is None:
            if not nullable:
                warnings.append(f"{field} OCR returned no value.")
            return RecognizedText(value=None, raw=None, confidence=None)
        if candidate.confidence < LOW_CONFIDENCE:
            warnings.append(f"{field} OCR confidence is low ({candidate.confidence:.3f}).")
        return RecognizedText(
            value=normalize_ocr_text(candidate.raw),
            raw=candidate.raw,
            confidence=candidate.confidence,
        )

    @staticmethod
    def _cp(
        candidate: OcrCandidate | None,
        warnings: list[str],
    ) -> RecognizedInteger:
        if candidate is None:
            warnings.append("cp OCR returned no value.")
            return RecognizedInteger(value=None, raw=None, confidence=None)
        normalized = normalize_cp_candidate(candidate.raw)
        if normalized is None:
            warnings.append("cp OCR returned no value with a reliable CP prefix.")
            return RecognizedInteger(
                value=None,
                raw=candidate.raw,
                confidence=candidate.confidence,
            )
        if candidate.confidence < LOW_CONFIDENCE:
            warnings.append(f"cp OCR confidence is low ({candidate.confidence:.3f}).")
        return RecognizedInteger(
            value=int(normalized[2:]),
            raw=candidate.raw,
            confidence=candidate.confidence,
        )

    def _candidate_crop(self, image: Any, candidate: OcrCandidate) -> Any:
        x1, y1, x2, y2 = candidate.box
        crop = self._crop(
            image,
            (
                max(0, x1 - 30),
                max(0, y1 - 20),
                min(WIDTH, x2 + 30),
                min(HEIGHT, y2 + 20),
            ),
        )
        return self._cv2.resize(
            self._sharpen(crop),
            None,
            fx=2,
            fy=2,
            interpolation=self._cv2.INTER_CUBIC,
        )

    @staticmethod
    def _crop(image: Any, rectangle: tuple[int, int, int, int]) -> Any:
        x1, y1, x2, y2 = rectangle
        return image[y1:y2, x1:x2].copy()

    def _sharpen(self, image: Any) -> Any:
        kernel = self._np.array(
            ((0, -1, 0), (-1, 5, -1), (0, -1, 0)),
            dtype=self._np.float32,
        )
        return self._cv2.filter2D(image, -1, kernel)

    def _write(self, path: Path, image: Any) -> None:
        if not self._cv2.imwrite(str(path), image):
            raise RecognitionError(f"Could not write debug image '{path}'.")


def discover_complete_scans(root: Path) -> tuple[Path, ...]:
    """Select complete scans directly from their manifests."""

    root = root.expanduser().resolve()
    if not root.is_dir():
        raise RecognitionError(f"Scan root does not exist: '{root}'.")
    output: list[Path] = []
    for path in sorted(root.rglob("manifest.json")):
        try:
            manifest = ScanManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError):
            continue
        if manifest.scan_status == "complete":
            output.append(path.parent.resolve())
    return tuple(output)


def write_inventory_csv(
    path: Path,
    results: Sequence[RecognitionResult],
) -> Path:
    """Write one flat CSV row per recognition result."""

    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for result in results:
        writer.writerow(
            {
                "scan_id": result.scan_id,
                "pokemon_name": result.pokemon_name.value or "",
                "cp": result.cp.value if result.cp.value is not None else "",
                "fast_move": result.fast_move.value or "",
                "charged_move_1": result.charged_move_1.value or "",
                "charged_move_2": result.charged_move_2.value or "",
                "attack_iv": result.attack_iv if result.attack_iv is not None else "",
                "defense_iv": result.defense_iv if result.defense_iv is not None else "",
                "hp_iv": result.hp_iv if result.hp_iv is not None else "",
                "warnings": " | ".join(result.warnings),
            }
        )
    atomic_write_text(destination, buffer.getvalue())
    return destination
