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
