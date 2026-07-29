# One guided scan from device selection to manifest

This walkthrough follows `pokemon-go-cleanup scan-one --guided` with one
authorized phone connected. The user prepares three visible states for the same
Pokémon: the top of the detail page, the moves section, and the appraisal bars.
Success is one scan directory containing `summary.png`, `moves.png`,
`appraisal.png`, and a complete `manifest.json`.

The hard part is preserving a truthful group when an external screenshot command
can fail between steps. Each finished image must remain usable, temporary files
must not masquerade as captures, and the manifest must say whether the set is
complete.

## Step 1: One device becomes the scan target

The command first applies the same trust boundary as a single capture: ADB must
exist, and device selection must resolve to exactly one ready serial. `--serial`
removes ambiguity when several devices are attached. The selected device also
provides its model, when ADB reports one, and its current logical resolution.

The selection and resolution behavior remain in
[`adb.py`](../../src/pokemon_go_cleanup/adb.py). An injectable command runner in
[`adb_runner.py`](../../src/pokemon_go_cleanup/adb_runner.py) lets tests exercise
this boundary without starting ADB.

## Step 2: The scan gets an identity before capture begins

After selection, the workflow creates a timestamp-and-random scan ID and a dated
directory under `data/scans/YYYY-MM-DD/<scan_id>/`. An initial
`manifest.json` records the device, resolution, application version, optional
notes, guided mode, and `in_progress` status.

[`GuidedScanService`](../../src/pokemon_go_cleanup/scan.py) owns this lifecycle.
The `--output` option replaces the data-directory base for this run, while
`--notes` stores local free text in the manifest.

## Step 3: The terminal and the user hand off control three times

For each view, the CLI prints a Chinese and English instruction, then waits for
Enter. The user performs all scrolling, appraisal opening, and dialogue
advancement on the phone. The program only sends
`adb exec-out screencap -p`; it never sends tap or swipe commands.

The order is fixed:

1. top of the detail page becomes `summary.png`;
2. the visible moves section becomes `moves.png`;
3. visible Attack, Defense, and HP IV bars become `appraisal.png`.

The bilingual interaction lives in
[`guided_prompts.py`](../../src/pokemon_go_cleanup/guided_prompts.py); the service
accepts the prompt as a callback so storage tests do not need an interactive
terminal.

## Step 4: Each accepted screenshot becomes visible atomically

ADB validates that screenshot output begins with the PNG signature. The scan
service then writes the bytes to a unique temporary file in the destination
directory, flushes them, and replaces the final step filename. Cleanup runs
whether replacement succeeds or fails, so an unfinished `.tmp` file is not
treated as a screenshot.

After each successful replacement, the manifest is atomically rewritten with
that step's capture timestamp and filename. The final appraisal update changes
the scan status to `complete`. The exact fields are defined by
[`ScanManifest`](../../src/pokemon_go_cleanup/models.py).

## Step 5: A midway failure leaves an honest partial scan

If screenshot capture or local screenshot persistence fails, successful earlier
PNGs remain in the directory. The service rewrites the manifest with
`scan_status: "incomplete"` and the exact `failed_step`, then raises a
user-facing error that retains the underlying exit code.

[`test_scan.py`](../../tests/test_scan.py) covers a moves-step failure, atomic
temporary-file cleanup, and the complete flow through a fake ADB runner.
[`test_scan_cli.py`](../../tests/test_scan_cli.py) checks the bilingual prompts
and `--serial`, `--notes`, and `--output` behavior.

## Verify the model

From the repository's WSL `.venv`:

```bash
pytest tests/test_scan.py tests/test_scan_cli.py -q
ruff check .
mypy
```

The target Huawei Mate 30 has produced eight guided scan groups that the dataset
validator recognizes as complete: 24 decodable PNGs and eight valid manifests.
Those private artifacts remain below ignored `data/` and are not copied into
tests or documentation.

For the original one-screenshot path, follow
[one capture from command to local artifacts](one-real-run.md). For
validation and manual annotation, continue with
[the dataset workflow](dataset-validation-and-annotation.md). For claim-level
limits, use the
[source evidence and falsifying checks](../references/source-evidence.md).

Evidence status: Confirmed unless noted.
