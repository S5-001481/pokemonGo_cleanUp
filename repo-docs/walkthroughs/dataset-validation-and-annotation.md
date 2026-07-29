# From local scans to validated ground truth

This walkthrough follows `pokemon-go-cleanup dataset validate` over a directory
of guided scans, then follows `pokemon-go-cleanup annotate <scan-directory>`
for one scan. The input is local PNG and JSON data created by the guided
workflow. Success is a readable status table plus an atomic, typed
`ground_truth.json`.

The hard part is separating absence from corruption. A capture may be unfinished
because one file never arrived, while a present file may be unsafe to trust
because it cannot be decoded or violates its schema. Annotation adds a second
boundary: existing human work must not be overwritten implicitly.

## Step 1: Recursive discovery finds scan-shaped directories

The dataset command starts from `data/scans/` unless `--output` supplies a
different dataset root. It walks recursively, so an extra organizational
directory does not hide scans. A directory becomes a candidate when it contains
a recognized scan artifact or appears as a scan-ID directory below a dated
folder.

The discovery rules live in
[`dataset.py`](../../src/pokemon_go_cleanup/dataset.py). A missing or non-directory
root is a typed dataset error rather than an empty success.

## Step 2: Present files are decoded and typed

Each candidate is checked for `summary.png`, `moves.png`,
`appraisal.png`, and `manifest.json`. Pillow fully loads every present PNG;
a signature alone is not enough at this stage. When all three decode, their
dimensions must match.

The manifest is parsed through
[`ScanManifest`](../../src/pokemon_go_cleanup/models.py), including rejection of
unknown fields. If `ground_truth.json` already exists, it is also parsed through
the typed `GroundTruth` contract. Exact test cases use runtime-generated solid
color images in [`test_dataset.py`](../../tests/test_dataset.py), never real
device media.

## Step 3: Evidence becomes one of three scan states

Classification preserves the distinction a maintainer needs:

- `complete` means every required artifact is present, all three images decode
  with equal dimensions, the manifest is valid and marked complete, and any
  existing annotation is valid;
- `incomplete` means a required artifact is absent or the manifest lifecycle has
  not reached complete;
- `invalid` means present JSON is malformed, a PNG cannot be decoded, dimensions
  disagree, or an existing annotation violates its schema.

`dataset validate` prints details and exits non-zero when any result is invalid.
`dataset status` prints the scan ID, capture date, screenshot count, manifest
validity, annotation presence, and overall status without using invalid data as a
process failure. Command-level table behavior is exercised in
[`test_dataset_cli.py`](../../tests/test_dataset_cli.py).

## Step 4: Annotation starts from visible local evidence

The annotate command prints absolute paths for all three screenshots before
asking for values. The user remains responsible for opening and reading those
local images; the program performs no OCR and sends no phone command.

Prompts collect name, CP, HP, dimensions and weight, one or two types, moves,
three IV values, state flags, and optional notes. The
[`GroundTruth` model](../../src/pokemon_go_cleanup/models.py) enforces positive or
non-negative numeric fields, IV integers from 0 through 15, non-empty names and
moves, one or two unique types, and `hp_current <= hp_max`.

## Step 5: Existing work is preserved unless replacement is explicit

When a valid `ground_truth.json` exists, every interactive prompt shows its
current value and Enter keeps it. Before prompts begin, replacement requires an
explicit confirmation. `--force` is the automation-safe opt-in to replacement.

`--input-json` skips prompts and validates a supplied UTF-8 JSON file. It will
not replace an existing annotation unless `--force` is also present. The
service in [`annotation.py`](../../src/pokemon_go_cleanup/annotation.py) shares
the same atomic text writer used by scan manifests, so a temporary file is
flushed, synchronized, and replaced in the destination directory.

## Step 6: Verification stays independent of private media

Run the focused checks from the repository WSL environment:

```bash
pytest tests/test_dataset.py tests/test_dataset_cli.py tests/test_annotation.py -q
ruff check .
mypy
python -m pip check
```

The local Huawei dataset can then be checked without modifying it:

```bash
pokemon-go-cleanup dataset validate
```

On 2026-07-28 this command reported eight complete scans, 24 decodable PNGs,
eight valid manifests, and no annotations. That local observation confirms the
guided Huawei path while the repository tests remain synthetic.

For the capture lifecycle that produces these directories, follow
[the guided three-view workflow](guided-scan.md). For claim-level proof and
remaining boundaries, use the
[source evidence and falsifying checks](../references/source-evidence.md).

Evidence status: Confirmed unless noted.
