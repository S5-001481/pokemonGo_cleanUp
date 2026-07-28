# pokemonGo_cleanUp

[English](README.md) | [简体中文](README.zh-CN.md)

`pokemonGo_cleanUp` is a local-first Windows CLI that captures the current screen
of an Android or HarmonyOS device through ADB. Each PNG is stored locally with a
UTF-8 JSON sidecar containing the device serial number, resolution, capture time,
file path, file size, and SHA-256 digest.
A guided scan can also group summary, moves, and appraisal screenshots from the
same device under one scan ID and one progressive manifest.

The Python package is `pokemon_go_cleanup`; the installed command is
`pokemon-go-cleanup`.

## Scope and privacy

This initial release:

- detects ADB and lists attached device states;
- supports a Huawei Mate 30 running an ADB-compatible HarmonyOS version;
- reads the current device resolution;
- captures only the current display via `adb exec-out screencap -p`;
- guides the user through three manually prepared views without sending tap or
  swipe commands;
- stores screenshots and metadata on the local computer.

It does **not** implement OCR, automate gameplay, send UI taps or swipes, access
Pokémon GO accounts or private APIs, inspect network traffic, or handle login
credentials. Captured screens may contain personal information, so both
`data/screenshots/` and `data/scans/` are excluded from Git. Review captures
before sharing them.

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
            └── manifest.json
```

`manifest.json` records the scan ID, capture timestamps, device serial, device
model when available, screen resolution, screenshot filenames, guided workflow
mode, application version, optional notes, scan status, and a failed step when
applicable.

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
pytest
ruff check .
mypy
```

No physical phone is required for the tests; ADB behavior includes a command-level
fake runner for the guided integration path.

## Contributing and license

Contributions that preserve the local-first, account-free scope are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the [Apache License 2.0](LICENSE).
