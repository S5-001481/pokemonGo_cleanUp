# pokemonGo_cleanUp

[English](README.md) | [简体中文](README.zh-CN.md)

`pokemonGo_cleanUp` is a local-first Windows CLI that captures the current screen
of an Android or HarmonyOS device through ADB. Each PNG is stored locally with a
UTF-8 JSON sidecar containing the device serial number, resolution, capture time,
file path, file size, and SHA-256 digest.
A guided scan can also group summary, moves, and appraisal screenshots from the
same device under one scan ID and one progressive manifest.
Dataset commands decode and validate those local artifacts, while manual
annotation writes typed ground truth without OCR.

The Python package is `pokemon_go_cleanup`; the installed command is
`pokemon-go-cleanup`.

## Scope and privacy

This initial release:

- detects ADB and lists attached device states;
- supports a Huawei Mate 30 running an ADB-compatible HarmonyOS version;
- reads the current device resolution;
- captures only the current display via `adb exec-out screencap -p`;
- keeps `scan-one --guided` fully manual with no device input;
- reads the fixed 1440x3120 Traditional Chinese layout with local OCR and IV bar
  geometry;
- offers one safety-gated `scan-auto-one` flow for that exact layout;
- recursively validates scan screenshots and typed manifests;
- records user-entered ground truth in local UTF-8 JSON;
- stores screenshots and metadata on the local computer.

It does **not** switch to another Pokémon, batch scan, transfer, power up,
evolve, rename, unlock moves, or battle. It never accesses Pokémon GO accounts,
private APIs, network traffic, or credentials. Automatic input is limited to the
documented single-scan state machine; a state mismatch stops before the next
input. Captured screens and annotations may contain personal information. The
entire `data/` tree and generated recognition/debug files are excluded from Git.
Review every local artifact before sharing it.

This repository contains no Pokémon artwork, icons, or screenshots. Pokémon is a
trademark of its respective owners; this independent project is not affiliated
with or endorsed by them.

## Requirements

- Windows 10 or 11
- Python 3.12 or newer
- Android SDK Platform-Tools (`adb`)
- A data-capable USB cable
- USB debugging enabled on the phone

## Windows installation

The following commands are for **Windows PowerShell**.

1. Install Python 3.12 or newer from
   [python.org](https://www.python.org/downloads/windows/). During installation,
   enable the option that makes Python available from the command line.
2. Download or clone this repository, then open PowerShell in its root directory.
3. Create an isolated environment and install the project:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pokemon-go-cleanup --help
```

If PowerShell blocks activation scripts, you can run the environment's executables
directly without changing policy:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\pokemon-go-cleanup.exe --help
```

## Install Android Platform-Tools on Windows

1. Download the current **SDK Platform-Tools for Windows** from the
   [official Android developer page](https://developer.android.com/tools/releases/platform-tools).
2. Extract the archive to a stable local folder such as
   `C:\Tools\platform-tools`.
3. Add that folder to your user `Path`, then close and reopen PowerShell.
4. Verify the installation:

```powershell
adb version
adb devices
```

You can avoid changing `Path` by passing the executable explicitly:

```powershell
pokemon-go-cleanup --adb-path "C:\Tools\platform-tools\adb.exe" device list
```

Use the latest stable Platform-Tools: Google documents that current ADB versions
are backward compatible with older Android devices.

## Prepare a Huawei Mate 30 / HarmonyOS device

Menu wording can vary by HarmonyOS or EMUI version. Huawei's documented route is:

1. Open **Settings > About phone**.
2. Tap **Build number** repeatedly until developer mode is enabled. Enter the
   lock-screen PIN if requested.
3. Open **Settings > System & updates > Developer options**.
4. Enable **USB debugging**.
5. Connect the unlocked phone with a data-capable USB cable. If prompted for a USB
   mode, choose **File transfer** rather than charge-only mode.
6. In Windows PowerShell, run:

```powershell
adb devices
```

7. On the phone, review the computer's RSA fingerprint, enable the remember option
   only if this is your own trusted computer, and tap **Allow**.
8. Run `adb devices` again. The state should be `device`, not `unauthorized`.

Huawei also documents the developer-mode and USB-debugging route in its
[official device development guide](https://developer.huawei.com/consumer/en/codelab/theme/index.html).
If the menu is missing, use Settings search for “USB debugging” and check the
device's region-specific Huawei support documentation.

To revoke an old computer authorization, use **Revoke USB debugging
authorizations** in Developer options, reconnect the cable, and accept the new
fingerprint prompt.

## Commands

List every device, including `unauthorized` and `offline` states:

```powershell
pokemon-go-cleanup device list
```

Show identity fields and current resolution for the only ready device:

```powershell
pokemon-go-cleanup device info
```

Capture the current screen:

```powershell
pokemon-go-cleanup capture
```

Run a guided three-screenshot scan for one Pokémon. The command waits for Enter
before each capture:

```powershell
pokemon-go-cleanup scan-one --guided
```

It asks for the top detail page (`summary.png`), all visible moves (`moves.png`),
and the appraisal view with Attack, Defense, and HP IV bars (`appraisal.png`).
The program does not scroll, tap, or open appraisal for you.

The command-specific options can be combined:

```powershell
pokemon-go-cleanup scan-one --guided --serial ABC123 --notes "Community Day" --output "D:\Local Captures\宝可梦整理"
```

`--output` is the data-directory base for this scan; files go below `OUTPUT/scans/`.

When multiple devices are ready, select one by its ADB serial:

```powershell
pokemon-go-cleanup device info --serial ABC123
pokemon-go-cleanup capture --serial ABC123
```

Choose another local data directory (global options go before the command):

```powershell
pokemon-go-cleanup --data-dir "D:\Local Captures\宝可梦整理" capture
```

Enable informational structured JSON logs:

```powershell
pokemon-go-cleanup --log-level INFO --log-format json capture
```

Configuration may also be supplied with environment variables:

- `POKEMON_GO_CLEANUP_DATA_DIR`
- `POKEMON_GO_CLEANUP_ADB_PATH`
- `POKEMON_GO_CLEANUP_ADB_TIMEOUT_SECONDS`

## Read the calibrated Huawei scans (OCR MVP)

This iteration intentionally supports only the existing Huawei Mate 30 scans:
1440x3120 pixels, Traditional Chinese Pokémon GO, WSL Ubuntu, and Python 3.12.
Install the local OCR extras inside the project virtual environment:

```bash
python -m pip install -e ".[ocr]"
```

Read one scan and save `recognition.json`:

```bash
pokemon-go-cleanup read-scan "data/scans/2026-07-28/<scan_id>"
pokemon-go-cleanup read-scan "data/scans/2026-07-28/<scan_id>" --debug
```

`--debug` saves the five OCR crops plus raw and annotated appraisal bars below
the scan's ignored `debug/` directory. Read every complete local scan and write
the requested CSV columns:

```bash
pokemon-go-cleanup read-dataset --csv inventory.csv
```

The reader uses RapidOCR only for name, CP, and moves. IV values come from
OpenCV color and bar geometry; OCR is not used for IV estimation. Missing or
low-confidence values are retained with warnings. No `ground_truth.json` is
required.

## Automatically scan one fixed Huawei Mate 30 page

This command is intentionally limited to Huawei Mate 30, 1440x3120, the current
Traditional Chinese UI, WSL Ubuntu, Python 3.12, and Windows `adb.exe` callable
from WSL. Manually open one Pokémon's detail page at the top first.

Start with the non-mutating check:

```bash
pokemon-go-cleanup scan-auto-one --dry-run --debug
```

Dry-run captures and validates the current summary page, prints every planned
coordinate, writes an incomplete local scan for inspection, and never calls ADB
tap, swipe, or BACK. Review `debug/automation/plan.json`, the initial PNG, and
the state JSON before allowing live input.

Run exactly one live scan only after that review:

```bash
pokemon-go-cleanup scan-auto-one --debug
```

When Windows Platform-Tools is not on the WSL PATH, pass the WSL view of
`adb.exe` as a global option before the command:

```bash
pokemon-go-cleanup --adb-path /mnt/c/Android/platform-tools/adb.exe scan-auto-one --dry-run --debug
```

The live state machine checks the expected page before every input and stops on
any mismatch. After `moves.png`, it accepts either `detail_moves` or
`detail_summary` and taps the always-visible menu button at `(1244, 2772)` without
scrolling back. It polls every 500 ms for up to 10 seconds until OCR reliably
classifies `action_menu`, then retries the same menu coordinate at most once if
the screen remains a detail state. It clicks the appraisal row only at the
high-confidence OCR box for `調查寶可夢`; there is no fallback coordinate for
that row, and targets near `傳送` are rejected. The appraisal screenshot is
accepted only after the existing three-IV-bar detector succeeds.

With `--debug`, operation screenshots, detections, target-state wait timing,
stability differences, and actual coordinates are saved under:

```text
data/scans/YYYY-MM-DD/<scan_id>/debug/automation/
```

On success, the command safely exits appraisal, runs the existing reader, writes
`recognition.json`, and prints name, CP, moves, and IV. On failure, existing
screenshots remain and `manifest.json` is marked `incomplete` with the failed
step. Ctrl+C exits with code 130 after the same recovery attempt.

The fixed coordinate sequence has focused tests and the summary/moves/IV state
detectors were checked against existing local scans. A 2026-07-29 physical run
completed the full automatic workflow: all three screenshots, OCR menu targeting,
appraisal dialogue, IV detection, safe exit, recognition, and a complete manifest.
Run dry-run first before each live input session.

## Batch scan the fixed Huawei Mate 30 inventory

Manually open the first Pokémon detail page at the top, then run a small physical
calibration before raising the limit:

```bash
pokemon-go-cleanup scan-batch --limit 2 --csv batch-test.csv --debug --delay 2
```

The batch command reuses the proven `scan-auto-one` service without changing its
menu or appraisal flow. After each complete recognized scan it atomically updates
the CSV, confirms that appraisal exited to `detail_summary`, waits the configured
delay, and sends the fixed right swipe `(260, 1500)` to `(1180, 1500)` for
600 ms. It polls every 500 ms for up to 15 seconds and accepts the next page only
when OCR reports `detail_summary` and either the name or CP differs. A still
unchanged page permits one final swipe; abnormal states or two unchanged attempts
stop the batch.

The default limit is 20. `--resume` validates an existing CSV and its referenced
complete manifests before appending; without `--resume`, an existing destination
is never overwritten. Every row includes the recognition fields, absolute scan
directory, and a local page fingerprint. Ctrl+C or a failed single scan preserves
all previously written rows. `--debug` keeps both each single-scan trace and the
switch evidence below that scan's ignored `debug/` directory.

Batch scanning still performs no transfer, power-up, evolution, rename, move
unlock, battle, account, or network action. It stops at the limit or when it
safely detects a return to the first name/CP.

## Validate and annotate local scans

Validate every recursively discovered scan below the default `data/scans/` root:

```powershell
pokemon-go-cleanup dataset validate
pokemon-go-cleanup dataset status
```

`validate` fully decodes all three PNGs, checks equal dimensions, and validates
`manifest.json` and any existing `ground_truth.json` with Pydantic. It exits
non-zero when at least one scan is invalid. `status` displays scan ID, capture
date, screenshot completeness, manifest validity, annotation presence, and
overall status without failing merely because a scan is invalid.

Use `--output` when the dataset root is elsewhere. This option points directly
to the directory that contains dated scan folders, not to the scan-one data base:

```powershell
pokemon-go-cleanup dataset validate --output "D:\Local Captures\宝可梦整理\scans"
```

Results use these states:

- `complete`: all required files exist, all PNGs decode with identical
  dimensions, the manifest is valid and marked `complete`, and any annotation is
  valid;
- `incomplete`: a required file is missing or the manifest is not marked
  `complete`;
- `invalid`: JSON validation, PNG decoding, dimensions, or an existing
  annotation fails validation.

Create a manual annotation for one scan directory:

```powershell
pokemon-go-cleanup annotate "data\scans\2026-07-28\<scan_id>"
```

The command first prints the full paths of `summary.png`, `moves.png`, and
`appraisal.png`, then prompts for every field. When a valid
`ground_truth.json` already exists, Enter keeps each displayed value. Replacing
the file requires interactive confirmation; `--force` explicitly skips that
confirmation.

For future non-interactive tooling, pass a UTF-8 JSON file:

```powershell
pokemon-go-cleanup annotate "data\scans\2026-07-28\<scan_id>" --input-json ".\annotation-input.json"
```

Example input:

```json
{
  "pokemon_name": "Example",
  "cp": 1000,
  "hp_current": 100,
  "hp_max": 100,
  "weight_kg": 10.5,
  "height_m": 1.2,
  "types": ["Type A", "Type B"],
  "fast_move": "Fast move",
  "charged_move_1": "Charged move",
  "charged_move_2": null,
  "attack_iv": 15,
  "defense_iv": 14,
  "hp_iv": 13,
  "favorite": false,
  "shiny": false,
  "shadow": false,
  "purified": false,
  "costume": false,
  "notes": null
}
```

CP, HP, weight, and height must be valid positive/non-negative values as
appropriate; each IV must be an integer from 0 through 15. The saved JSON keeps
Chinese and other Unicode text readable.

## Local data layout

A guided scan stores one fixed set of files below a unique scan ID. The manifest
is rewritten after each successful screenshot:

```text
data/
└── scans/
    └── 2026-07-28/
        └── 20260728_143015_123456_a1b2c3d4e5f60718293a4b5c6d7e8f90/
            ├── summary.png
            ├── moves.png
            ├── appraisal.png
            ├── manifest.json
            └── ground_truth.json  # optional, created by annotate
```

`manifest.json` records the scan ID, capture timestamps, device serial, device
model when available, screen resolution, screenshot filenames, guided workflow
mode, application version, optional notes, scan status, and a failed step when
applicable.

`ground_truth.json` is independent of capture metadata. It contains only
validated values entered by the user or supplied through `--input-json`.

On a midway failure, earlier screenshots are preserved and the manifest becomes
`incomplete`. Successful runs end with `complete`; temporary files are cleaned.

A standalone capture continues to create a PNG and JSON sidecar with the same
generated stem:

```text
data/
└── screenshots/
    └── 2026-07-28/
        ├── 20260728_143015_123456_ABC123_a1b2c3d4.png
        └── 20260728_143015_123456_ABC123_a1b2c3d4.json
```

Example metadata:

```json
{
  "schema_version": "1.0",
  "captured_at": "2026-07-28T14:30:15.123456+09:00",
  "serial_number": "ABC123",
  "resolution": {
    "width": 1080,
    "height": 2400
  },
  "screenshot_path": "C:\\path\\to\\data\\screenshots\\2026-07-28\\capture.png",
  "metadata_path": "C:\\path\\to\\data\\screenshots\\2026-07-28\\capture.json",
  "file_size_bytes": 123456,
  "sha256": "64-lowercase-hex-characters"
}
```

Windows paths and Unicode directory names are supported. Device serial numbers
are sanitized before being included in filenames; the original serial remains in
metadata.

## Error reporting

Expected failures are printed clearly and use stable process exit codes:

| Exit code | Meaning |
| ---: | --- |
| 2 | ADB is not installed or cannot be found |
| 3 | No connected, ready device |
| 4 | Device is unauthorized |
| 5 | Multiple ready devices; use `--serial` |
| 6 | Screenshot command failed or did not return a PNG |
| 7 | Another ADB command failed |
| 8 | Requested serial was not found |
| 9 | Requested device is offline or otherwise unavailable |
| 10 | ADB returned an unrecognized response |
| 11 | Local screenshot or metadata storage failed |
| 12 | Dataset root or scan traversal failed |
| 13 | Annotation input, validation, or overwrite protection failed |
| 14 | Calibrated screenshot recognition could not continue |
| 15 | Automatic scan stopped at a safety or timeout boundary |

Guided scan failures retain the underlying exit code and name the failed step.
Successful earlier screenshots remain in place and `manifest.json` is marked
`incomplete` whenever that recovery write succeeds.

Useful checks in **Windows PowerShell**:

```powershell
Get-Command adb
adb kill-server
adb start-server
adb devices -l
pokemon-go-cleanup device list
```

If the phone remains absent, try another data-capable cable or USB port and check
Windows Device Manager for a device-driver problem. If it is `unauthorized`,
unlock the phone, revoke USB debugging authorizations, reconnect, and accept the
new prompt.

## Development

The repository uses a `src` layout, Python 3.12+, full type hints, Typer,
Pydantic, pytest, Ruff, and mypy.

Commands below are for **Ubuntu / WSL**, from the canonical project path:

```bash
cd /home/zhang/projects/pokemonGo_cleanUp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pip check
pytest
ruff check .
mypy
```

No physical phone is required for the tests. ADB behavior includes a
command-level fake runner, and dataset tests generate small synthetic PNGs at
runtime. CI runs the dependency check, pytest, Ruff, and mypy on Windows and
Ubuntu with Python 3.12 and 3.13.

## Contributing and license

Contributions that preserve the local-first, account-free scope are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the [Apache License 2.0](LICENSE).
