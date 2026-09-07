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
| Review every automatic/batch issue through 2026-08-27 | [Read the cumulative Chinese problem/solution summary](2026-08-26-debugging-summary.zh-CN.md) | Twenty-four diagnosed problems, their causes, fixes or current status, safety boundaries, saved evidence, and remaining live checks. |

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
input, polls for the expected target state after actions, and refuses to guess the
appraisal menu row. It does not use whole-screen pixel stability because the CP
background and Pokémon model are permanently animated. Follow the automatic
walkthrough before running it on a phone.
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
- Automatic input remains limited to one fixed Huawei layout. `--rename-with-iv`
  on `scan-auto-one` or `scan-batch`, and the dedicated `rename-iv-one` action,
  opt into nickname mutation: each resets the
  nickname to the game's default Chinese species name, then appends recognized
  IVs. Its first live run stopped safely because the original pencil coordinate
  missed the real control. The current flow no longer targets the pencil: after
  confirming `detail_summary`, both rename passes tap the fixed center of the
  nickname row at `(720,1460)` and then require an OCR-confirmed keyboard/dialog
  state before any delete or text input. The
  flow OCR-clicks the input method's right-side confirmation before the real
  dialog `OK`. New names append three circled IVs such as `超梦⑭⑭⑮`, with
  `⓪` for zero; a complete name over 12 characters is rejected. The phone must
  have ADB Keyboard enabled. UTF-8/Base64 input temporarily selects that IME and
  restores the prior keyboard, including after failure or interruption. Missing
  support stops before capture or editing; installation/enabling is never automatic.
  Editor, wide-summary and batch-name comparisons preserve circle characters.
  The former slash/compact-digit formats remain historical scan evidence; the
  new circle input passed a complete physical run for `向日種子⑮⑭⑩`.
  A dedicated three-ring/two-interior-crop reader handles OCR circle loss, and
  IME binding/display checks prevent confirmation from racing keyboard restoration.
  Batch mode additionally requires that width-sensitive editor evidence,
  a wide final-name ROI, unchanged CP/HP/static fingerprint, and durable rename
  evidence before a row or switch. The first live batch rename saved the
  correct `飄飄球12/2/5` but the original wide crop omitted the middle `2` during
  OCR, so it correctly wrote no row and sent no left swipe; the tighter
  nickname-row crop replays both failed screenshots exactly. Transfer,
  power-up/evolution/unlock/battle actions, account/private API access,
  traffic inspection, and credentials remain outside.
- A physical one-scan run completed. The first batch attempt safely stopped before
  input on a black screen; two-Pokémon switching still needs the prepared phone.
- The Tkinter launcher reports successful scans for the current run as a large
  number in the open area to the right of the maximum-scan input. Single-scan
  success is counted only on exit 0; batch progress reads complete records from
  the atomically replaced CSV and subtracts the resume baseline, so historical
  rows and failed partial scans are not counted. A monotonic `HH:MM:SS` timer sits
  directly below the count, resets for single/batch scan or IV-only naming starts, updates
  during the child process, and freezes at the final complete, failed, or stopped
  duration.
- The GUI also offers **扫描 IV 并命名** for the current Pokemon. It opens appraisal
  directly, uses summary/IV evidence in memory, and reuses the existing guarded
  rename flow. It skips moves and all scan/CSV/debug file persistence, including
  failure paths. Batch/CSV/debug settings are ignored, successful exit counts one,
  and Stop/timing follow the existing task lifecycle. See the
  [IV-only path and its limits](walkthroughs/automatic-one-scan.md#iv-naming-without-a-saved-scan).
- Debug-enabled automatic scans write one monotonic `debug/automation/timings.json`
  per scan directory. Ordered navigation, capture, recognition, finalization,
  and rename substeps retain durations and completed/failed outcomes; the file is
  finalized on success, safe failure, Ctrl+C, and dry run. A batch therefore has
  a separate profile for every attempted Pokémon, including the failed item.
  Each step and the top level split out cumulative OCR-engine time and report
  `stable_wait_seconds: 0`, confirming that no production action waits for full-screen
  stability.
- `detail_summary` OCR uses progressive fallback without changing page identity
  comparators: three ordinary CP and name variants return immediately on valid
  name+CP; an HSV near-white mask runs after an ordinary CP miss, the six threshold
  variants run only after HSV also misses, and HP OCR runs last. Final recognition
  follows the same rule. Saved-real replay retains the known names and CP values,
  including `古月鳥` CP1217, and recovers `索財靈` CP458 from some anniversary
  animation frames without accepting isolated artwork numbers. A frame that still
  loses CP cannot establish a new Pokémon's CP. When a batch checkpoint already
  has a trusted baseline, however, it may reuse that baseline CP only after exact
  name/nickname, exact HP, and distance-8 static-fingerprint evidence all match.
  Live pre-switch checks retry CP twice first and a strong fallback authorizes only
  one swipe. This applies to ordinary and renamed pages, rename transitions, saved
  row restoration, duplicate/resume baselines, switch polling, and retry
  confirmation; a different/new page still has to yield CP.
  Before the first nickname-editor tap, a conflicting persisted/checkpoint CP now
  triggers a bounded no-input CP-only burst. One complete prefixed CP must repeat
  twice before the recognition baseline is atomically replaced; otherwise rename
  stops before opening the editor. The burst does not repeat name/nickname, HP,
  fingerprint, or full-page detection for every frame.
- Final move recognition keeps the normal anchor crop first and adds explicit
  Shadow and Dynamax fallbacks only for an upper move-section anchor accompanied
  by `暗影獎勵` or `極巨招式`. Modifier and Max Move labels remain excluded from
  regular move names. Saved-real replay recovers `冰息`/`遷怒` for shadow 冰雪龍,
  `躍起`/`遷怒` for shadow 果然翁, and `踢倒`/`地獄翻滾` for Dynamax 豪力.
  The existing CSV warnings/remarks field records `类型：暗影` or `类型：极巨化`.
- Ground truth remains manual and can be replaced only by confirmation or `--force`.

Evidence status: Confirmed unless noted.
