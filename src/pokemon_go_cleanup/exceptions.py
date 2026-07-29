"""Domain-specific errors with stable CLI exit codes."""

from __future__ import annotations

from pathlib import Path


class PokemonGoCleanupError(Exception):
    """Base class for expected, user-facing failures."""

    exit_code = 1


class AdbNotInstalledError(PokemonGoCleanupError):
    """Raised when no ADB executable can be found."""

    exit_code = 2

    def __init__(self) -> None:
        super().__init__(
            "ADB is not installed or is not on PATH. Install Android SDK Platform-Tools "
            "and reopen the terminal."
        )


class NoConnectedDeviceError(PokemonGoCleanupError):
    """Raised when ADB reports no usable devices."""

    exit_code = 3

    def __init__(self) -> None:
        super().__init__(
            "No connected ADB device was found. Connect the phone, enable USB debugging, "
            "and run 'adb devices'."
        )


class UnauthorizedDeviceError(PokemonGoCleanupError):
    """Raised when one or more devices have not authorized this computer."""

    exit_code = 4

    def __init__(self, serial_numbers: list[str]) -> None:
        serials = ", ".join(serial_numbers)
        super().__init__(
            f"ADB device authorization is required for: {serials}. Unlock the phone and "
            "accept the USB debugging fingerprint prompt."
        )


class MultipleConnectedDevicesError(PokemonGoCleanupError):
    """Raised when device selection would be ambiguous."""

    exit_code = 5

    def __init__(self, serial_numbers: list[str]) -> None:
        serials = ", ".join(serial_numbers)
        super().__init__(
            f"Multiple connected devices are ready: {serials}. Select one with '--serial SERIAL'."
        )


class ScreenshotCaptureError(PokemonGoCleanupError):
    """Raised when ADB does not return a valid PNG screenshot."""

    exit_code = 6


class AdbCommandError(PokemonGoCleanupError):
    """Raised when an ADB subprocess exits unsuccessfully."""

    exit_code = 7


class DeviceNotFoundError(PokemonGoCleanupError):
    """Raised when a requested serial number is absent."""

    exit_code = 8

    def __init__(self, serial_number: str) -> None:
        super().__init__(f"ADB device '{serial_number}' was not found.")


class DeviceUnavailableError(PokemonGoCleanupError):
    """Raised when a requested device exists but is not ready."""

    exit_code = 9

    def __init__(self, serial_number: str, state: str) -> None:
        super().__init__(f"ADB device '{serial_number}' is not ready (state: {state}).")


class DeviceProtocolError(PokemonGoCleanupError):
    """Raised when ADB returns an unexpected response."""

    exit_code = 10


class LocalStorageError(PokemonGoCleanupError):
    """Raised when a local screenshot or metadata file cannot be saved."""

    exit_code = 11


class DatasetError(PokemonGoCleanupError):
    """Raised when a dataset root or scan cannot be read safely."""

    exit_code = 12


class AnnotationError(PokemonGoCleanupError):
    """Raised when ground-truth input or persistence is invalid."""

    exit_code = 13


class AnnotationExistsError(AnnotationError):
    """Raised when an annotation would be overwritten without permission."""

    def __init__(self, annotation_path: Path) -> None:
        super().__init__(
            f"Annotation already exists at '{annotation_path}'. "
            "Confirm replacement interactively or pass --force."
        )


class RecognitionError(PokemonGoCleanupError):
    """Raised when the calibrated screenshot reader cannot continue."""

    exit_code = 14


class AutomationError(PokemonGoCleanupError):
    """Raised when the fixed Huawei automation cannot proceed safely."""

    exit_code = 15


class BatchAutomationError(PokemonGoCleanupError):
    """Raised when bounded batch scanning cannot continue safely."""

    exit_code = 16


class GuidedScanStepError(PokemonGoCleanupError):
    """Report the failed guided step while retaining the underlying exit code."""

    def __init__(
        self,
        *,
        failed_step: str,
        manifest_path: Path,
        cause: PokemonGoCleanupError,
        manifest_error: LocalStorageError | None = None,
    ) -> None:
        self.exit_code = cause.exit_code
        self.failed_step = failed_step
        self.manifest_path = manifest_path
        self.cause = cause
        self.manifest_error = manifest_error

        preservation = "Any successful screenshots were preserved."
        if manifest_error is None:
            manifest_detail = f"The manifest was marked incomplete at '{manifest_path}'."
        else:
            manifest_detail = (
                f"The manifest at '{manifest_path}' could not be marked incomplete: "
                f"{manifest_error}"
            )
        super().__init__(
            f"Guided scan failed at step '{failed_step}': {cause} {preservation} {manifest_detail}"
        )
