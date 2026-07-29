# Understanding pokemonGo_cleanUp

This project turns local CLI actions into inspectable screenshot artifacts and
UTF-8 JSON metadata from one ADB-connected phone.

A single capture records one current display, while a guided scan groups summary,
moves, and appraisal views from the same device under one scan ID.
Dataset validation then decides whether each local group is complete, incomplete,
or invalid; annotation adds typed human ground truth. A fixed automatic path gates
one Huawei Mate 30 scan, and a bounded batch layer safely advances between summaries.

The main behavior to understand is `pokemon-go-cleanup scan-one --guided`. ADB is
an external process, the user controls the visible phone state, and any screenshot
step can fail after earlier artifacts already exist.

Follow [one guided scan from device selection to manifest](walkthroughs/guided-scan.md)
to see that lifecycle end to end; use the
[original single-capture path](walkthroughs/one-real-run.md) for its sidecar flow.

## Reader Routes

| Reader goal | Start here | What this page gives you |
| --- | --- | --- |
| Understand one guided scan | [Follow the three-view lifecycle](walkthroughs/guided-scan.md) | The user handoffs, atomic screenshots, manifest updates, and incomplete-state recovery. |
| Understand one automatic scan | [Follow the gated device-input lifecycle](walkthroughs/automatic-one-scan.md) | Fixed coordinates, page states, stable waits, menu OCR safety, IV confirmation, and incomplete recovery. |
| Understand bounded batch scanning | [Follow complete scan to guarded horizontal switch](walkthroughs/batch-scan.md) | One-scan reuse, fixed switching, identity checks, atomic CSV rows, resume, wrap detection, and stop conditions. |
| Validate and annotate scans | [Follow local scans into typed ground truth](walkthroughs/dataset-validation-and-annotation.md) | Recursive discovery, full PNG decoding, status classification, prompts, overwrite protection, and atomic JSON. |
| Understand one standalone capture | [Follow the capture path](walkthroughs/one-real-run.md) | The device-selection, validation, and PNG/sidecar persistence handoffs. |
| Understand module boundaries | [Map the local data pipeline](modules/local-data-pipeline.md) | Capture, validation, annotation, trust boundaries, persistence, and privacy. |
| Install and use the CLI | [Set up Windows, ADB, and Huawei authorization](../README.md) | Native Windows commands, Platform-Tools setup, USB debugging, and troubleshooting. |
| Audit a behavior claim | [Inspect the source evidence](references/source-evidence.md) | Evidence passes, confidence labels, source/test links, caveats, and falsifying checks. |
| See what changed over time | [Review project-guide changes](change-log.md) | Meaningful requests, actions, verification, and sync state. |

## The short model

A guided scan moves through five decisions:

1. The CLI locates ADB and selects exactly one ready device.
2. The service creates a scan ID, directory, and `in_progress` manifest.
3. The user prepares each view and confirms it with Enter.
4. Each validated PNG atomically becomes its final step filename.
5. The manifest becomes `complete`, or `incomplete` with the failed step.

The downstream dataset path fully decodes present PNGs, validates typed JSON,

The automatic path replaces user handoffs with a narrower state machine: it
requires the fixed 1440x3120 layout, verifies the allowed pre-state before every
input, waits for visual stability afterward, and refuses to guess the appraisal
menu row. Follow the automatic walkthrough before running it on a phone.
separates missing artifacts from corrupt ones, and protects human annotations.
Follow [that validation-to-annotation handoff](walkthroughs/dataset-validation-and-annotation.md)
when the scan directory already exists.

`Device` and `ScreenResolution` describe the target, while `ScanManifest` carries
the grouped lifecycle. `CaptureMetadata` remains the standalone-capture sidecar.
Exact source and test links live in the
[claim-by-claim evidence map](references/source-evidence.md).

## Where the model stops

- The guided workflow has produced eight locally validated scan groups on the
  target Huawei Mate 30; this does not by itself validate every native Windows
  ADB installation.
- ADB capture rejects bytes without a PNG signature. Dataset validation later
  fully decodes each stored PNG and compares all three dimensions.
- Guided screenshots and manifest updates use same-directory temporary files and
  atomic replacement, subject to the local filesystem's replacement guarantees.
- PNG and JSON are separate writes. A local failure during the JSON write can
  leave the PNG without its sidecar.
- Automatic and batch input remain limited to one fixed Huawei layout. Transfer,
  power-up/evolution/rename/unlock/battle actions, account/private API access,
  traffic inspection, and credentials remain outside.
- A physical one-scan run completed. The first batch attempt safely stopped before
  input on a black screen; two-Pokémon switching still needs the prepared phone.
- Ground truth remains manual and can be replaced only by confirmation or `--force`.

Evidence status: Confirmed unless noted.
