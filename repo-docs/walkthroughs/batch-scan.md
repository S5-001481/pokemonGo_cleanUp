# Bounded batch scanning on Huawei Mate 30

This walkthrough follows `pokemon-go-cleanup scan-batch` from an already-open
first Pokémon detail summary. The batch layer delegates every Pokémon to the
existing `AutoScanService`; it does not duplicate or alter the proven menu,
appraisal, IV, exit, or recognition sequence.

Use `pokemon-go-cleanup scan-batch --limit 5 --csv inventory-rename.csv
--rename-with-iv --debug` for a new opt-in rename batch. Add `--resume` only when
continuing that same verified-rename CSV, and keep the checkbox/flag consistent.

## Step 1: Establish one durable destination

`--limit` is a positive total-row ceiling, defaulting to 20. The CSV path is
resolved locally. An existing destination is rejected unless `--resume` is
explicit; resume parses the exact header, rejects duplicate scan IDs, and checks
that every referenced `manifest.json` is valid, matches its scan ID, and remains
`complete`.

The CSV includes the requested recognition fields, absolute scan directory, and
a 256-bit perceptual fingerprint. The current Mate 30 crop is `(100, 1550, 1340,
2300)`: HP through the static detail fields, excluding the CP arc, model, and upper
animated background. Root CSV exports, all scan directories, recognition JSON, and
debug files remain ignored by Git.

New batch CSVs use an exact 16-column contract beginning with `batch_index` and
including `page_fingerprint`, `scan_directory`, `nickname_before`,
`nickname_after`, and `rename_status`. The loader still accepts the prior valid
13-column batch header as `rename_status: not_requested`; it can only be resumed
without `--rename-with-iv`. An older recognition
export such as the 10-column `scan_id,pokemon_name,cp,...,warnings` inventory is
not resumable: it lacks the screenshot reference and fingerprint needed for the
same-Pokémon guard. Do not add blank columns to bypass this check. Start a new
batch CSV, or migrate only when every referenced complete scan directory and its
`summary.png` still exist so the missing evidence can be recomputed. `--limit` is
the total row ceiling; to append five rows to a valid 90-row batch, use 95 rather
than 5.

## Step 2: Reuse the complete one-scan transaction

For each item, `BatchScanService` calls `AutoScanService.scan_one()` with the
same selected serial, debug flag, and optional `--rename-with-iv`. A batch row is
eligible only after that call returns a complete manifest and recognition result.
Without renaming, it reads the already-saved `summary.png`, builds the row, and
atomically rewrites the CSV immediately. The next phone input cannot erase or
defer that durable row.

With `--rename-with-iv`, the one-scan layer also persists
`renamed_summary.png` and `nickname_change.json`. The batch layer independently
checks their exact editor nickname, wide summary-name evidence, unchanged CP,
optional HP, and the existing distance-8 static fingerprint before writing a row
with `rename_status: verified`. A failed transition writes no CSV row and sends
no horizontal swipe.

On `--resume`, the service reads the last complete row and its referenced
summary artifact, captures the current phone page, and first requires
`detail_summary`. Normal rows compare against `summary.png` exactly as before.
Verified rename rows restore the post-edit identity from `renamed_summary.png`,
require the saved expected nickname through the wide name reader, and then reuse
the unchanged strict name/CP/fingerprint comparison. Matching rows run the
existing bounded next-Pokémon switch before scanning. If CP/HP/fingerprint still
look like the last Pokémon but its nickname is neither the verified value nor a
strict identity match, resume stops as ambiguous with no input.

Immediately before append, the new summary is compared once more only with the last
CSV row. A highly similar static ROI plus matching auxiliary identity refuses the
append even though the new scan directory differs. A single-scan exception or this
adjacent-duplicate guard stops the batch. Ctrl+C keeps earlier CSV rows and relies
on the current one-scan recovery path for any in-progress manifest.

## Step 3: Gate the only new gesture

After the complete one-scan flow, the batch layer captures a fresh screen and
requires `detail_summary`. A normal row uses the original summary identity. A
verified rename row uses the post-edit generic OCR identity returned with
`renamed_summary.png`; the existing pre-swipe name/CP/fingerprint function remains
unchanged and must match that baseline before any switch input is sent.

The fixed Mate 30 batch profile performs one left swipe from `(1180, 1500)` to
`(260, 1500)` over 600 ms. If the page is still the same after the bounded wait,
the only retry is a stronger left swipe from `(1300, 1500)` to `(140, 1500)` over
850 ms. Both y-coordinates remain far above the bottom menu and transfer band;
there is no right-swipe fallback. The profile remains intentionally limited to
1440x3120 Traditional Chinese.

## Step 4: Prove that the page changed

After a swipe, the service captures and classifies every 500 ms for at most 15
seconds. A transient `unknown` may continue during animation. Any recognized
menu, moves, or appraisal state stops immediately. A `detail_summary` is accepted
only when at least one of OCR name, CP, or the static page fingerprint differs
from the previous Pokémon.

If the summary stays the same through the first timeout, one fresh screenshot
must still confirm that same detail summary before the second and final stronger
left swipe. Two unchanged attempts report a possible list end or ineffective
gesture and stop safely instead of scanning the same Pokémon forever.

When the changed identity matches the first row's name and CP, the service stops
with `wrapped_to_first` before scanning it again. Otherwise the next loop invokes
the unchanged one-scan service.

The first physical rename cycle exposed a fail-closed limitation in that rule:
row 1 and the revisited row 6 both had CP175 and an identical distance-0 static
fingerprint, but generic OCR returned different fragments (`"2"` and `"5"`) from
the same long `飄飄球12/2/5` nickname. Because `_same_switch_identity` still
requires exact generic name equality, the batch missed the wrap, appended the
duplicate row 6, and advanced to the already-verified row-2 泥巴魚. The dedicated
full nickname evidence was not consulted by this wrap branch. The renamed wrap
branch now uses the first row's verified expected nickname through the wide-name
reader plus unchanged CP, optional HP, and static fingerprint. The general
`_same_switch_identity` comparator remains unchanged for non-rename switching.

### Nickname-mutation boundary

Batch renaming is opt-in through `scan-batch --rename-with-iv`. The static fingerprint does not contain
the visible nickname: its fixed crop starts at y=1550, below the rendered name,
and two device-confirmed before/after nickname pairs produced identical hashes
(Hamming distance 0). Nickname is nevertheless a mandatory field outside that
hash: the current page-identity, adjacent-row, resume, pre-swipe, and wrap guards
require the OCR name to match alongside CP and the fingerprint (and resume also
uses HP when both sides provide it).

The implementation does not make those general-purpose guards name-agnostic or
raise their existing fingerprint tolerance. A separate, narrowly scoped
expected-nickname-transition check requires width-sensitive editor evidence to
match the exact planned value (with half-width digits and punctuation), while CP, optional HP,
and the existing static fingerprint continue to identify the same Pokémon. The
generic summary name crop is not sufficient evidence for a long renamed value:
both live `呆火駝15/14/15` confirmations were read as only `14`, so this path
uses the full editor value before `OK` plus a separate wide final-summary ROI.
The first live batch rename exposed a crop-dependent OCR false negative: the
saved summary visibly contained `飄飄球12/2/5`, but `(150,1250,1290,1600)`
returned only `飄飄球12` and `5`. The dedicated crop is now tightened to the
actual name row `(150,1300,1290,1550)`; both saved failures return the ordered
tokens `飄飄球12`, `2`, `5` and therefore the exact slash-insensitive skeleton
`飄飄球1225`. This changes neither generic summary OCR nor any CP, HP,
fingerprint, switch, or resume comparator.
Only after that transition is
verified may the post-edit identity become the baseline passed to the unchanged
next-Pokémon switch. Resume data preserves the planned/post-edit nickname so
that a completed rename can be distinguished from an already-advanced page; an
ambiguous transition stops rather than being treated as either case.

## Step 5: Inspect evidence and stop reasons

With `--debug`, each scan keeps its existing `debug/automation/` evidence. The
preceding scan also receives `debug/batch/` with the pre-switch screenshot,
coordinates, per-sample screenshots, OCR state/identity timeline, and final
attempt state. Resume debug evidence adds `resume_current.png`, both static ROI
PNGs, the last summary path, both hashes, Hamming distance, OCR auxiliaries,
`same_as_last`, and whether the pre-scan switch executed.

Normal completion reports `limit_reached` or `wrapped_to_first`. Page mismatch,
two failed switches, malformed resume data, a single-scan failure, or Ctrl+C
stops without deleting complete scans or previously written rows.

## Verification boundary

Synthetic tests cover two distinct scans, both exact left-swipe gestures, the
`start.x > end.x` invariant with no reverse fallback, atomic CSV persistence,
summary-before-IV classification, debug output, the two-attempt
ceiling, resume-on-last pre-switching, already-next no-switch behavior, upper-screen
animation exclusion, the final adjacent-duplicate append guard, verified rename
success/failure, exact editor mismatch, post-rename resume, ambiguous-name zero
input, legacy schema loading, and rename-mode consistency. Both real
`呆火駝15/14/15` final screenshots produce the expected `呆火駝151415` nickname
skeleton. The two first live batch attempts both saved the correctly renamed
`飄飄球12/2/5` summary, then stopped before CSV append and before left swipe when
the old crop missed the middle `2`; both screenshots replay as the expected
`飄飄球1225` after the crop correction. Physical testing saved 搗蛋小妖 CP318 as the first atomic row
and switched to a reliably OCR-identified 睡睡菇 CP431 summary. The first attempt at the second one-scan run stopped when its move-section
heading fell just above the old OCR crop, so the batch correctly preserved one row
and sent no further switch. The fixed Mate 30 `detail_moves` gate now accepts a
move-name/damage-number row without requiring that heading; saved real move and
appraisal screens confirm the positive and negative boundaries. A resumed live run then scanned 睡睡菇 CP431 from the current page, saved a
second complete manifest and recognition, atomically appended row 2, and stopped at
`limit_reached`. The preserved first row remained 搗蛋小妖 CP318, so the final CSV
had exactly two distinct scan IDs and two distinct name/CP identities. A later
bounded `--resume --limit 3` started while the phone was already on 熔蟻獸 CP1591.
The static ROI distance from the last 睡睡菇 row was 60 against an 8 threshold, so
resume correctly sent no preliminary horizontal swipe and scanned the current page.
That single scan later stopped in the unchanged appraisal-dialogue limit; the CSV
therefore remained at two rows, proving failure preservation without another batch
run.

Continue to the [source evidence and falsifying checks](../references/source-evidence.md) for the exact implementation and test anchors.

Evidence status: Two-row batch persistence, resume, left switching, the repaired
move gate, and bounded stopping are source-, test-, and device-confirmed. The
first live batch rename reached and saved the correct mutation, then demonstrated
the no-row/no-swipe failure boundary. The corrected final-name read is
saved-real-image-confirmed; a post-correction live run through CSV append and
left switch remains pending.
