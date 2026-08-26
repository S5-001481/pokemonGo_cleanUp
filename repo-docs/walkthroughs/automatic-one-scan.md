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

## Step 5: Exit, recognize, optionally rename, or preserve an honest partial run

The center tap `(720, 1560)` is sent only from `appraisal_bars`. The returned screen must be a recognized
detail state before the existing calibrated reader runs and writes
`recognition.json`. By default the phone is not renamed.

`scan-auto-one --rename-with-iv` is an explicit single-Pokémon extension. After
all three IVs are recognized, it verifies `detail_summary` and taps the fixed
center of the nickname row at `(720,1460)`. This opens the same editor without
depending on the pencil glyph or another name-row OCR pass. The fixed Huawei input method can cover the real
dialog controls, so the detector treats `設定暱稱` plus the right-side input-method
`確定` as a separate `rename_keyboard` state. After editing, it clicks the OCR box
center near `(1248,1712)` to close only the keyboard; it then requires both the
real `取消` and `OK` controls and clicks the OCR-derived dialog `OK` center near
`(719,1665)`.

The first pass sends `MOVE_END` plus 32 bounded delete keys and confirms the empty
nickname, which restores Pokémon GO's default Traditional Chinese species name.
The second pass sends `MOVE_END`, then passes the complete ASCII suffix such as
`15/14/15` to the existing ADB `input text` wrapper. Individual digit and slash
keyevents are not a half-width guarantee: the Huawei input method can turn those
events into `１５／１４／１５`. Before dismissing the final keyboard, OCR must
read one exact input candidate equal to the default name plus the requested
suffix. This comparison removes whitespace but deliberately does not apply NFKC,
so a wrong digit or any full-width digit/slash stops before the keyboard
confirmation and real `OK`. After `OK`, a separate wide name ROI
must reproduce the same character skeleton even when the rendered slashes are
missed. Successful verification atomically stores `renamed_summary.png` and
`nickname_change.json`. Both passes must return to `detail_summary` within 10
seconds. Missing IVs, an unrecognized keyboard/dialog, an exact-name mismatch,
or a failed return leaves the scan incomplete; no ungated input follows.

The first physical run after switching back to `input text`, scan
`20260826_102152_537806_e166da7687744511bd0c9ef7d8534f99`, recognized `腕力`,
CP750, and IV `10/14/4`. Its action log contains one literal
`input_text("10/14/4")` request, but the next raw editor OCR candidate is
`腕力10／14／4`. The phone's active keyboard is Gboard in its Pinyin layout, as
shown by the `拼音` spacebar. Android 10 on this device exposes no shell command
implementation for `cmd clipboard` or `cmd input`, so neither ordinary keyevents
nor the existing text wrapper bypass that layout's slash conversion. The
width-sensitive gate correctly sent neither the keyboard confirmation nor the
game `OK`; a verified English-layout transition and restoration path is still
needed for automatic layout handling. The chosen operating procedure is instead
to switch Gboard to English manually before starting any rename-enabled single or
batch scan. The program does not change or restore the user's keyboard layout.

`scan-batch --rename-with-iv` reuses this one-scan transaction, then adds its own
unchanged-CP/HP/static-fingerprint transition check before persisting a CSV row
or switching. The generic summary-name OCR remains unchanged.

The first device run of this option on 2026-08-25 exposed a calibration defect
before any text input: the configured `(932,1690)` tap landed roughly 230 pixels
below the visible pencil, whose screenshot center is approximately `(932,1460)`.
Both post-tap samples remained `detail_summary`, so the service timed out, wrote
`failed_step: "rename_with_iv"`, and sent no delete or text commands. That failure
supplied the corrected pencil center. A later retry opened the editor but showed
that the Huawei keyboard obscures the real controls; the operator confirmed that
its right-side `確定` closes the keyboard. After modeling that state, scan
`20260825_150806_710137_981f9622c35648f8bb33eee96d48fb2a` completed both rename
passes and produced `呆火駝15/14/15`. A final rerun
`20260825_151358_160055_60784be4263347e3856fc959f66504d3` replaced string input
with explicit digit/slash keycodes and appeared to complete with a half-width
suffix. That implementation was later corrected after the phone showed that the
input method can convert those keyevents to full-width characters; normalized OCR
had hidden the distinction.
Recognition still reads the pre-rename `summary.png`: because that screen already
contained the earlier IV suffix, this final corrective run stored `pokemon_name:
"14"` even though the post-rename detail screen visibly shows
`呆火駝15/14/15`. Renaming does not rewrite `recognition.json`; consumers that
rescan already-suffixed names must treat that name field as a known OCR caveat.
The new wide-name reader replays both saved final screenshots as the expected
`呆火駝151415` skeleton without changing the generic reader.

A later batch item exposed a second pencil-target boundary. Scan
`20260825_184130_484579_50c2bdf9239a40448821133295ba7a0d` recognized
`拉魯拉絲`, CP140, and IV `11/4/1`, then returned safely from appraisal. Its
four-character name OCR box ended at x=934, while the fixed `(932,1460)` tap
landed inside the final name glyph rather than on the pencil that had shifted to
the right. The following state was still `detail_summary`, so the editor gate
timed out with no delete or text input. Earlier successful three-character names
ended around x=886–891, which explains why the same fixed x worked for them.
The repair at that point implemented an OCR-name-box-relative target. Saved-image replay
derives `(982,1454)` for the failed four-character example, approximately
`(933–937,1454–1456)` for the preceding three-character names, and
`(1119–1146,1454–1456)` for saved long suffixed names. Synthetic tests also
require zero pencil input when candidates are low-confidence or the derived
point leaves the allowed lane. A post-fix physical tap remains pending.

That physical retry later reached scan
`20260825_191305_543577_3527e89a255146e5b40b0a2332f86386`, whose existing
nickname `泥巴魚13/14/10` filled the row. The implementation derived
`(1146,1453)`, but its source `NAME_RECT` stops at x=1100 and clipped the final
`10` token. The tap landed between the text and pencil, and the page stayed
`detail_summary`; no delete or text input followed. Replaying the same screenshot
through the already-existing wider `(150,1300,1290,1550)` name row preserves the
final token through x=1144 and derives `(1190,1453)` at the visible pencil. This
The target source at that point used the wider name row and returned `(1190,1453)` for the
saved failure. More importantly, this rename
attempt was redundant: the current CP903/static fingerprint exactly matches
already-verified batch row 2. The batch had first revisited row 1, but its same
long nickname was generically OCR-read as `"2"` in row 1 and `"5"` on revisit;
the name-dependent first-row wrap check therefore missed an otherwise exact
CP/fingerprint repeat and allowed row 6. It then advanced to the already-renamed
row-2 Pokémon. The clipped pencil target explains the final local failure, while
the missed renamed-wrap identity explains why an already-correct nickname was
being edited at all.

The renamed-wrap path is now separate from the unchanged generic switch
comparator: it requires the current wide nickname to equal the first row's saved
expected nickname while CP, optional HP, and the distance-8 static fingerprint
match. Thus generic fragments may differ, but a missing/wrong wide nickname or
any immutable-identity change still refuses the wrap.

A later English-layout run, scan
`20260826_104349_261861_15115ad2f606423a8064257e0d1e1b4c`, completed the scan and
recognized `古月鳥`, CP121, and IV `15/15/13`. The first wide-row pencil lookup
returned `(943,1456)`, and the reset-to-default pass completed. Afterward the
general summary gate still read `CP121`, `古月鳥`, and `110/110HP`, but the
second independent wide-row lookup returned no bounded pencil target. The action
log therefore ends after `confirm_default_nickname`: no second pencil tap, IV
text, keyboard confirmation, or game `OK` was sent. Two immediately preceding
English-layout scans completed their rename transactions, so this evidence points
to a transient wide-row OCR false negative after reset, not a keyboard-width or
wrong-name failure. The missing post-reset screenshot prevents a narrower pixel
cause from being proven from saved artifacts.

The current implementation supersedes both pencil-target approaches: the user
confirmed that tapping the middle of the name row also opens the editor, so both
passes now use fixed `(720,1460)`. The pre-tap `detail_summary` gate and post-tap
`rename_keyboard`/`rename_dialog` gate remain mandatory. Thus animated name-row
OCR can no longer block the edit tap, while an unexpected page or a tap that does
not open the editor still stops before delete or text input.

The manifest becomes complete only after safe exit, recognition, and any enabled
rename sequence succeed.

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
