# Development environment

## Canonical workspace

- Runtime: WSL2 Ubuntu 24.04
- Canonical Linux path:
  `/home/zhang/projects/pokemonGo_cleanUp`
- Windows view of the same path:
  `\\wsl$\Ubuntu-24.04\home\zhang\projects\pokemonGo_cleanUp`

Do not create a second active copy under `/mnt/c`, Windows Desktop, Downloads, or
OneDrive.

## Python

The project requires Python 3.12 or newer and uses a repository-local `.venv`.

Commands for **Ubuntu / WSL**:

```bash
cd /home/zhang/projects/pokemonGo_cleanUp
which python3
python3 --version
python3 -m venv .venv
source .venv/bin/activate
which python
python --version
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Validation:

```bash
python -m pip check
pytest
ruff check .
mypy
```

The tests mock ADB, include a command-level fake runner, and generate synthetic
PNG files at runtime. They do not need a physical device or repository
screenshots. CI runs this check set on Windows and Ubuntu with Python 3.12 and
3.13.

## Windows runtime validation

End users run the CLI from a separate Windows-native virtual environment. Do not
reuse the WSL `.venv` from PowerShell. Follow `README.md` to create a Windows
`.venv`, install Platform-Tools, authorize the device, and run:

```powershell
pokemon-go-cleanup device list
pokemon-go-cleanup device info
pokemon-go-cleanup capture
pokemon-go-cleanup scan-one --guided --notes "manual device check"
pokemon-go-cleanup dataset validate
pokemon-go-cleanup dataset status
```

Real screenshots, manifests, and annotations remain below `data/` and must not
be committed.


## Unicode IV nickname input (2026-09-06)

The circled IV suffix requires the phone-side
[ADB Keyboard component](https://github.com/senzhk/ADBKeyBoard),
`com.android.adbkeyboard/.AdbIME`. This is independent of Python dependencies.
After user-approved installation, enable it in the phone's input-method settings
(or with `adb shell ime enable com.android.adbkeyboard/.AdbIME` from Ubuntu / WSL).
Keep the normal keyboard selected: the ADB client temporarily switches only while
sending the UTF-8/Base64 suffix and restores the recorded original IME afterward.

The application checks enabled IMEs before any rename-enabled live scan and fails
without capturing/editing if the component is absent. Dry runs and scans without
renaming do not require it. No automatic APK installation or IME enabling occurs.
ADB Keyboard v2.4-dev is now installed and enabled on the authorized test phone.
The previous keyboard configuration was backed up before installation. Gboard is
the normal selected keyboard. The input wrapper checks the target IME binding and
shown state after switching and after restoration, so confirmation does not use
coordinates while the keyboard is still changing layout.

A complete live IV-only run on 2026-09-07 verified `向日種子⑮⑭⑩` and
restored Gboard. The run created zero scan files and did not change any CSV.
