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
a 256-bit perceptual fingerprint derived from the fixed summary display crop.
Root CSV exports, all scan directories, recognition JSON, and debug files remain
ignored by Git.

## Step 2: Reuse the complete one-scan transaction

For each item, `BatchScanService` calls `AutoScanService.scan_one()` with the
same selected serial and debug flag. A batch row is eligible only after that call
returns a complete manifest and a recognition result. It reads the already-saved
`summary.png`, builds the row, and atomically rewrites the CSV immediately. The
next phone input cannot erase or defer that durable row.

A single-scan exception stops the batch. Ctrl+C keeps earlier CSV rows and relies
on the current one-scan recovery path for any in-progress manifest.

## Step 3: Gate the only new gesture

After the complete one-scan flow has pressed BACK, the batch layer captures a
fresh screen and requires `detail_summary`. Its OCR name and CP must match the
just-saved recognition result before any switch input is sent.

The fixed Mate 30 batch profile performs one right swipe from
`(260, 1500)` to `(1180, 1500)` over 600 ms. That y-coordinate lies in the
summary name/display region and remains far above the bottom menu and transfer
band. The profile is intentionally limited to 1440x3120 Traditional Chinese.

## Step 4: Prove that the page changed

After a swipe, the service captures and classifies every 500 ms for at most 15
seconds. A transient `unknown` may continue during animation. Any recognized
menu, moves, or appraisal state stops immediately. A `detail_summary` is accepted
only when at least one of OCR name or CP differs from the previous Pokémon.

If the summary stays the same through the first timeout, one fresh screenshot
must still confirm that same detail summary before a second and final identical
swipe. Two unchanged attempts raise a safe batch error instead of scanning the
same Pokémon forever.

When the changed identity matches the first row's name and CP, the service stops
with `wrapped_to_first` before scanning it again. Otherwise the next loop invokes
the unchanged one-scan service.

## Step 5: Inspect evidence and stop reasons

With `--debug`, each scan keeps its existing `debug/automation/` evidence. The
preceding scan also receives `debug/batch/` with the pre-switch screenshot,
coordinates, per-sample screenshots, OCR state/identity timeline, and final
attempt state.

Normal completion reports `limit_reached` or `wrapped_to_first`. Page mismatch,
two failed switches, malformed resume data, a single-scan failure, or Ctrl+C
stops without deleting complete scans or previously written rows.

## Verification boundary

Synthetic tests cover two distinct scans, the exact fixed right swipe, atomic
two-row CSV persistence, summary-before-IV classification, debug output, and the
two-attempt ceiling. Physical testing saved 搗蛋小妖 CP318 as the first atomic row
and switched to a reliably OCR-identified 睡睡菇 CP431 summary. The first attempt at the second one-scan run stopped when its move-section
heading fell just above the old OCR crop, so the batch correctly preserved one row
and sent no further switch. The fixed Mate 30 `detail_moves` gate now accepts a
move-name/damage-number row without requiring that heading; saved real move and
appraisal screens confirm the positive and negative boundaries. A resumed live run then scanned 睡睡菇 CP431 from the current page, saved a
second complete manifest and recognition, atomically appended row 2, and stopped at
`limit_reached`. The preserved first row remained 搗蛋小妖 CP318, so the final CSV
had exactly two distinct scan IDs and two distinct name/CP identities.

Continue to the [source evidence and falsifying checks](../references/source-evidence.md) for the exact implementation and test anchors.

Evidence status: Two-row batch persistence, resume, right switching, the repaired move gate, and bounded stopping are source-, test-, and device-confirmed.
