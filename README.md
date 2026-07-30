# pokemonGo_cleanUp

English | [简体中文](README.zh-CN.md)

## Goal

Capture the detail page and IV bars for each Pokémon in Pokémon GO on an Android device via ADB, helping you organize information such as CP, HP, moves, and IVs for your existing Pokémon.

## Quick Start

This project is currently recommended for use in a WSL Ubuntu environment on Windows.

Open an Ubuntu terminal and run the following commands in order:

```bash
git clone https://github.com/S5-001481/pokemonGo_cleanUp.git
cd pokemonGo_cleanUp
bash scripts/setup-wsl.sh
```

After a successful installation, the terminal will display:

```text
Installation complete.

Created on the Windows desktop:
Pokémon GO Cleanup.bat
```

After that, double-click `Pokémon GO Cleanup.bat` on the Windows desktop to launch the graphical interface.

## Interface Example

<img
  src="示例图片.png"
  width="900"
  alt="Pokémon GO Cleanup graphical interface example"
/>

> [!IMPORTANT]
> OCR and automatic scanning are not general-purpose features. They have currently been calibrated only for the following environment:
> **Huawei Mate 30, 1440 × 3120 resolution, Traditional Chinese Pokémon GO interface,
> WSL Ubuntu, Python 3.12, and a Windows `adb.exe` that can be called from WSL**.

## Compatibility

| Feature | Currently Supported Environment |
| --- | --- |
| Device inspection and screenshots | Windows 10/11, Python 3.12+, and an ADB-compatible Android or HarmonyOS device |
| Guided manual scanning | Same as above |
| OCR and IV recognition | Huawei Mate 30, 1440 × 3120, Traditional Chinese interface, WSL Ubuntu, Python 3.12 |
| Automatic single and batch scanning | Same as above, with Windows `adb.exe` callable from WSL |

Required hardware and software:

- Python 3.12 or later;
- Android SDK Platform-Tools (including `adb`);
- a USB cable that supports data transfer;
- USB debugging enabled on the phone.

## Quick Start in WSL Ubuntu

The following commands assume that the repository is located at
`/home/zhang/projects/pokemonGo_cleanUp`.

```bash
cd /home/zhang/projects/pokemonGo_cleanUp

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev,ocr]"

pokemon-go-cleanup --help
```

If Windows Platform-Tools cannot be found in the WSL `PATH`, pass the WSL path to
`adb.exe` before the subcommand:

```bash
pokemon-go-cleanup \
  --adb-path /mnt/c/Android/platform-tools/adb.exe \
  device list
```

Recognition and automatic scanning commands use the same global option:

```bash
pokemon-go-cleanup \
  --adb-path /mnt/c/Android/platform-tools/adb.exe \
  scan-auto-one --dry-run --debug
```

## Installation in Windows PowerShell

Install Python 3.12 or later, then run the following commands from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

pokemon-go-cleanup --help
```

If PowerShell blocks the virtual-environment activation script, you can call the programs inside the virtual environment directly:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\pokemon-go-cleanup.exe --help
```

Download the latest stable **SDK Platform-Tools for Windows** from the
[official Android developer page](https://developer.android.com/tools/releases/platform-tools),
extract it to a fixed directory such as `C:\Android\platform-tools`, and then verify the installation:

```powershell
adb version
adb devices
```

You can also specify the executable explicitly:

```powershell
pokemon-go-cleanup `
  --adb-path "C:\Android\platform-tools\adb.exe" `
  device list
```

## Prepare the Phone

Menu names may vary slightly between devices and system versions. A common path on Huawei devices is:

1. Open **Settings > About phone**.
2. Tap **Build number** repeatedly until developer mode is enabled.
3. Open **Settings > System & updates > Developer options**.
4. Enable **USB debugging**.
5. Connect the unlocked phone with a USB cable that supports data transfer and choose
   **File transfer** when prompted.
6. Run `adb devices`.
7. Verify the RSA fingerprint on the phone and authorize the trusted computer.
8. Run `adb devices` again. The device status should be `device`.

If the status is `unauthorized`, unlock the phone, revoke the old USB debugging authorizations, reconnect it, and accept the new authorization prompt.

## Command Overview

### Inspect Devices

```bash
pokemon-go-cleanup device list
pokemon-go-cleanup device info
pokemon-go-cleanup device info --serial ABC123
```

`device list` displays available, unauthorized, and offline devices. If multiple available devices are connected, use `--serial` to select the target device.

### Capture One Screen

```bash
pokemon-go-cleanup capture
pokemon-go-cleanup capture --serial ABC123
```

Each capture produces a PNG and a UTF-8 JSON sidecar containing the capture time, device serial number, resolution, file path, file size, and SHA-256 digest.

To use a different local data directory, place the global option before the subcommand:

```bash
pokemon-go-cleanup --data-dir "/path/to/local-data" capture
```

### Perform a Guided Three-Page Scan

```bash
pokemon-go-cleanup scan-one --guided
```

Before each screenshot, the command waits for the user to press Enter. Manually prepare the following pages:

1. `summary.png`: the top of the Pokémon detail page;
2. `moves.png`: the detail page with all moves visible;
3. `appraisal.png`: the appraisal page with the Attack, Defense, and HP bars visible.

Manual mode does not send tap, swipe, or BACK input.

Optional arguments:

```bash
pokemon-go-cleanup scan-one --guided \
  --serial ABC123 \
  --notes "Community Day" \
  --output "/path/to/local-data"
```

`--output` specifies the data-directory base for this scan. The final files are stored under
`OUTPUT/scans/`.

### Read a Calibrated Scan

First install the optional OCR dependencies:

```bash
python -m pip install -e ".[ocr]"
```

Then read a complete three-page scan:

```bash
pokemon-go-cleanup read-scan "data/scans/YYYY-MM-DD/<scan_id>"
pokemon-go-cleanup read-scan "data/scans/YYYY-MM-DD/<scan_id>" --debug
```

RapidOCR is used for the name, CP, and moves. IVs are determined through OpenCV color and bar-geometry analysis rather than OCR. Unrecognized or low-confidence values are retained with a warning.

`--debug` saves OCR crops and IV-detection overlays in the Git-ignored
`debug/` directory under the scan directory.

### Generate a CSV from Complete Scans

```bash
pokemon-go-cleanup read-dataset --csv inventory.csv
```

The command recursively finds complete scans under the current `data/scans/` root, writes one row for each scan, and saves the corresponding `recognition.json`.

### Automatically Scan One Pokémon

First, manually open the top of a Pokémon detail page that matches the calibrated environment.

Always begin with a dry run that sends no device input:

```bash
pokemon-go-cleanup scan-auto-one --dry-run --debug
```

The dry run captures and identifies the current page, prints the planned operations, and creates an incomplete scan for inspection. It does not send tap, swipe, or BACK input.

Before allowing live input, inspect:

```text
data/scans/YYYY-MM-DD/<scan_id>/
├── manifest.json
└── debug/
    └── automation/
        └── plan.json
```

After confirming that the dry run matches the actual page, run:

```bash
pokemon-go-cleanup scan-auto-one --debug
```

The live state machine checks the page before every input. On success, it saves all three screenshots, safely exits the appraisal screen, generates `recognition.json`, and marks the manifest as complete. If it encounters an unexpected page, timeout, failure, or Ctrl+C, it stops, preserves the existing evidence, and marks the manifest as incomplete.

### Scan a Limited Number of Consecutive Pokémon

First, open the top of the detail page for the first Pokémon. When using the feature for the first time, perform a small physical-device calibration:

```bash
pokemon-go-cleanup scan-batch \
  --limit 2 \
  --csv batch-test.csv \
  --debug \
  --delay 2
```

After each scan, the command safely exits the appraisal screen, atomically updates the CSV, confirms that it has returned to the detail page, and then swipes left to the next Pokémon. It proceeds only when both the page state and the local fingerprint indicate that the Pokémon has changed.

The default limit is 20. An existing CSV is never silently overwritten; it is continued only when `--resume` is explicitly supplied:

```bash
pokemon-go-cleanup scan-batch \
  --limit 20 \
  --csv inventory.csv \
  --resume \
  --debug
```

If Ctrl+C is pressed or a later scan fails, all previously completed CSV rows and scan files are preserved.

### Validate Local Data

```bash
pokemon-go-cleanup dataset status
pokemon-go-cleanup dataset validate
```

Use `--output` when the scan root is not in the default location:

```bash
pokemon-go-cleanup dataset validate \
  --output "/path/to/local-data/scans"
```

Scan statuses:

- `complete`: all required files are present and valid;
- `incomplete`: required files are missing, or the manifest has not yet been marked complete;
- `invalid`: PNG decoding, image dimensions, JSON, manifest, or annotation validation failed.

`dataset validate` returns a non-zero exit code if at least one invalid scan exists.

### Add Manual Ground Truth

```bash
pokemon-go-cleanup annotate "data/scans/YYYY-MM-DD/<scan_id>"
```

The interactive command displays the full paths of all three screenshots and prompts for typed annotation values one by one. Press Enter to retain an existing valid value.

For non-interactive input:

```bash
pokemon-go-cleanup annotate \
  "data/scans/YYYY-MM-DD/<scan_id>" \
  --input-json annotation-input.json
```

Use `--force` only when you explicitly need to overwrite an existing `ground_truth.json` without confirmation.

## Configuration

Global options must appear before the subcommand:

```bash
pokemon-go-cleanup \
  --data-dir "/path/to/local-data" \
  --adb-path "/path/to/adb.exe" \
  --adb-timeout 30 \
  --log-level INFO \
  --log-format text \
  device list
```

Environment variables:

| Variable | Purpose |
| --- | --- |
| `POKEMON_GO_CLEANUP_DATA_DIR` | Local screenshot and scan data directory |
| `POKEMON_GO_CLEANUP_ADB_PATH` | Explicit path to `adb` or `adb.exe` |
| `POKEMON_GO_CLEANUP_ADB_TIMEOUT_SECONDS` | ADB command timeout in seconds |

## Local Data Directory

A complete manual or automatic scan has the following structure:

```text
data/
└── scans/
    └── YYYY-MM-DD/
        └── <scan_id>/
            ├── summary.png
            ├── moves.png
            ├── appraisal.png
            ├── manifest.json
            ├── recognition.json   # Created by a recognition command
            ├── ground_truth.json  # Optional; created by annotate
            └── debug/             # Optional; ignored by Git
```

A standalone screenshot's PNG and matching JSON sidecar are stored under:

```text
data/screenshots/YYYY-MM-DD/
```

`manifest.json` records scan progress and device metadata;
`recognition.json` stores machine-recognized values and warnings;
`ground_truth.json` separately stores validated manual input values.

## Safety, Privacy, and Feature Boundaries

This repository contains no Pokémon images, icons, or screenshots. Pokémon is a trademark of its respective owners. This is an independent project and is neither affiliated with nor endorsed by the rights holders.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
