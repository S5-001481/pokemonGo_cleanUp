# Bounded batch scanning on Huawei Mate 30

This walkthrough follows `pokemon-go-cleanup scan-batch` from an already-open
first Pokémon detail summary. The batch layer delegates every Pokémon to the
existing `AutoScanService`; it does not duplicate or alter the proven menu,
appraisal, IV, exit, or recognition sequence.

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

## Step 2: Reuse the complete one-scan transaction

For each item, `BatchScanService` calls `AutoScanService.scan_one()` with the
same selected serial and debug flag. A batch row is eligible only after that call
returns a complete manifest and a recognition result. It reads the already-saved
`summary.png`, builds the row, and atomically rewrites the CSV immediately. The
next phone input cannot erase or defer that durable row.

On `--resume`, the service reads the last complete row and its referenced
`summary.png`, captures the current phone page, and first requires
`detail_summary`. It compares only these adjacent pages using the same static ROI
hash plus OCR name, CP, and optional HP. If they are the same, it runs the existing
bounded next-Pokémon switch before scanning; if already different, it scans without
an extra switch.

Immediately before append, the new summary is compared once more only with the last
CSV row. A highly similar static ROI plus matching auxiliary identity refuses the
append even though the new scan directory differs. A single-scan exception or this
adjacent-duplicate guard stops the batch. Ctrl+C keeps earlier CSV rows and relies
on the current one-scan recovery path for any in-progress manifest.

## Step 3: Gate the only new gesture

After the complete one-scan flow has tapped the appraisal center to exit, the batch layer captures a
fresh screen and requires `detail_summary`. Its OCR name and CP must match the
just-saved recognition result before any switch input is sent.

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
animation exclusion, and the final adjacent-duplicate append guard. Physical testing saved 搗蛋小妖 CP318 as the first atomic row
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

Evidence status: Two-row batch persistence, resume, right switching, the repaired move gate, and bounded stopping are source-, test-, and device-confirmed.
