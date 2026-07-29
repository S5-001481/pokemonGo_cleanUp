"""Manual and JSON-backed ground-truth annotation persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import ValidationError

from pokemon_go_cleanup.dataset import ANNOTATION_FILENAME
from pokemon_go_cleanup.exceptions import AnnotationError, AnnotationExistsError
from pokemon_go_cleanup.models import GroundTruth, ScanStep
from pokemon_go_cleanup.scan import SCAN_STEPS
from pokemon_go_cleanup.storage import atomic_write_text

SCREENSHOT_PATHS: Final[tuple[tuple[ScanStep, str], ...]] = tuple(
    (step, f"{step}.png") for step in SCAN_STEPS
)


class AnnotationService:
    """Load, validate, and atomically persist one scan annotation."""

    @staticmethod
    def resolve_screenshot_paths(scan_directory: Path) -> dict[ScanStep, Path]:
        directory = scan_directory.expanduser().resolve()
        if not directory.is_dir():
            raise AnnotationError(f"Scan directory does not exist: '{directory}'.")

        paths: dict[ScanStep, Path] = {
            step: (directory / filename).resolve()
            for step, filename in SCREENSHOT_PATHS
        }
        missing = [path.name for path in paths.values() if not path.is_file()]
        if missing:
            raise AnnotationError(
                f"Scan directory is missing required screenshots: {', '.join(missing)}."
            )
        return paths

    @staticmethod
    def load_existing(scan_directory: Path) -> GroundTruth | None:
        annotation_path = scan_directory.expanduser().resolve() / ANNOTATION_FILENAME
        if not annotation_path.exists():
            return None
        return AnnotationService._load_path(annotation_path)

    @staticmethod
    def load_json_input(input_path: Path) -> GroundTruth:
        return AnnotationService._load_path(input_path.expanduser().resolve())

    @staticmethod
    def _load_path(path: Path) -> GroundTruth:
        try:
            return GroundTruth.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise AnnotationError(
                f"Could not read annotation JSON '{path}': {error}"
            ) from error
        except ValidationError as error:
            raise AnnotationError(f"Invalid annotation JSON '{path}': {error}") from error

    @staticmethod
    def save(
        scan_directory: Path,
        annotation: GroundTruth,
        *,
        allow_overwrite: bool = False,
    ) -> Path:
        directory = scan_directory.expanduser().resolve()
        destination = directory / ANNOTATION_FILENAME
        if destination.exists() and not allow_overwrite:
            raise AnnotationExistsError(destination)
        atomic_write_text(
            destination,
            annotation.model_dump_json(indent=2) + "\n",
        )
        return destination
