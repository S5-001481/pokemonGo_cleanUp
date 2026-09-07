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

## IV naming only

Open the current Pokemon's detail page at the top,
then click **扫描 IV 并命名** in the GUI. This reads appraisal IVs and restores the
game's default Chinese name before appending IVs, for example `超梦⑭⑭⑮`.
Attack, defense and HP each use one circled number; zero is `⓪`. The complete
name must fit 12 characters; overlong names stop without truncation or ASCII fallback.

The button handles the current Pokemon once, skips moves and next-item switching,
and saves no CSV, screenshots, recognition JSON, or debug files. Batch limits,
CSV/resume, and debug settings do not apply. The success counter, timer, and Stop
button work for this action. Recognition or nickname verification failures stop
with a message in the log.

Ubuntu / WSL CLI: `pokemon-go-cleanup rename-iv-one`.

### Circled-number input setup

Install and enable [ADB Keyboard](https://github.com/senzhk/ADBKeyBoard) on the
phone first. Ordinary `adb input text` cannot send these Unicode characters;
selecting an English keyboard alone is insufficient. A missing bridge stops
before capture or nickname changes. This also applies to full-scan IV naming.
The project never installs or enables a keyboard automatically.

Keep your usual keyboard selected. During suffix input, the program temporarily
selects ADB Keyboard, sends UTF-8/Base64, and restores the original input method,
including on failure or interruption. Exact editor and final-name checks still
run afterward and do not accept ordinary digits in place of circles. A physical run on 2026-09-07 verified
`向日種子⑮⑭⑩` in both editor and final summary, restored Gboard, and created
no scan files or CSV changes. A dedicated ring/interior reader avoids whole-row
OCR confusing circled and ordinary digits.

## Interface Example

![Pokémon GO Cleanup interface](docs/images/interface-example.png)

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
            ├── recognition.json   # Created by the recognition command
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

## Safety, Privacy, and Scope

This repository does not contain any Pokémon images, icons, or screenshots. Pokémon is a trademark of its respective rights holders. This is an independent project and is not affiliated with or endorsed by the rights holders.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
