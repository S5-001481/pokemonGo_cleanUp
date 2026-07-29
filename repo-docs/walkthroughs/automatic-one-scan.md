# One safety-gated automatic scan

This walkthrough follows `pokemon-go-cleanup scan-auto-one` after the user has
manually opened one Pokémon detail page at the top. It is not a batch workflow:
success means one dated directory with three screenshots, a complete manifest,
`recognition.json`, and the phone returned from appraisal.

The hard part is not sending the next input just because time passed. Every tap
or swipe must have fresh visual evidence that the current page matches the one
state allowed to receive that input. Unknown screens stop the run and preserve
what already exists.

## Step 1: Fix one device and one known layout

The command resolves exactly one ready ADB device and rejects any resolution
other than 1440x3120. The coordinate profile is deliberately limited to the
Huawei Mate 30 and the current Traditional Chinese interface; it is not a device
abstraction.

All fixed tap and swipe coordinates, per-step timing, the whole-flow timeout, and
the forbidden lower transfer band live together in
[`automation.py`](../../src/pokemon_go_cleanup/automation.py). ADB input itself is
limited to the explicit `tap` and `swipe` wrappers in
[`adb.py`](../../src/pokemon_go_cleanup/adb.py).

## Step 2: Dry-run proves the initial boundary without input

`scan-auto-one --dry-run --debug` captures the current display and polls every
500 ms for at most 15 seconds without sending input. `detail_summary` now
requires a recognized name plus either CP or the fixed `SUMMARY_HP_RECT` text
matching `number / number HP`; a single name, CP, or HP signal remains
`unknown`. Name plus CP is accepted immediately. Name plus HP confirms the page
but keeps polling for CP, and the last such valid screenshot becomes
`summary.png` if the full interval expires.

CP OCR keeps the original enlarged crop and also tries grayscale, CLAHE, Otsu,
inverted Otsu, adaptive threshold, and inverted adaptive threshold images. Only
`CP`-prefixed values are candidates, so unrelated numeric text cannot become CP.
Dry-run then prints the coordinate plan and marks the manifest incomplete with
`failed_step: "dry_run"`; it never calls an ADB input wrapper.

The ignored `debug/automation/` directory contains the initial PNG, page-state
JSON, and `plan.json`. It lets the operator review the fixed coordinates before
a live run. A wrong starting page becomes `unknown` and stops before any input.

## Step 3: Scroll once, then wait for the menu target state

A live run captures `summary.png`, confirms `detail_summary`, and sends the
configured upward swipe. The upper CP arc, model, and background may keep moving, so
this step does not use whole-screen pixel stability. It polls every 500 ms for up to
15 seconds and saves the first screenshot reliably classified as `detail_moves`
directly as `moves.png`. `detail_summary` and transient `unknown` keep waiting
without input; menu or appraisal states stop immediately. Only a timeout whose final
state is still `detail_summary` permits one second and final identical swipe. A final
`unknown` stops without retry. The existing anchored move OCR remains the first
signal. If the “道館對戰＆團體戰” heading is clipped, the fixed
Mate 30 detector also accepts a reliably OCR-read move name paired with its
right-side damage number on the same row. The heading raises confidence but is
not required; unrelated names, HP text, and appraisal labels do not form a move
row.

The bottom-right menu button remains fixed while the detail page scrolls, so the
workflow sends no reverse swipe. A fresh pre-click screenshot must classify as
`detail_moves` or `detail_summary`; any other result stops before input. The
program then taps `(1244, 2772)` once and polls every 500 ms for up to 10 seconds.
Only OCR-confirmed `action_menu` succeeds. Detail states keep waiting;
`appraisal_dialogue`, `appraisal_bars`, or `unknown` stop immediately. A timeout
allows exactly one second tap, but only after another fresh screenshot still
confirms one of the two detail states.

## Step 4: Appraisal has no guessed menu target

After the menu tap, OCR must find the exact Traditional Chinese text
`調查寶可夢` with the configured minimum confidence. The program clicks the
center of that OCR box, not a fixed appraisal-row coordinate. A target outside
the allowed row band, inside the lower transfer band, or too close to an OCR
`傳送` target is rejected.

Inside appraisal, the configured dialogue coordinate is used exactly once and
only while the screen is classified as `appraisal_dialogue`. After that tap, the
service polls every 500 ms for up to 30 seconds without sending more input.
`appraisal_dialogue` and `unknown` continue waiting; any other non-bar state stops
safely. `appraisal.png` is saved only after the existing OpenCV geometry detector
finds all three IV bars and classifies the screen as `appraisal_bars`.

## Step 5: Exit, recognize, or preserve an honest partial run

The center tap `(720, 1560)` is sent only from `appraisal_bars`. The returned screen must be a recognized
detail state before the existing calibrated reader runs and writes
`recognition.json`. The manifest becomes complete only after both safe exit and
recognition succeed.

Any state mismatch, ADB failure, stability timeout, IV failure, recognition
failure, or whole-flow timeout marks the manifest incomplete with the current
step when recovery storage remains available. Earlier final screenshots are not
deleted. Ctrl+C uses the same incomplete recovery attempt and then exits.

With `--debug`, every operation has raw screenshots, detection evidence, and
actual-coordinate JSON under `debug/automation/`. Move scrolling records
`before_scroll_to_moves_attempt_N.png`, every `*_poll_*.png`, and one
`*_states.json` timeline per attempt; it has no whole-screen stability file. Menu evidence
also includes `before_open_action_menu.png`, one `after_*`, `*_state.json`, and
`*_wait.json` file per attempt; the wait log records each detected state and its
elapsed time. These files and all scan artifacts remain below ignored `data/`.

## Verify the model

From the WSL project virtual environment:

```bash
pytest tests/test_automation.py tests/test_adb.py tests/test_scan_cli.py -q
ruff check .
mypy
```

The tests use synthetic PNGs, a fake detector, and fake ADB. Existing local
summary, moves, and appraisal images also confirmed the three corresponding
state detectors. A 2026-07-29 live Mate 30 run completed menu OCR, appraisal dialogue, IV
capture, safe exit, recognition, and a complete manifest. A later live resume on
熔蟻獸 reached `detail_moves` on the first 500 ms polling attempt with OCR confidence
0.99701 while producing no whole-screen scroll stability artifact. A saved real move-page
failure also confirms the no-anchor row fallback: `火花`/`10` and
`噴射火焰`/`65` classify as `detail_moves`, while a saved appraisal screen remains
`unknown` under the same expected state.

Continue to the [claim ledger and falsifying checks](../references/source-evidence.md)
for exact source, test, and runtime evidence.

Evidence status: The one-Pokémon path is source-, test-, and device-confirmed; the no-anchor detail-moves fallback is real-image-confirmed.
