# pokemonGo_cleanUp

English | [简体中文](README.zh-CN.md)

## Goal

Use ADB to capture each Pokémon's details page and appraisal IV bars in Pokémon GO, helping you organize information such as CP, HP, moves, and IVs for your existing Pokémon.

## Quick Start

This project is currently recommended for use in a Windows WSL Ubuntu environment.

Open an Ubuntu terminal and run the following commands:

```bash
git clone https://github.com/S5-001481/pokemonGo_cleanUp.git
cd pokemonGo_cleanUp
bash scripts/setup-wsl.sh
```

After the installation completes, the terminal will display:

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
alt="Example of the Pokémon GO Cleanup graphical interface"
/>

> [!IMPORTANT]
> OCR and automated scanning are not general-purpose features. They have currently been calibrated only for the following environment:
> **Huawei Mate 30, 1440 × 3120 resolution, Traditional Chinese Pokémon GO interface,
> WSL Ubuntu, Python 3.12, and a Windows `adb.exe` that can be invoked from WSL**.

## Compatibility

| Feature                                     | Currently Supported Environment                                                     |
| ------------------------------------------- | ----------------------------------------------------------------------------------- |
| Device detection and screenshots            | Windows 10/11, Python 3.12+, and an ADB-compatible Android or HarmonyOS device      |
| Guided manual scanning                      | Same as above                                                                       |
| OCR and IV recognition                      | Huawei Mate 30, 1440 × 3120, Traditional Chinese interface, WSL Ubuntu, Python 3.12 |
| Automated single-Pokémon and batch scanning | Same as above, with WSL able to invoke the Windows `adb.exe`                        |

## Using the GUI

The desktop launcher starts the existing CLI through a Tkinter interface; it
does not use a separate automation implementation.

1. Connect and unlock the phone, enable USB debugging, and leave Pokémon GO on a
   Pokémon detail summary page.
2. Click **Check phone** to verify that exactly one authorized device is ready.
3. Click **Check current page (no input)** before the first live run. This uses
   `scan-auto-one --dry-run` and sends no taps, swipes, or text.
4. Use **Scan one Pokémon** for one automatic scan, or choose the row limit, delay,
   CSV path, and options before clicking **Start batch scan**.
5. Enable **Resume existing CSV** only when continuing the selected batch CSV.
   Without resume, the GUI refuses to overwrite an existing CSV.

## Rename-with-IV Keyboard Requirement

Before enabling **Reset Chinese name and append IV** in the GUI or using
`--rename-with-iv` with `scan-auto-one` or `scan-batch`, manually switch Gboard
to its **English** layout.
The Pinyin layout converts the ASCII `/` characters sent by ADB to full-width `／`.
The program does not switch or restore the keyboard layout automatically. If
full-width digits or punctuation are detected, it stops before confirming the
nickname so the incorrect value is not saved.

## Command-line Workflows

Run commands from the project directory with the installed virtual environment:

```bash
# Inspect the connected device and its current resolution.
.venv/bin/python -m pokemon_go_cleanup device info

# Capture one screenshot plus JSON metadata.
.venv/bin/python -m pokemon_go_cleanup capture

# Manually prepare and confirm summary, moves, and appraisal screens.
.venv/bin/python -m pokemon_go_cleanup scan-one --guided

# Check the fixed-layout automatic plan without phone input.
.venv/bin/python -m pokemon_go_cleanup scan-auto-one --dry-run --debug

# Automatically scan one Pokémon.
.venv/bin/python -m pokemon_go_cleanup scan-auto-one --debug

# Scan up to five total CSV rows, waiting two seconds before each switch.
.venv/bin/python -m pokemon_go_cleanup scan-batch \
  --limit 5 --csv inventory.csv --delay 2 --debug

# Continue a previously created batch CSV.
.venv/bin/python -m pokemon_go_cleanup scan-batch \
  --limit 20 --csv inventory.csv --delay 2 --debug --resume
```

To rename during an automatic single or batch scan, first satisfy the English
keyboard requirement above, then add `--rename-with-iv`. Resume a batch with the
same rename setting used to create it. `--limit` is the maximum **total** row
count in the CSV, not the number of additional rows.

Recognition and dataset commands operate only on local files:

```bash
# Read one complete calibrated scan and write recognition.json.
.venv/bin/python -m pokemon_go_cleanup read-scan data/scans/YYYY-MM-DD/SCAN_ID --debug

# Read all complete scans into a separate inventory CSV.
.venv/bin/python -m pokemon_go_cleanup read-dataset --csv recognized-inventory.csv

# Validate or summarize all stored scan directories.
.venv/bin/python -m pokemon_go_cleanup dataset validate
.venv/bin/python -m pokemon_go_cleanup dataset status

# Create or validate a manual ground_truth.json annotation.
.venv/bin/python -m pokemon_go_cleanup annotate data/scans/YYYY-MM-DD/SCAN_ID
```

Use `.venv/bin/python -m pokemon_go_cleanup --help` and append `--help` to any
subcommand for the complete option list. Root options such as `--data-dir`,
`--adb-path`, and `--log-format text` must appear before the subcommand.

Required hardware and software:

* Python 3.12 or later;
* Android SDK Platform-Tools, including `adb`;
* A USB cable that supports data transfer;
* USB debugging enabled on the phone.

Environment variables:

| Variable                                 | Purpose                                       |
| ---------------------------------------- | --------------------------------------------- |
| `POKEMON_GO_CLEANUP_DATA_DIR`            | Directory for local screenshots and scan data |
| `POKEMON_GO_CLEANUP_ADB_PATH`            | Explicit path to `adb` or `adb.exe`           |
| `POKEMON_GO_CLEANUP_ADB_TIMEOUT_SECONDS` | Timeout for ADB commands, in seconds          |

## Local Data Directory

A complete manual or automated scan has the following structure:

```text
data/
└── scans/
    └── YYYY-MM-DD/
        └── <scan_id>/
            ├── summary.png
            ├── moves.png
            ├── appraisal.png
            ├── manifest.json
            ├── recognition.json   # Automatic scan or read-scan output
            ├── renamed_summary.png # Present after a verified IV rename
            ├── nickname_change.json # Exact verified rename evidence
            ├── ground_truth.json  # Optional; created by annotate
            └── debug/             # Optional; ignored by Git
```

PNG files from individual screenshots and their matching JSON sidecar files are stored in:

```text
data/screenshots/YYYY-MM-DD/
```

`manifest.json` records scan progress and device metadata;
`recognition.json` stores machine-recognized values and warnings;
`ground_truth.json` separately stores verified manual input values.

Batch scanning writes one CSV row atomically only after a complete single scan
and all identity/rename checks pass. A rename-enabled CSV currently contains 16
columns, including `nickname_before`, `nickname_after`, and `rename_status`.
`--resume` validates the header, prior scan directories, manifests, saved
screenshots, duplicate scan IDs, and the last-page position before continuing.

## Automation Safety and Failure Behavior

Automatic input is limited to the calibrated Huawei Mate 30 layout. The scanner
checks the expected page before each action, uses an OCR-confirmed menu target and
the fixed nickname-row center `(720,1460)`, verifies that the nickname editor
actually opens, verifies IV bars, and verifies switching reaches another Pokémon.
It never transfers Pokémon, powers up, evolves, unlocks moves, or accesses account
credentials or private APIs.

If a step cannot be verified, completed screenshots remain on disk and the scan
manifest is marked `incomplete`. Exit code `15` means one automatic scan stopped
safely; exit code `16` means the batch layer rejected its state, CSV, resume, or
identity evidence. A failed batch item writes no partial CSV row and sends no
next-Pokémon swipe after the failure. Ctrl+C preserves completed rows and files.

## Safety, Privacy, and Scope

This repository does not contain any Pokémon images, icons, or screenshots. Pokémon is a trademark of its respective rights holders. This is an independent project and is not affiliated with or endorsed by the rights holders.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
