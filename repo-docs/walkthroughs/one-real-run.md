# One capture from command to local artifacts

This walkthrough follows `pokemon-go-cleanup capture` with one authorized phone
connected over USB. CLI options and environment-backed settings are the first
input. Success is a PNG and matching JSON sidecar under the current date.

The hard part is deciding when ADB output is safe to persist. ADB can be missing;
the device list can be empty, unauthorized, offline, or ambiguous; and a
successful subprocess can still return bytes that are not a screenshot. The path
therefore narrows uncertainty in stages before touching local capture storage.

## Step 1: The command fixes the local rules for this run

The run begins by deciding where local data belongs, how to find ADB, how long an
ADB call may take, and how logs should be rendered. Downstream code receives one
validated settings object, so it does not need to reinterpret CLI strings.

The Typer callback in
[`cli.py`](../../src/pokemon_go_cleanup/cli.py) constructs the Pydantic
[`AppConfig`](../../src/pokemon_go_cleanup/config.py). Explicit global options
such as `--data-dir` and `--adb-path` override matching environment values.
Omitted options let the settings model read the `POKEMON_GO_CLEANUP_*`
environment namespace and then use its defaults. The command configures
JSON logging before device work begins, allowing expected failures to record an
event, error type, and exit code.

## Step 2: The device list becomes one trustworthy target

A capture cannot safely choose “the first phone.” The list may include states
that look connected but cannot execute shell commands, and two ready phones make
an implicit choice dangerous. This phase either returns one ready device or
stops with a precise instruction.

[`discover_adb()`](../../src/pokemon_go_cleanup/adb.py) accepts a configured
executable/directory or searches `PATH`. `AdbClient.list_devices()` parses
`adb devices -l` into `Device` models. `select_device()` then applies these rules:

| Observed state | Result |
| --- | --- |
| No ready devices | Exit 3 with connection/debugging guidance |
| Unauthorized device(s), no ready device | Exit 4 with fingerprint guidance |
| More than one ready device | Exit 5 and require `--serial` |
| Requested serial absent | Exit 8 |
| Requested serial offline or unavailable | Exit 9 |
| Exactly one ready device | Continue with that serial |

The state branches are exercised in
[`test_adb.py`](../../tests/test_adb.py). This is the first major trust boundary:
resolution and screenshot commands are not attempted until selection succeeds.

## Step 3: ADB output must describe a screen and look like an image

The selected serial is stable, but two independent ADB responses still need
validation. The logical dimensions must contain a positive `WIDTHxHEIGHT` pair,
and the screenshot bytes must begin with the PNG signature. Without these checks,
an error message or empty response could be saved with a `.png` extension.

`AdbClient.get_resolution()` runs `adb -s SERIAL shell wm size` and uses the last
reported size, which handles the common physical-size plus override-size response.
`AdbClient.capture_screen()` runs
`adb -s SERIAL exec-out screencap -p`. It maps a nonzero subprocess result, empty
output, or non-PNG output to `ScreenshotCaptureError` and exit code 6.

No output directory has been created yet. A selection, resolution, or screenshot
failure therefore leaves capture storage unchanged.

## Step 4: The result gets a collision-resistant local identity

Once the bytes pass the boundary, the service can name the artifact without
using unsafe characters from the device serial. The current local date chooses
the directory; time down to microseconds, a sanitized serial, and a random suffix
make accidental overwrite unlikely.

[`CaptureService.capture()`](../../src/pokemon_go_cleanup/capture.py) creates the
shape:

```text
data/screenshots/YYYY-MM-DD/
├── YYYYMMDD_HHMMSS_microseconds_SAFE-SERIAL_random.png
└── YYYYMMDD_HHMMSS_microseconds_SAFE-SERIAL_random.json
```

The configured data directory may contain Windows path separators and Unicode
characters. Only the generated serial component is reduced to an ASCII-safe
filename fragment; the original serial remains in metadata.

## Step 5: The files make the run auditable

The PNG is the captured evidence. The sidecar preserves enough context to locate
and verify it later without querying the phone again. After both writes, the
command prints the Pydantic result as JSON and an informational log can record
the same paths.

[`CaptureMetadata`](../../src/pokemon_go_cleanup/models.py) records the
timezone-aware capture time, original serial, resolution, absolute PNG and JSON
paths, byte size, and SHA-256. [`test_capture.py`](../../tests/test_capture.py)
uses a Unicode temporary directory and fixed time to verify the exact dated
layout and UTF-8 JSON values.

A local write error becomes exit code 11. The current writes are sequential, so a
failure while writing JSON can leave the PNG in place. That partial-artifact case
is reported rather than silently treated as success.

## Verify the model

From the repository's WSL `.venv`, the focused check is:

```bash
pytest tests/test_adb.py tests/test_capture.py -q
```

The full project gate is:

```bash
pytest
ruff check .
mypy
```

These tests prove the parsing, selection, byte-validation, and local persistence
contracts without a phone. Follow the root README's
[native Windows device setup](../../README.md#prepare-a-huawei-mate-30--harmonyos-device)
for the remaining Huawei integration check. For audit details and the exact
limits of each claim, use the
[source evidence and falsifying checks](../references/source-evidence.md).

Evidence status: Confirmed unless noted.
