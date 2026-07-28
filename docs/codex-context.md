# Codex project context

## Purpose

`pokemonGo_cleanUp` is an open-source, local-first CLI for capturing screenshots
from an ADB-connected Android or HarmonyOS phone. It supports standalone captures
and a user-guided three-view scan grouped by one scan ID. The GitHub/display name
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
- Group `summary.png`, `moves.png`, `appraisal.png`, and `manifest.json` below
  `data/scans/YYYY-MM-DD/<scan_id>/`.
- Record progressive `in_progress`, `complete`, or `incomplete` scan status.
- Emit structured JSON or text logs.

Explicitly excluded: OCR, gameplay automation, Pokémon GO account access, private
APIs, credential handling, and network traffic inspection. No copyrighted
Pokémon media belongs in the repository.

## Architecture

- `src/pokemon_go_cleanup/adb_runner.py`: injectable subprocess command boundary.
- `src/pokemon_go_cleanup/adb.py`: parsing, state selection, resolution query, and
  PNG validation.
- `src/pokemon_go_cleanup/capture.py`: dated paths and local PNG/metadata writes.
- `src/pokemon_go_cleanup/scan.py`: scan IDs, atomic step writes, progressive
  manifest updates, and incomplete recovery.
- `src/pokemon_go_cleanup/guided_prompts.py`: bilingual manual-step prompts.
- `src/pokemon_go_cleanup/models.py`: immutable device, capture, and scan models.
- `src/pokemon_go_cleanup/config.py`: environment/CLI-backed Pydantic settings.
- `src/pokemon_go_cleanup/cli.py`: Typer commands and user-facing errors.
- `tests/`: physical-device-free unit, CLI, and fake-runner integration tests.

Expected user failures have stable exit codes. Captures are excluded by
`.gitignore` under both `data/screenshots/` and `data/scans/`.

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

## Next steps

Standalone capture and guided scan should be validated on the target Huawei Mate
30 from native Windows. OCR remains intentionally unimplemented. Future expansion
must preserve the privacy and non-automation boundaries above.
