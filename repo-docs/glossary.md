# Glossary

## ADB

Android Debug Bridge, the external command-line tool used to discover the
connected device, read its logical resolution, and capture the current screen.
The project does not use ADB to tap, swipe, or control gameplay.

## Annotation

Human-entered facts stored as UTF-8 `ground_truth.json` in one scan directory.
The typed contract and replacement rules are traced in the
[dataset walkthrough](walkthroughs/dataset-validation-and-annotation.md).

## Atomic replacement

A local persistence pattern that writes and synchronizes a unique temporary file
beside its destination before replacing the final path. It prevents an
unfinished temporary file from appearing under a final screenshot or JSON name,
but does not make separate PNG and JSON writes one transaction.

## Batch scan

A bounded sequence that reuses the complete one-Pokémon automatic service, writes
each recognition row atomically, and advances only after a fixed left swipe produces
a different name, CP, or static page fingerprint. See the [batch walkthrough](walkthroughs/batch-scan.md).

## Ground truth

Manually verified values used as the trusted reference for future dataset work.
Ground truth is not OCR output and is never inferred from an account or network
API in the current project.

## Guided scan

One user-controlled three-view capture consisting of `summary.png`,
`moves.png`, `appraisal.png`, and `manifest.json` under one scan ID.
See the [guided scan walkthrough](walkthroughs/guided-scan.md).

## Scan status

The dataset validator's outcome for a discovered scan directory:
`complete`, `incomplete`, or `invalid`. Missing required artifacts are
incomplete; corrupt images, malformed typed JSON, or dimension mismatches are
invalid.

## Scan manifest

The typed `manifest.json` created by the guided workflow. It records the scan
identity, device metadata, resolution, captured steps, application version,
workflow mode, lifecycle status, optional notes, and any failed step.
