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

Summary OCR is progressive both within and after the ordinary preprocessing set.
CP and name each start with the cheapest 2x color crop, then run sharpened 3x and
CLAHE 4x OCR only while the accumulated result is still insufficient. CP may stop
inside that set only when the best candidate retains the `CP` prefix, meets the
unchanged confidence threshold, has a positive value, and is centered in the
existing CP text band. Name may stop only when the current variant has one
confident Chinese-bearing name token of at least two Chinese characters and that
token is also the accumulated best candidate. A single `怒`, or one variant that
splits `怒` and `鸚哥`, therefore continues to the next variant. If no ordinary
variant reaches those early-stop conditions, all three still run and retain the
same final best-candidate rules. A valid name plus `CP`-prefixed value returns
`detail_summary` immediately. Only when the name succeeds but the ordinary CP set
has no valid prefixed value does it run one
HSV near-white mask. This preserves the white CP text while suppressing saturated
gold/event artwork. A prefixed candidate in the expected CP band is preferred;
bare digits are promoted to CP only with confidence at least 0.95 and the wide,
tall geometry of the complete CP label. Only an HSV miss runs grayscale, CLAHE,
Otsu, inverted Otsu, adaptive threshold, and inverted adaptive threshold; only a
total CP miss OCRs the fixed HP rectangle. A missing name stops classification
without useless fallback. Final `read_scan` uses the same progressive order, so
later threshold artifacts cannot overwrite valid ordinary or HSV CP. Unrelated
background numbers remain rejected.
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

Final move-name recognition keeps its original `(100,1100,1340,2320)` anchor
path first. Shadow and Dynamax layouts can shift the “道館對戰＆團體戰” heading
above y=1100. Only after the normal anchor misses, a dedicated
`(100,800,1340,1800)` branch runs. It requires an upper move-section heading plus
exactly one explicit special-layout marker: `暗影獎勵` for Shadow or `極巨招式`
for Dynamax. Neither marker, or an ambiguous crop containing both, fails closed.
Modifier and Max Move labels remain UI labels and are filtered out of the regular
move list. Accepted results add `类型：暗影` or `类型：极巨化` to warnings, which
is also the existing CSV remarks field. Ordinary Pokémon never pay for the
fallback OCR.

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

After that click, appraisal entry is target-state polling rather than a fixed
delay or whole-screen stability wait. The IV-bar geometry remains the strongest
direct success signal. Otherwise a high-confidence OCR hit containing `你好` in
the fixed bottom-left ROI `(80,2450,420,2615)` is explicit positive evidence for
`appraisal_dialogue`; the existing wider dialogue-keyword detector and the
four-consecutive-unknown fallback remain available if that small ROI cannot be
read. A still-visible `action_menu` only keeps polling and never causes another
menu-row click.

Once the greeting is found, the configured dialogue coordinate `(1120,1660)` is
used immediately and exactly once. It is sent only while the screen is classified
as `appraisal_dialogue`. After that tap, the
service polls every 500 ms for up to 30 seconds without sending more input.
`appraisal_dialogue` and `unknown` continue waiting; any other non-bar state stops
safely. `appraisal.png` is saved only after the existing OpenCV geometry detector
finds all three IV bars and classifies the screen as `appraisal_bars`. The normal
geometry search remains `(171..665, 2150..2700)`. Only its miss retries the same
row-height, 125–150 px spacing, HSV endpoint, and quantization rules with the top
extended upward to y=2100; that fallback must yield exactly one legal three-row
sequence. It does not loosen bar dimensions or use the visible attack/defense/HP
labels as IV values. Debug state records `iv_geometry_path` as `standard` or
`upward_fallback`.

Saved scan `20260827_224510_336106_4d6062a9fed44d7eb678fa76bcea62f2`
motivated this retry. Its 来悲茶 card is shifted upward: attack, defense, and HP
start at y=2136/2269/2400. The old y=2150 top clipped attack to 17 pixels and
discarded it below the unchanged 20-pixel minimum. Upward replay of all nine wait
frames returns exact IV `14/11/14`; the user confirmed HP14. The action menu,
pre-tap dialogue, and four entry frames from the same run remain negative.

## Step 5: Exit, recognize, optionally rename, or preserve an honest partial run

The center tap `(720, 1560)` is sent only from `appraisal_bars`; its coordinate and
single-tap behavior are unchanged. The return wait uses a dedicated lightweight
detector instead of ordinary name/CP/moves OCR. Every 500 ms it first requires the
existing IV-bar geometry to be absent, then inspects only small fixed ROIs around
the detail page's close button `(720,2772)` and menu button `(1244,2772)`. Both
ROIs must contain the calibrated teal disk and a sufficiently non-teal surrounding
ring, proving two page-specific circular controls rather than a teal overlay.
Visible IV bars, black frames, appraisal dialogue, a full-teal action menu, and an
incomplete one-button animation frame remain `unknown`; polling continues and no
input is sent. Normal polling calls no RapidOCR. If 15 seconds expires, the final
PNG and state evidence are preserved and that last frame alone goes through the
existing full detector once for diagnosis; the run still fails without another
tap. A lightweight success only proves safe return from appraisal and does not
change or replace any later identity comparison. The existing calibrated reader
then writes `recognition.json`. In an automatic live scan only, the summary,
moves, and appraisal detectors retain the exact candidates, warnings, IV bars,
and debug crops they already produced, together with a SHA-256 of each captured
PNG byte string. After all three PNGs are atomically saved, the reader hashes the
final files again. Complete evidence with three matching hashes constructs the
same `RecognitionResult` and debug images without decoding or recognizing those
screens a second time. Missing name/CP, move, or IV evidence, a move-page row
fallback that did not produce final move candidates, an unreliable ordinary CP,
a missing file, or any hash mismatch declines this path and calls the unchanged
`read_scan()` implementation. Historical, CLI, guided, manual, replay, and debug
reading therefore retain their existing path and fail-closed behavior. By default
the phone is not renamed.

`scan-auto-one --rename-with-iv` is an explicit single-Pokémon extension. After
all three IVs are recognized, it verifies `detail_summary` and taps the fixed
center of the nickname row at `(720,1460)`. This opens the same editor without
depending on the pencil glyph or another name-row OCR pass. The fixed Huawei input method can cover the real
dialog controls, so the detector treats `設定暱稱` plus the right-side input-method
`確定` as a separate `rename_keyboard` state. After editing, it clicks the OCR box
center near `(1248,1712)` to close only the keyboard; it then requires both the
real `取消` and `OK` controls and clicks the OCR-derived dialog `OK` center near
`(719,1665)`.

The first editor pre-state also exposes a conditional CP checkpoint. When both
the persisted recognition CP and that checkpoint CP exist and agree, the normal
path taps immediately and performs no CP-only retry. If they disagree, the page
is left untouched and at most six additional screenshots are read through the
existing prefixed CP-only reader at 500 ms intervals. A CP must appear completely
and identically in at least two of those frames before it replaces the baseline
in the in-memory result and atomically rewritten `recognition.json`. These retry
frames deliberately do not repeat name/nickname, HP, static-fingerprint, or full
page detection: the page identity/state was gated once immediately before the
bounded no-input burst. No repeated CP means failure before the nickname-row tap.

The first pass sends `MOVE_END` plus 32 bounded delete keys and confirms the empty
nickname, which restores Pokémon GO's default Traditional Chinese species name.
The returned screen must still pass the ordinary `detail_summary` page gate, but
its generic highest-confidence name token is no longer used to construct the
expected nickname. The dedicated wide nickname row `(150,1300,1290,1550)` reads
the restored default name without narrowing the long-name boundary. It anchors
the row on the tallest/highest-confidence candidate containing Chinese; the
no-Chinese fallback prefers a tall candidate near y=1450. Only candidates within
70 pixels of the anchor center and at least 40% of its height are joined from
left to right, so a split such as `怒` plus `鸚哥` becomes `怒鸚哥` while smaller
or vertically displaced UI evidence is excluded. An empty result stops before
the second editor opening and IV input, and debug mode records
`verified_default_nickname_wide` evidence.

Saved scan `20260826_191654_239673_757c091e92d0461d9a31ab735dca6cda`
exposed the false-positive boundary that motivated this filter. The restored
screen visibly shows `哈力栗`, but the ROI also clips the acquisition-date badge
at the far right. Pre-fix OCR returned `哈力栗` at `(540,1374,895,1536)` plus a
spurious `1` at `(1259,1325,1289,1377)`, constructing `哈力栗1`; the exact editor
gate then safely rejected correct `哈力栗14/11/12`. Current saved-image replay
returns only `哈力栗`. A separate completed `古月鳥15/15/13` screenshot retains
the full same-row IV suffix, confirming the filter does not solve the date badge
by shrinking the ROI or deleting digits.

The second pass appends three circled IV values in attack/defense/HP order,
for example `超梦⑭⑭⑮` and `赫拉克羅斯⑮⑭⑬`. Zero is `⓪`. Each IV
occupies one Unicode character, and the complete name must fit the game's
12-character limit. Overlong results fail before reopening the editor without
truncating the name or falling back to slash/compact ASCII.

The new Unicode input route requires an installed, enabled
[ADB Keyboard](https://github.com/senzhk/ADBKeyBoard). Before any rename-enabled
live scan captures or changes the phone, the ADB client checks this prerequisite.
It never installs or enables an input method itself. When the suffix is ready,
it records the current IME, temporarily selects ADB Keyboard, confirms that
selection, waits for matching `mCurId`, `mBoundToMethod=true` and
`mInputShown=true` (at most ten state polls), and sends one package-targeted
`ADB_INPUT_B64` broadcast carrying UTF-8/Base64. A `finally` block restores and
checks the original IME even if broadcasting fails or is interrupted. The restored
IME must also be bound and shown before control returns to confirmation OCR. The original
keyboard is therefore active for the existing confirmation OCR; no manual English
layout switch is needed for the suffix.

Broadcast completion only proves delivery, so editor OCR must still read the
exact expected nickname before either confirmation. The final wide-name reader,
generic name recognition and batch name comparisons preserve circles instead of
NFKC-folding them to ASCII. An OCR reading such as `超梦141415` cannot prove
`超梦⑭⑭⑮`. Successful full scans persist their usual rename evidence;
IV-only naming retains the no-file contract. New format and input recovery are
covered by synthetic tests. A complete live run on 2026-09-07 verified
`向日種子⑮⑭⑩` (CP164), restored Gboard, created zero scan files and left
CSV files unchanged; arbitrary other names/devices are not implied by this sample.

### Reading circled IVs from the screen

A physical `向日種子⑮⑭⑩` page exposed a separate OCR problem: whole-row
recognition produced `向日種子151410` or `向日種子⑤14⑩`, and the editor
produced `向日種子1⑤14⑩`. The circles were already correctly rendered on the
phone. UI Automator exposed only the Unity surface, without a native editable
text node, so no system-text shortcut was available on this device.

The dedicated nickname reader now first finds exactly three adjacent closed
circular outlines inside the calibrated nickname rectangle. It checks circularity,
inner-hole area, ring size, alignment and spacing. Each interior is read at 4x
scale with two different insets; both readings must agree on one integer from
0 through 15 with at least 0.90 confidence. The name to their left is read
separately. Only that measured ring-and-number evidence constructs the three
Unicode circle characters. No expected nickname is used to infer missing digits.
Missing rings, inconsistent crops, low confidence, or out-of-range values decline
this branch. Existing exact editor/final-name comparisons remain mandatory.

Both editor and wide-summary readers use this path, including batch's wide-name
checks. Debug editor state identifies `nickname_evidence: circled_geometry_ocr`.
Synthetic tests cover every IV value, ordinary digits without rings, missing or
extra rings, misalignment, conflicting interior readings and low confidence.
Replaying the physical editor and summary recovered exact `向日種子⑮⑭⑩`.
The first subsequent live run passed the circle text check but exposed a Gboard
restore/confirmation race. Waiting for the selected IME to bind and show resolved
it; the next complete live run passed editor and final-summary verification.

The following incidents describe the historical slash/compact formats and explain
the retained length, editor and summary guards; they are not the current suffix
selection rule.

Scan `20260827_173005_868327_3f08c68fc4aa4f36bb15cc06ed9e6e77`
exposed the nickname-length boundary that led to the compact fallback. The five-character default name
`赫拉克羅斯` plus the eight-character suffix `15/14/13` requires 13 characters,
but the editor evidence contained only the first 12,
`赫拉克羅斯15/14/1`. Exact verification therefore failed at
`rename_confirm_iv` before either confirmation tap. The reset-to-default pass had
already completed, but neither `nickname_change.json` nor `renamed_summary.png`
was written, and the truncated candidate was not committed. The configured value
`nickname_maximum_characters: 32` is only the bounded delete-key count; it is not
a promise about the game's accepted nickname length. The implementation now
selects the 11-character `赫拉克羅斯151413` before reopening the editor. The
original exact editor check remains mandatory: a truncated candidate such as
`赫拉克羅斯15141` is rejected before either confirmation tap.

Physical scan `20260827_183050_601615_f2fd7e7d971f40409053a93b0619bf9c`
confirmed the compact transaction itself: ADB sent `151413`, editor OCR returned
the sole complete candidate `赫拉克羅斯151413`, both confirmations completed, and
the final summary matched `CP1758` plus the same complete nickname. The scan
manifest and nickname evidence are complete. Its initial summary had already been
left with nickname `15/14/13` before this run, so `recognition.json.pokemon_name`
records that pre-run nickname rather than the species name; that separate input
state does not weaken the compact editor equality evidence.

Scan `20260827_213607_150274_116b76f50f9f40e49bce607ab66a2a3f`
motivated the conditional CP checkpoint. The visible value is CP997, while its
saved summary/checkpoint/final frames OCR as CP67, CP97, CP997, and CP99 as bubbles
cover different digits. The rename itself completed exactly as
`湧躍鴨12/15/15`, but batch correctly rejected the 67-to-99 transition. That old
run did not capture the new CP-only burst, so it is diagnostic evidence rather
than a replay proof of two independent matching frames.

Physical scan `20260827_185253_573760_81b3c80b7b094ff29dd3c90e31790069`
confirmed a separate final-wide OCR false negative. The editor exactly verified
`一對鼠14/15/15`, both confirmation taps completed, and the saved final frame
visibly contains that nickname. Generic summary OCR on the same frame also returns
the complete value, but the dedicated wide-row OCR reproducibly reads
`-對鼠14/15/15`, confusing the first `一` with ASCII `-`. The final comparison
originally failed closed and wrote no nickname evidence or CSV row and sent no
switch. Final verification now keeps that sharpened 1.5x wide-row pass first and
returns immediately on an existing skeleton match. Only a mismatch runs one raw
color 2.0x OCR pass over the same ROI with the same geometry filter and token
joining. Saved-real replay retains the sharpened `-對鼠14/15/15` result but the
raw pass returns exact `一對鼠14/15/15`, so the unchanged final comparison succeeds.
RapidOCR's nearby empty-detection warning was not itself the equality failure.

A later animation frame in scan
`20260827_211507_528118_e137f8b37b254daf8616110674bccb73`
showed the limit of the original 1.5x fallback. The editor and final generic summary
both contain `一對鼠15/14/12`, but sharpened 1.5x and raw color 1.5x each
reproducibly return `-對鼠15/14/12`; progressive verification therefore still
failed closed. The final-only raw fallback now uses 2.0x, which returns exact
`一對鼠15/14/12` on this saved frame. Both saved 一對鼠 frames replay successfully;
the sharpened first pass and all non-final-wide OCR scales remain unchanged.

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
needed for automatic layout handling. The historical operating procedure was to switch Gboard to English manually.
The current circled-number route above replaces that workaround with temporary
Unicode IME selection and restoration.

`scan-batch --rename-with-iv` reuses this one-scan transaction, then adds its own
unchanged-CP/HP/static-fingerprint transition check before persisting a CSV row
or switching. Its post-scan evidence check rebuilds the circled nickname from the
saved default name and recognized IVs and requires exact equality. Existing CSV
rows retain their recorded nickname and are not rewritten into the new format.
Name normalization now preserves circles; the CP/HP/fingerprint gates remain.

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
stored an OCR result of `古月鳥`, CP121, and IV `15/15/13`; the user later
confirmed that its real CP is 1217. The ordinary CP variants consistently read
`CP1217`, while the old unconditional threshold pass produced a higher-confidence
but wrong `CP121` and overwrote it. The first wide-row pencil lookup returned
`(943,1456)`, and the reset-to-default pass completed. Afterward the old general
summary gate also reported `CP121`, `古月鳥`, and `110/110HP`, but the
second independent wide-row lookup returned no bounded pencil target. The action
log therefore ends after `confirm_default_nickname`: no second pencil tap, IV
text, keyboard confirmation, or game `OK` was sent. Two immediately preceding
English-layout scans completed their rename transactions, so this evidence points
to a transient wide-row OCR false negative after reset, not a keyboard-width or
wrong-name failure. The missing post-reset screenshot prevents a narrower pixel
cause from being proven from saved artifacts.

The progressive CP path corrects future recognition of that saved summary to
`CP1217` and finishes CP OCR after the three ordinary candidates. It does not
rewrite the historical local `recognition.json` or CSV row automatically.

The current implementation supersedes both pencil-target approaches: the user
confirmed that tapping the middle of the name row also opens the editor, so both
passes now use fixed `(720,1460)`. The pre-tap `detail_summary` gate and post-tap
`rename_keyboard`/`rename_dialog` gate remain mandatory. Thus animated name-row
OCR can no longer block the edit tap, while an unexpected page or a tap that does
not open the editor still stops before delete or text input.

The manifest becomes complete only after safe exit, recognition, and any enabled
rename sequence succeed.

Any state mismatch, ADB failure, target-state timeout, IV failure, recognition
failure, or whole-flow timeout marks the manifest incomplete with the current
step when recovery storage remains available. Earlier final screenshots are not
deleted. Ctrl+C uses the same incomplete recovery attempt and then exits.

With `--debug`, every operation has raw screenshots, detection evidence, and
actual-coordinate JSON under `debug/automation/`. Move scrolling records
`before_scroll_to_moves_attempt_N.png`, every `*_poll_*.png`, and one
`*_states.json` timeline per attempt; it has no whole-screen stability file. Menu evidence
also includes `before_open_action_menu.png`, one `after_*`, `*_state.json`, and
`*_wait.json` file per attempt; the wait log records each detected state and its
elapsed time. `timings.json` uses the injected monotonic clock to record a flat,
ordered duration for initialization, page verification, captures, navigation,
recognition, finalization, and each rename substep. It is finalized as
`complete`, `failed`, `interrupted`, or `dry_run`; a step that raises is retained
with outcome `failed`. Every step and the top level also record OCR-engine time
and `stable_wait_seconds`, which is zero because no production transition uses
full-screen stability. These files and all scan artifacts remain below ignored
`data/`.

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

The first appraisal-entry frame from scan
`20260826_123322_191081_f91f29908297412f8f2232bc650c4945` contains `你好` at the
bottom left. Replaying it through the fixed greeting ROI returned
`appraisal_dialogue` at confidence 0.99997 in 0.50 seconds. The older generic
dialogue detector missed the greeting and the live profile spent 13.39 seconds in
the entry wait before taking the inferred-dialogue path.

Continue to the [claim ledger and falsifying checks](../references/source-evidence.md)
for exact source, test, and runtime evidence.

Evidence status: The one-Pokémon path is source-, test-, and device-confirmed; the no-anchor detail-moves fallback is real-image-confirmed.


## IV naming without a saved scan

The GUI's **扫描 IV 并命名** button launches `rename-iv-one` for the currently
open detail-summary page. It uses the same device/resolution checks, OCR-targeted
appraisal menu, appraisal dialogue/exit gates, default-name restoration, CP
consensus, and exact editor/final nickname checks as the full scan. Enabled
ADB Keyboard is now a prerequisite for circled IV input. No next-Pokemon swipe runs.

The dedicated [service entry](../../src/pokemon_go_cleanup/automation.py) skips
move scrolling, move capture, and the three-view recognition reader. It builds an
in-memory result from summary OCR and appraisal bar evidence whose hashes must
match the captured frames; missing or mismatched evidence stops before the first
nickname-editor tap. The default name plus three circled IVs must fit 12 characters.

This session disables persistence from initialization through success, failure,
and interruption. It creates no scan directory or manifest and saves no PNG,
recognition JSON, nickname evidence, debug profile, or CSV. Screenshots and rename
evidence are held in memory. Therefore it cannot be resumed from a CSV or used as
a complete three-view dataset scan. The CLI prints the verified final nickname;
failures use exit 15 and Ctrl+C uses 130 without claiming files were saved.

The GUI ignores all batch/CSV/debug settings for this button. A run resets the
counter and timer, successful exit 0 counts one, and failure/Stop freezes the
elapsed duration without counting success. The button is disabled while any task
runs. [Automation tests](../../tests/test_automation.py) forbid all file writes
and full-reader calls, check no swipe occurs, reject missing/mismatched IV evidence
and incorrect/full-width editor text, and cover interruption. CLI and GUI tests
verify routing, exit handling, counting, and timer completion. Physical execution
passed on 2026-09-07 for `向日種子⑮⑭⑩`, including the no-file contract.
