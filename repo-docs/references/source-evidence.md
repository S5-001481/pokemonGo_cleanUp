# Source evidence for local capture and dataset paths

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
PNG cases. Eight scan groups produced on the target Huawei Mate 30 were later
fully decoded by the dataset validator; that confirms the guided device path,
but not every possible native Windows ADB installation.

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

## Dataset and annotation extension

`dataset validate` recursively finds scan-shaped directories, fully decodes
present PNGs, compares their dimensions, and parses manifests and annotations
through strict Pydantic models. Missing artifacts remain distinguishable from
corrupt or malformed artifacts. `dataset status` exposes the same model without
turning an invalid scan into a command failure.

`annotate` prints all screenshot paths and collects manual ground truth without
OCR or device commands. Existing work requires confirmation or `--force`;
non-interactive JSON input is typed before a same-directory atomic replacement.

## Fixed automatic scan extension

`scan-auto-one` is a single-run Huawei Mate 30 state machine. The fixed profile
requires 1440x3120 and checks a fresh expected state before every input. The one
moves swipe and later appraisal inputs use stability waits; menu opening instead
polls OCR for the explicit `action_menu` target every 500 ms for up to 10 seconds,
with one detail-state-confirmed retry. Dry-run records the initial state and
coordinate plan without calling tap, swipe, or BACK.

The appraisal row has no guessed coordinate: OCR must reliably locate
`調查寶可夢`, and the derived center must remain outside the transfer band and
away from an OCR `傳送` center. Existing OpenCV IV geometry confirms the bars
before appraisal capture. Failures preserve final screenshots and record the
current automation step in an incomplete manifest. A later real Mate 30 run
completed all three captures, appraisal progression, safe exit, recognition, and
a complete manifest.

## Bounded batch extension

`scan-batch` delegates each item to that unchanged one-scan service. Its only new
input is a centralized pair of fixed left swipes, each permitted only after a
fresh `detail_summary` matches the completed recognition. The next summary must
change name, CP, or static fingerprint; polling lasts 15 seconds per attempt and
allows one normal left swipe plus one stronger left retry, never a right swipe.
Each complete result atomically rewrites the CSV before switching. Resume accepts
only rows whose referenced manifests remain complete.

## Evidence Traversal Log

| Pass | Purpose | Inspected evidence | What changed in the model |
| --- | --- | --- | --- |
| Pass 1 | Map the main path | [`pyproject.toml`](../../pyproject.toml), [`cli.py`](../../src/pokemon_go_cleanup/cli.py), [`adb.py`](../../src/pokemon_go_cleanup/adb.py), [`capture.py`](../../src/pokemon_go_cleanup/capture.py), [`models.py`](../../src/pokemon_go_cleanup/models.py) | Confirmed the command-to-device-to-PNG-to-JSON handoffs and the dated output location. |
| Pass 2 | Challenge the happy path | [`exceptions.py`](../../src/pokemon_go_cleanup/exceptions.py), [`config.py`](../../src/pokemon_go_cleanup/config.py), [`logging_config.py`](../../src/pokemon_go_cleanup/logging_config.py), [`test_adb.py`](../../tests/test_adb.py), [`test_capture.py`](../../tests/test_capture.py), [`test_cli.py`](../../tests/test_cli.py) | Added the state-selection failures, PNG trust check, Unicode path coverage, structured error logging, exact exit codes, and the non-atomic pair caveat. |
| Pass 3 | Trace the guided lifecycle | [`adb_runner.py`](../../src/pokemon_go_cleanup/adb_runner.py), [`scan.py`](../../src/pokemon_go_cleanup/scan.py), [`guided_prompts.py`](../../src/pokemon_go_cleanup/guided_prompts.py), [`test_scan.py`](../../tests/test_scan.py), [`test_scan_cli.py`](../../tests/test_scan_cli.py) | Added the three user handoffs, atomic step files, progressive manifest, incomplete recovery, option coverage, and no-tap/swipe command evidence. |
| Pass 5 | Trace the fixed automatic safety boundary | [`automation.py`](../../src/pokemon_go_cleanup/automation.py), input wrappers in [`adb.py`](../../src/pokemon_go_cleanup/adb.py), [`recognition.py`](../../src/pokemon_go_cleanup/recognition.py), [`test_automation.py`](../../tests/test_automation.py), and [`test_adb.py`](../../tests/test_adb.py) | Added six page states, centralized coordinates, dry-run zero-input evidence, one moves scroll, no-scroll menu opening with OCR target-state polling and one gated retry, transfer clearance, one appraisal-dialogue tap followed by 500 ms/30-second IV-bar polling with no retry, IV confirmation, a center tap to exit appraisal, timeouts, and incomplete recovery. |
| Pass 4 | Trace dataset trust and annotation replacement | [`dataset.py`](../../src/pokemon_go_cleanup/dataset.py), [`annotation.py`](../../src/pokemon_go_cleanup/annotation.py), [`storage.py`](../../src/pokemon_go_cleanup/storage.py), [`test_dataset.py`](../../tests/test_dataset.py), [`test_dataset_cli.py`](../../tests/test_dataset_cli.py), [`test_annotation.py`](../../tests/test_annotation.py) | Added recursive discovery, full image decoding, complete/incomplete/invalid classification, typed ground truth, UTF-8 persistence, overwrite protection, and runtime-only synthetic image evidence. |
| Pass 6 | Trace bounded batch durability and switching | [`batch.py`](../../src/pokemon_go_cleanup/batch.py), unchanged [`automation.py`](../../src/pokemon_go_cleanup/automation.py), CLI wiring in [`cli.py`](../../src/pokemon_go_cleanup/cli.py), atomic persistence in [`storage.py`](../../src/pokemon_go_cleanup/storage.py), and [`test_batch.py`](../../tests/test_batch.py) | Added a fixed Huawei horizontal gesture, post-exit summary gate, name/CP change and wrap checks, two-attempt ceiling, per-row atomic CSV, complete-manifest resume checks, and switch debug evidence. |

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
| Guided screenshot and manifest writes use same-directory temporary files and atomic replacement. | [`storage.py`](../../src/pokemon_go_cleanup/storage.py) flushes, `fsync`s, replaces, and cleans temporary paths; `test_atomic_screenshot_write_removes_temporary_file_on_failure` exercises cleanup through the scan wrapper. | Confirmed | Atomic replacement follows the guarantees of the local filesystem. | Guide README, guided walkthrough |
| A midway guided failure preserves earlier PNGs and marks the manifest incomplete with the failed step. | [`_raise_incomplete()`](../../src/pokemon_go_cleanup/scan.py) and [`GuidedScanStepError`](../../src/pokemon_go_cleanup/exceptions.py) implement recovery; `test_guided_scan_preserves_success_and_marks_midway_failure` verifies a moves failure. | Confirmed | If the manifest itself cannot be rewritten, the CLI explicitly reports that second failure. | Guided walkthrough |
| Missing `--guided` always reports that exact option and exits 2. | [`scan_one()`](../../src/pokemon_go_cleanup/cli.py) emits a fixed plain-text error before exiting; [`test_scan_cli.py`](../../tests/test_scan_cli.py) asserts both the text and code. | Confirmed | This deliberately avoids Rich usage wrapping and terminal-width truncation. | CI, guided walkthrough |
| Dataset validation recursively finds candidates, fully decodes PNGs, and requires equal dimensions. | [`dataset.py`](../../src/pokemon_go_cleanup/dataset.py) implements discovery and Pillow loading; [`test_dataset.py`](../../tests/test_dataset.py) covers nested, corrupt, and mismatched cases. | Confirmed | Candidate detection is artifact- or dated-directory-based. | Dataset walkthrough, root READMEs |
| Dataset status separates complete, incomplete, and invalid scans. | [`validate_scan_directory()`](../../src/pokemon_go_cleanup/dataset.py) classifies absence/lifecycle separately from corrupt present data; [`test_dataset_cli.py`](../../tests/test_dataset_cli.py) verifies the table and validate-only nonzero exit. | Confirmed | A malformed existing annotation makes an otherwise complete scan invalid. | Dataset walkthrough |
| Ground truth is typed, readable UTF-8, atomic, and overwrite-protected. | [`GroundTruth`](../../src/pokemon_go_cleanup/models.py) defines all fields and numeric/IV rules; [`AnnotationService`](../../src/pokemon_go_cleanup/annotation.py) validates input and writes atomically; [`test_annotation.py`](../../tests/test_annotation.py) covers Unicode, Enter-to-keep, force, refusal, and cleanup. | Confirmed | `--input-json` requires `--force` when a destination already exists. | Dataset walkthrough, root READMEs |
| Tests contain no real scan media. | [`test_dataset.py`](../../tests/test_dataset.py), [`test_dataset_cli.py`](../../tests/test_dataset_cli.py), and [`test_annotation.py`](../../tests/test_annotation.py) create synthetic solid-color PNGs under pytest temporary paths at runtime; ignored `data/` remains outside Git. | Confirmed | Local real data is still processed when the user runs the commands. | Privacy boundary |
| Expected failures have stable CLI exit codes and structured error logs. | [`exceptions.py`](../../src/pokemon_go_cleanup/exceptions.py) defines codes 2–16; [`_abort()`](../../src/pokemon_go_cleanup/cli.py) logs error type/code and exits; tests exercise command-specific failures. | Confirmed | Ctrl+C from automatic or batch scanning uses exit 130 after preserving completed local state. | Guide README |
| A bright CP background cannot by itself invalidate a real detail summary. | [`HuaweiMate30PageDetector`](../../src/pokemon_go_cleanup/automation.py) accepts name+CP or name+fixed-ROI HP, records normalized `cp`, `hp`, and `summary_evidence`, and the initial automatic scan polls without input for 15 seconds before using its last name+HP screenshot. [`RecognitionService`](../../src/pokemon_go_cleanup/recognition.py) preserves the existing CP OCR variants, adds six bright-background preprocessing variants, and selects only the highest-confidence `CP\s*\d{1,5}` candidate. | Confirmed in source | No test, linter, type check, validator, or real-device run was performed for this change at the user's request. | Automatic walkthrough |
| A Huawei Mate 30 running HarmonyOS accepts the guided capture sequence. | The user reported the working physical workflow; the [project context](../../docs/codex-context.md) records the 2026-07-28 read-only validation of eight complete groups with 24 decodable PNGs and eight valid manifests. | Confirmed | This confirms the target guided path, not every native Windows ADB installation or standalone capture setup. | Guide README, walkthroughs |
| Dry-run cannot send ADB input, and live inputs are gated by expected states. | [`AutoScanService`](../../src/pokemon_go_cleanup/automation.py) separates the dry-run return before every input helper; [`test_automation.py`](../../tests/test_automation.py) asserts zero inputs for dry-run/unknown start, the five normal live inputs, no reverse scroll, and the two-attempt menu ceiling. | Confirmed | Fake ADB proves all branches; the first menu-tap path also succeeded on the connected device. | Automatic walkthrough |
| Appraisal requires OCR `調查寶可夢` and IV bars. | [`HuaweiMate30PageDetector`](../../src/pokemon_go_cleanup/automation.py) derives and safety-checks the menu target and reuses the IV detector; an existing real trio returned `detail_summary`, `detail_moves`, and `appraisal_bars` with IV 15/15/15. A later ignored real run completed menu OCR, dialogue, IV capture, safe exit, recognition, and a complete manifest. | Confirmed | This evidence is specific to the current Mate 30 layout and Traditional Chinese UI. | Automatic walkthrough |
| A clipped move-section heading is not required for `detail_moves`. | [`HuaweiMate30PageDetector`](../../src/pokemon_go_cleanup/automation.py) first reuses existing move extraction, then pairs a confident Chinese move name with a 1–3 digit right-side damage value on the same fixed Mate 30 row; the heading adds confidence only. Focused synthetic tests reject appraisal labels, and saved real move/appraisal screenshots verify the positive and negative boundary. | Confirmed | This is intentionally calibrated only for the current 1440x3120 Traditional Chinese layout. | Automatic walkthrough |
| Move scrolling is target-state-driven rather than whole-screen-stability-driven. | [`AutoScanService._scroll_to_moves()`](../../src/pokemon_go_cleanup/automation.py) polls every 500 ms for `detail_moves`, permits transient summary/unknown, stops on menu/appraisal states, and retries only once after a summary timeout; [`test_automation.py`](../../tests/test_automation.py) fails if stability is requested before moves and covers summary-only retry plus unknown no-retry. | Confirmed | Later menu/appraisal inputs retain their existing stability waits. A live 熔蟻獸 run reached moves on poll 1 at 0.99701 with no scroll stability file. | Automatic walkthrough |
| Resume and append reject only an adjacent static-summary repeat. | [`BatchScanService`](../../src/pokemon_go_cleanup/batch.py) compares `(100,1550,1340,2300)` hashes plus name/CP/optional HP against only the final CSV row, pre-switches when resume is still on that row, and repeats the guard before atomic append; focused batch tests cover same-current, already-next, animated upper screen, and refused append. | Confirmed | This does not globally deduplicate the history; different adjacent individuals may proceed when their static details differ. A live already-next resume measured distance 60, sent no preliminary switch, and preserved the two-row CSV after a later appraisal failure. | Batch walkthrough |
| Batch CSV is durable before switching, and switching is bounded by identity checks. | [`BatchScanService`](../../src/pokemon_go_cleanup/batch.py) appends through `atomic_write_text` immediately after a complete one-scan result, then requires a matching post-exit summary before exactly two configured left gestures: `(1180,1500)` to `(260,1500)` over 600 ms, then `(1300,1500)` to `(140,1500)` over 850 ms; [`test_batch.py`](../../tests/test_batch.py) asserts both gestures have `start.x > end.x` for normal and resume switching. | Confirmed | The direction is operator-confirmed for the fixed Mate 30 profile; no right-swipe fallback is implemented. | Batch walkthrough |
| Transfer, gameplay mutations, account access, private APIs, traffic inspection, and credentials are outside the implementation. | Batch adds only one fixed horizontal swipe between complete single scans; the existing bounded menu/appraisal inputs remain unchanged. Root [`README.md`](../../README.md) states the boundary. | Confirmed | Any future input expansion requires a new safety and privacy review. | Guide README |

## Coverage and exclusions

The guide now traces standalone `capture`, guided `scan-one`, fixed
`scan-auto-one`, bounded `scan-batch`, calibrated reading, dataset validation,
and manual annotation.
`device list` and `device info` remain adjacent entry paths. Packaging, logging,
configuration precedence, and CI were checked where they constrain these paths.
No real device media is stored in the repository.

The guide does not cover transfer or other gameplay mutations, network inspection,
account access, or credentials. One-Pokémon automation, batch row durability, and right switching are physically
confirmed. The fixed detail-moves fallback is real-image-confirmed, and the
resumed two-complete-row device run finished with two distinct identities.

## Falsifying checks

- `pytest` would invalidate the documented device-state, PNG, manifest,
  annotation, temporary-file cleanup, classification, or failure-recovery
  behavior if those branches changed.
- `ruff check .` and `mypy` catch code-quality and type-contract drift but do not
  prove ADB integration.
- The CI matrix checks Ubuntu and Windows on Python 3.12 and 3.13. Physical ADB
  behavior still depends on the host installation, authorization state, vendor
  `wm size` response, and captured bytes.

Evidence status: Confirmed unless noted.
