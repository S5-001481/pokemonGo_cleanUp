"""Atomic local-file persistence shared by capture workflows."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

from pokemon_go_cleanup.exceptions import LocalStorageError

logger = logging.getLogger(__name__)


def _cleanup_temporary_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning(
            "temporary_file_cleanup_failed",
            extra={"temporary_path": str(path), "error": str(error)},
        )


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write bytes beside the destination, then atomically replace it."""

    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary_path.open("xb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(path)
    except OSError as error:
        raise LocalStorageError(f"Could not atomically save '{path}': {error}") from error
    finally:
        _cleanup_temporary_file(temporary_path)


def atomic_write_text(path: Path, content: str) -> None:
    """Write UTF-8 text beside the destination, then atomically replace it."""

    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary_path.open(
            "x",
            encoding="utf-8",
            newline="\n",
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(path)
    except OSError as error:
        raise LocalStorageError(f"Could not atomically save '{path}': {error}") from error
    finally:
        _cleanup_temporary_file(temporary_path)
