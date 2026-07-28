# Understanding pokemonGo_cleanUp

This project turns local CLI actions into inspectable screenshot artifacts and
UTF-8 JSON metadata from one ADB-connected phone.

A single capture records one current display, while a guided scan groups summary,
moves, and appraisal views from the same device under one scan ID.

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
| Understand one standalone capture | [Follow the capture path](walkthroughs/one-real-run.md) | The device-selection, validation, and PNG/sidecar persistence handoffs. |
| Install and use the CLI | [Set up Windows, ADB, and Huawei authorization](../README.md) | Native Windows commands, Platform-Tools setup, USB debugging, and troubleshooting. |
| Audit a behavior claim | [Inspect the source evidence](references/source-evidence.md) | Three evidence passes, confidence labels, source/test links, caveats, and falsifying checks. |
| See what changed over time | [Review project-guide changes](change-log.md) | Meaningful requests, actions, verification, and sync state. |

## The short model

A guided scan moves through five decisions:

1. The CLI locates ADB and selects exactly one ready device.
2. The service creates a scan ID, directory, and `in_progress` manifest.
3. The user prepares each view and confirms it with Enter.
4. Each validated PNG atomically becomes its final step filename.
5. The manifest becomes `complete`, or `incomplete` with the failed step.

`Device` and `ScreenResolution` describe the target, while `ScanManifest` carries
the grouped lifecycle. `CaptureMetadata` remains the standalone-capture sidecar.
Exact source and test links live in the
[claim-by-claim evidence map](references/source-evidence.md).

## Where the model stops

- A physical Huawei Mate 30 guided scan has not yet been verified in this workspace.
- The PNG signature is checked; the full image structure is not decoded.
- Guided screenshots and manifest updates use same-directory temporary files and
  atomic replacement, subject to the local filesystem's replacement guarantees.
- PNG and JSON are separate writes. A local failure during the JSON write can
  leave the PNG without its sidecar.
- OCR, gameplay automation, account/private API access, traffic inspection, and
  credentials remain outside the project.

Evidence status: Confirmed unless noted.
