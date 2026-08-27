# Local capture, validation, and annotation pipeline

The project has one local data pipeline with three human-visible phases:
capture evidence from a selected ADB device, validate stored scan groups, and
add manual ground truth. The phone remains user-controlled throughout.

## Responsibilities

The capture side owns ADB discovery, device-state selection, resolution parsing,
PNG signature rejection, guided sequencing, and scan manifest lifecycle. The
dataset side owns recursive local discovery, full image decoding, dimension
comparison, typed JSON validation, and scan classification. The annotation side
owns prompts or JSON input, typed ground truth, overwrite protection, and atomic
UTF-8 persistence.

These responsibilities meet in one scan directory but do not blur their trust
boundaries. Capturing does not infer facts from an image, validation does not
change data, and annotation does not contact the phone.

## Inputs and outputs

| Phase | Inputs | Local outputs |
| --- | --- | --- |
| Guided capture | ADB executable, one selected device, three user confirmations | Three fixed PNG files and a progressive `manifest.json` |
| Dataset validation | A dataset root containing scan-shaped directories | A table and process status; no dataset mutation |
| Annotation | One scan directory plus prompts or `--input-json` | One typed `ground_truth.json` |

The detailed lifecycle is visible in the
[guided scan walkthrough](../walkthroughs/guided-scan.md) and
[dataset walkthrough](../walkthroughs/dataset-validation-and-annotation.md).

## Trust boundaries

ADB output is external and can fail even after device selection. The capture
layer rejects empty or non-signature PNG bytes before persistence. The dataset
layer later performs a stronger check by fully decoding every present PNG and
requiring equal dimensions across the three views.

A missing required artifact is an incomplete capture fact. A present but corrupt
artifact is invalid evidence. Keeping those states separate prevents a partially
finished scan from being confused with damaged data.

Human ground truth is another boundary. Existing annotation work is preserved
unless the user confirms replacement or supplies `--force`. Pydantic validates
interactive and non-interactive values before the atomic writer touches the
destination.

## Persistence and failure behavior

Shared storage helpers create a unique temporary file in the destination
directory, flush and synchronize its contents, and replace the final path.
Temporary files are cleaned after success or failure. A guided midway failure
preserves earlier screenshots and records the failed step when the manifest can
still be updated.

Atomic replacement applies to one file at a time. It does not make a screenshot
and its JSON metadata a single filesystem transaction.

## Privacy and exclusions

All runtime capture and annotation artifacts remain below ignored `data/`, and
any `ground_truth.json` is ignored independently. Tests generate synthetic
solid-color PNGs in temporary directories; real device images are not copied
into the repository.

Guided capture remains free of OCR and device input. The fixed automatic path
uses OCR plus tap/swipe input, and its only opt-in gameplay mutation is
`--rename-with-iv` on one-scan or batch; it restores the default Chinese name
then appends recognized IVs. A verified rename adds local
`renamed_summary.png` and `nickname_change.json`; batch CSV stores the before and
after names plus `not_requested` or `verified`. Transfer, other gameplay
mutations, account or credential access, private APIs, and network inspection
remain out of scope.

## Evidence

Source and test links for each claim are maintained in the
[source evidence ledger](../references/source-evidence.md). Terms used across
the guide are defined in the [shared project terminology](../glossary.md).
