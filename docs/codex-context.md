# Codex project context

## Purpose

`pokemonGo_cleanUp` is an open-source, local-first CLI for capturing screenshots
from an ADB-connected Android or HarmonyOS phone. It supports standalone captures
and guided, automatic, or bounded batch three-view scans grouped by scan ID. It
also reads the current Huawei dataset with local OCR/OpenCV, validates local scan
datasets, and records typed manual ground truth. The
GitHub/display name
is `pokemonGo_cleanUp`, the Python import package is `pokemon_go_cleanup`, and the
console command is `pokemon-go-cleanup`.

## Current scope

- Target device: Huawei Mate 30 running an ADB-compatible HarmonyOS version.
- Detect ADB and enumerate device states.
- Select one ready device, optionally by serial number.
- Query the logical resolution with `adb shell wm size`.
- Capture the current display with `adb exec-out screencap -p`.
- Save a PNG and UTF-8 JSON sidecar below
  `data/screenshots/YYYY-MM-DD/`.
- Guide the user through summary, moves, and appraisal views without UI commands.
- Automatically scan one already-open Huawei Mate 30 detail page with page-state
  gates, fixed inputs, OCR appraisal targeting, stability waits, and IV confirmation.
- Batch that proven one-scan service with a bounded horizontal switch, name/CP
  change checks, wrap detection, per-row atomic CSV updates, and resume validation.
- Group `summary.png`, `moves.png`, `appraisal.png`, and `manifest.json` below
  `data/scans/YYYY-MM-DD/<scan_id>/`.
- Record progressive `in_progress`, `complete`, or `incomplete` scan status.
- Recursively discover and classify guided scans as complete, incomplete, or
  invalid by decoding PNGs and validating typed JSON.
- Create atomic UTF-8 `ground_truth.json` files interactively or from validated
  non-interactive JSON input.
- Emit structured JSON or text logs.

Explicitly excluded: transfer, power-up/evolution/rename/unlock/battle actions,
account access, private APIs,
credential handling, and network traffic inspection. No copyrighted Pokémon
media belongs in the repository.

## Architecture

- `src/pokemon_go_cleanup/adb_runner.py`: injectable subprocess command boundary.
- `src/pokemon_go_cleanup/adb.py`: parsing, state selection, resolution query, and
  PNG validation.
- `src/pokemon_go_cleanup/automation.py`: proven one-Pokémon state machine,
  fixed coordinates, state gates, move-row fallback, debug evidence, and incomplete
  recovery.
- `src/pokemon_go_cleanup/batch.py`: bounded horizontal switching, page identity,
  fingerprints, atomic CSV persistence, resume checks, and batch debug evidence.
- `src/pokemon_go_cleanup/recognition.py`: calibrated RapidOCR text extraction and
  OpenCV IV-bar geometry.
- `src/pokemon_go_cleanup/capture.py`: dated paths and local PNG/metadata writes.
- `src/pokemon_go_cleanup/scan.py`: scan IDs, atomic step writes, progressive
  manifest updates, and incomplete recovery.
- `src/pokemon_go_cleanup/storage.py`: shared atomic byte/text persistence.
- `src/pokemon_go_cleanup/dataset.py`: recursive discovery, Pillow decoding,
  typed JSON validation, and scan classification.
- `src/pokemon_go_cleanup/annotation.py`: annotation input, overwrite protection,
  and atomic UTF-8 persistence.
- `src/pokemon_go_cleanup/guided_prompts.py`: bilingual manual-step prompts.
- `src/pokemon_go_cleanup/models.py`: immutable device, capture, and scan models.
- `src/pokemon_go_cleanup/config.py`: environment/CLI-backed Pydantic settings.
- `src/pokemon_go_cleanup/cli.py`: Typer commands and user-facing errors.
- `tests/`: physical-device-free unit, CLI, and fake-runner integration tests.

Expected user failures have stable exit codes. Captures are excluded by
`.gitignore` through the whole `data/` tree and explicit `ground_truth.json` rule.

## Decisions

- Metadata is a JSON sidecar next to its PNG so a capture can be moved as a pair.
- Capture timestamps use the computer's timezone and include a UTC offset.
- Filenames use microseconds, a sanitized serial, and a random suffix to avoid
  accidental overwrite.
- Metadata records absolute paths, byte size, and SHA-256.
- `device list` is diagnostic and exits successfully even when the list is empty;
  selection commands report no-device as an error.
- Guided scans use fixed step filenames inside a unique scan directory.
- Screenshots and manifests use same-directory temporary files plus atomic replace.
- A failed guided step preserves earlier screenshots and marks the manifest incomplete.
- Automatic input is allowed only after the immediately preceding screenshot
  matches the expected state; after moves, either detail state may open the fixed
  menu without a reverse swipe, and `調查寶可夢` has no guessed fallback coordinate.
- Menu opening polls OCR every 500 ms for up to 10 seconds and permits one retry
  only after a fresh screenshot still confirms `detail_moves` or `detail_summary`.
- `--dry-run` captures/detects the initial summary and records the plan without
  calling tap, swipe, or BACK.
- Automatic scans become complete only after safe appraisal exit and recognition.
- Batch CSV is rewritten atomically after every complete scan; an existing CSV
  requires `--resume` and every referenced manifest must still be complete.
- Horizontal switching is allowed only from a freshly confirmed `detail_summary`;
  the next name or CP must differ, with at most two fixed swipes.
- Missing required artifacts or a non-complete manifest classify a scan as
  incomplete; malformed JSON, undecodable PNGs, unequal dimensions, or invalid
  annotations classify it as invalid.
- Annotation overwrite requires confirmation unless `--force` is explicit.
- Dataset tests create synthetic images at runtime; real scans remain local.

## Next steps

The automatic target is deliberately fixed to the existing Huawei Mate 30,
1440x3120, Traditional Chinese, WSL Ubuntu, Python 3.12, and Windows `adb.exe`
environment. `scan-auto-one --dry-run --debug` is the mandatory first physical
check before a live run.

On 2026-07-29, all eight real scans produced names, CP, moves, and three IV values.
The seventh CP uses a documented glyph fallback and warning. No annotation is
required, and no OCR result is used to estimate IVs. A connected-device live run
confirmed the no-scroll menu path and OCR `action_menu` detection at 0.99948 on the
first poll. A later connected-device run completed the entire one-Pokémon flow,
including appraisal dialogue, IV capture, safe exit, recognition, and a complete
manifest. Physical batch testing confirmed a durable first CSV row and the fixed
right swipe from 搗蛋小妖 CP318 to 睡睡菇 CP431. The batch summary-first fix prevents
the IV detector from misclassifying that new summary. The old move gate stopped when the section heading fell above its crop even
though move rows were visible. The authorized minimal fix keeps the anchored path and
adds a fixed-layout fallback requiring a move name and same-row damage number; saved
real move and appraisal screenshots verify both sides. A resumed live batch run then
kept 搗蛋小妖 CP318 as row 1, completed 睡睡菇 CP431 as row 2, and stopped at the
two-row limit with distinct scan IDs and identities. Device/language/version
generalization remains deferred.
