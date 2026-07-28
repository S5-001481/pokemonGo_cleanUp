# Source evidence for local capture paths

> This is lookup material. Read the walkthrough first if you do not yet
> understand the behavior.

## Understanding brief

The representative behavior is `pokemon-go-cleanup capture`. CLI options and
environment-backed configuration enter first. A successful run produces one PNG
and one JSON sidecar below `data/screenshots/YYYY-MM-DD/`.

The hard part is the trust boundary around ADB. The command may be missing,
several devices may be attached, a phone may be unauthorized, or the screenshot
subprocess may return an error or non-PNG bytes. The code resolves those states
before it writes a capture.

The strongest boundary checks are the selection branches in `select_device()` and
the PNG signature check in `capture_screen()`. The tests falsify the happy-path
model with missing, unauthorized, multiple-device, command-failure, and invalid
PNG cases. A physical Windows-to-Huawei capture would falsify or confirm the
remaining device-integration assumption.

A careful newcomer will likely ask whether the JSON and PNG are always written as
an atomic pair. The current answer is no: a storage failure during the second
write can leave the PNG without its JSON sidecar. The CLI reports that failure as
exit code 11.

## Guided scan extension

The newer `pokemon-go-cleanup scan-one --guided` path groups `summary.png`,
`moves.png`, and `appraisal.png` under one dated scan ID from exactly one device.
It writes `manifest.json` before prompting and refreshes it after each success.

Its hard boundary is a midway failure: earlier screenshots must remain, temporary
files must be cleaned, and the manifest must become `incomplete` with the exact
failed step. Fake-runner integration tests exercise the complete ADB command path
without a physical phone or UI automation.


## Evidence Traversal Log

| Pass | Purpose | Inspected evidence | What changed in the model |
| --- | --- | --- | --- |
| Pass 1 | Map the main path | [`pyproject.toml`](../../pyproject.toml), [`cli.py`](../../src/pokemon_go_cleanup/cli.py), [`adb.py`](../../src/pokemon_go_cleanup/adb.py), [`capture.py`](../../src/pokemon_go_cleanup/capture.py), [`models.py`](../../src/pokemon_go_cleanup/models.py) | Confirmed the command-to-device-to-PNG-to-JSON handoffs and the dated output location. |
| Pass 2 | Challenge the happy path | [`exceptions.py`](../../src/pokemon_go_cleanup/exceptions.py), [`config.py`](../../src/pokemon_go_cleanup/config.py), [`logging_config.py`](../../src/pokemon_go_cleanup/logging_config.py), [`test_adb.py`](../../tests/test_adb.py), [`test_capture.py`](../../tests/test_capture.py), [`test_cli.py`](../../tests/test_cli.py) | Added the state-selection failures, PNG trust check, Unicode path coverage, structured error logging, exact exit codes, and the non-atomic pair caveat. |
| Pass 3 | Trace the guided lifecycle | [`adb_runner.py`](../../src/pokemon_go_cleanup/adb_runner.py), [`scan.py`](../../src/pokemon_go_cleanup/scan.py), [`guided_prompts.py`](../../src/pokemon_go_cleanup/guided_prompts.py), [`test_scan.py`](../../tests/test_scan.py), [`test_scan_cli.py`](../../tests/test_scan_cli.py) | Added the three user handoffs, atomic step files, progressive manifest, incomplete recovery, option coverage, and no-tap/swipe command evidence. |

## Claim ledger

| Claim | Evidence | Confidence | Caveat | Used by |
| --- | --- | --- | --- | --- |
| The installed entrypoint is `pokemon-go-cleanup`. | [`[project.scripts]`](../../pyproject.toml) maps the command to `pokemon_go_cleanup.cli:app`; the installed `--help` smoke check returned the command tree. | Confirmed | The smoke check ran in WSL, not native Windows. | Guide README, walkthrough |
| Runtime configuration is validated by Pydantic and can come from `POKEMON_GO_CLEANUP_*` environment variables. | [`AppConfig`](../../src/pokemon_go_cleanup/config.py) subclasses `BaseSettings`; [`main()`](../../src/pokemon_go_cleanup/cli.py) preserves environment/default values for omitted flags; [`test_config.py`](../../tests/test_config.py) covers both precedence paths. | Confirmed | Explicit CLI flags override matching environment values. | Walkthrough |
| ADB is discovered from an explicit path or `PATH`. | [`discover_adb()`](../../src/pokemon_go_cleanup/adb.py) checks a configured file/directory and then `shutil.which`. | Confirmed | A real Windows path has not yet been exercised in this workspace. | Walkthrough |
| An implicit capture requires exactly one ready device. | [`select_device()`](../../src/pokemon_go_cleanup/adb.py) rejects zero ready devices, unauthorized-only devices, and more than one ready device; [`test_adb.py`](../../tests/test_adb.py) covers each branch. | Confirmed | If exactly one ready device and additional unauthorized devices exist, the ready device is selected. | Walkthrough |
| The current logical resolution is the last size reported by `adb shell wm size`. | [`get_resolution()`](../../src/pokemon_go_cleanup/adb.py) selects the last `WIDTHxHEIGHT` match; `test_resolution_uses_current_override` covers physical plus override output. | Confirmed | An unexpected vendor-specific output becomes exit code 10. | Walkthrough |
| Screenshot bytes must start with the PNG signature before persistence. | [`capture_screen()`](../../src/pokemon_go_cleanup/adb.py) rejects empty and non-PNG output; two tests cover invalid bytes and ADB command failure. | Confirmed | The code validates the signature, not full PNG structure. | Guide README, walkthrough |
| Captures use a dated directory and Windows-safe generated filename. | [`CaptureService.capture()`](../../src/pokemon_go_cleanup/capture.py) combines local date, microseconds, sanitized serial, and random suffix; [`test_capture.py`](../../tests/test_capture.py) verifies the dated Unicode path and sanitized filename. | Confirmed | The data directory itself may contain Unicode; the serial component is reduced to ASCII-safe characters. | Walkthrough |
| The JSON sidecar records capture time, serial, resolution, both paths, byte size, and SHA-256. | [`CaptureMetadata`](../../src/pokemon_go_cleanup/models.py) defines the contract; `test_capture_saves_dated_png_and_utf8_json` verifies serialized values. | Confirmed | Paths are absolute paths as seen by the runtime that performed the capture. | Guide README, walkthrough |
| A guided scan stores three fixed PNG names and one manifest below `data/scans/YYYY-MM-DD/<scan_id>/`. | [`GuidedScanService`](../../src/pokemon_go_cleanup/scan.py) fixes the step order and paths; [`ScanManifest`](../../src/pokemon_go_cleanup/models.py) defines timestamps, filenames, device data, version, mode, notes, status, and failed step. | Confirmed | Incomplete scans contain only filenames and timestamps for successful steps. | Guided walkthrough, root README |
| Guided screenshot and manifest writes use same-directory temporary files and atomic replacement. | [`_atomic_write_bytes()` and `_atomic_write_text()`](../../src/pokemon_go_cleanup/scan.py) flush, `fsync`, replace, and clean temporary paths; `test_atomic_screenshot_write_removes_temporary_file_on_failure` exercises cleanup. | Confirmed | Atomic replacement follows the guarantees of the local filesystem. | Guide README, guided walkthrough |
| A midway guided failure preserves earlier PNGs and marks the manifest incomplete with the failed step. | [`_raise_incomplete()`](../../src/pokemon_go_cleanup/scan.py) and [`GuidedScanStepError`](../../src/pokemon_go_cleanup/exceptions.py) implement recovery; `test_guided_scan_preserves_success_and_marks_midway_failure` verifies a moves failure. | Confirmed | If the manifest itself cannot be rewritten, the CLI explicitly reports that second failure. | Guided walkthrough |
| Expected failures have stable CLI exit codes and structured error logs. | [`exceptions.py`](../../src/pokemon_go_cleanup/exceptions.py) defines codes 2–11; [`_abort()`](../../src/pokemon_go_cleanup/cli.py) logs error type/code and exits; the missing-ADB subprocess smoke check returned 2. | Confirmed | Unexpected programming errors remain ordinary failures. | Guide README |
| A Huawei Mate 30 running HarmonyOS will accept these capture sequences from native Windows. | The implementation uses standard ADB commands and the root README documents Huawei authorization; no physical-device artifact exists yet. | Unknown | Confirm with `device list`, `device info`, `capture`, and `scan-one --guided` on the target phone. | Guide README, walkthroughs |
| OCR, gameplay automation, account access, private APIs, traffic inspection, and credentials are outside the implementation. | All current source paths were inspected; the root [`README.md`](../../README.md) and [`SECURITY.md`](../../SECURITY.md) state the boundary. | Confirmed | Future behavior changes require a repo-docs sync and privacy review. | Guide README |

## Coverage and exclusions

The guide now traces both standalone `capture` and guided `scan-one` behavior.
`device list` and `device info` remain adjacent entry paths. Packaging, logging,
configuration precedence, and CI were checked where they constrain these paths.
No real device media is stored in the repository.

The guide does not trace OCR, gameplay automation, network inspection, account
access, or credential handling because the current project excludes them. It
also does not claim a successful physical Huawei run; that remains the main
integration check.

## Falsifying checks

- `pytest` would invalidate the documented device-state, PNG, manifest status,
  temporary-file cleanup, or failure-recovery behavior if those branches changed.
- `ruff check .` and `mypy` catch code-quality and type-contract drift but do not
  prove ADB integration.
- A native Windows run against the target Mate 30 is the decisive integration
  check. A different `wm size` response, authorization state, or corrupted PNG
  would require code and guide updates.

Evidence status: Confirmed unless noted.
