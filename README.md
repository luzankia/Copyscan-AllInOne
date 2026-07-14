# Image Workflow Automation Toolkit

> **Automated, multi-step workflow for comic, manga, and webtoon image sets. CLI and Web hybrid.**

---

## Features

* **Cross-platform**: Windows & Linux compatible.
* **Customizable**: Via `config.yaml` and CLI overrides, with startup validation that catches missing or misconfigured keys before anything runs.
* **CLI UX**: Color, progress, and error reporting powered by [rich](https://github.com/Textualize/rich).
* **Advanced Web UI**, all in a single browser tab:
  * **Global Sort**: Fast manual review of every chapter, with a live thumbnail always showing the current first page (self-healing after edits, deletions, merges or splits).
  * **Leaf Editor**: Opens a specific chapter folder to:
    * Permanently delete individual pages.
    * **Vertically merge consecutive images** (useful when a hosting site does an unwanted horizontal cut), with a dedicated validation step to accept or reject each generated merge before it's finalized.
    * **Split a single image** into multiple pages via an interactive cutting tool (horizontal/vertical markers, zoom), with automatic, conflict-free renumbering of the whole folder afterward.
    * **Prev/Next Chapter navigation**: jump directly to the previous or next chapter without going back to the main gallery first.
  * One-tab navigation: moving between the main gallery, a chapter editor, and the split studio reuses the same tab and reloads with up-to-date data every time — no stale thumbnails, no manual refresh.
  * **Optional popup masking**: confirmation dialogs and alerts in the Web UI can be silently auto-accepted via `mask_security_popups` in `config.yaml`, for a faster, uninterrupted review pass.
  * Responsive CSS tooltips and dynamic thumbnails.
* **External tools integration**: [ImageMagick](https://imagemagick.org/) (for integrity checks) and [7-Zip](https://www.7-zip.org/) (for compression).
  * If ImageMagick is missing, the CLI offers to skip the integrity check step for the current run instead of hard-failing.
  * If 7-Zip is missing, the CLI offers to fall back to Python's built-in `zipfile` module for compression (7-Zip remains the preferred, faster option when available).
* **Flexible logging**: log file location can be overridden per run via `--log-path`, disabled entirely via `log_enabled: false` in `config.yaml`, and the destination folder is created automatically if it doesn't exist.
* **Flexible folder layout**: works with the standard `root_dir/Parent1/Parent2/Leaf` hierarchy by default, or with a flat `root_dir/Leaf` layout via `--local`, for one-off or single-series batches.
* **Safe, no-data-loss logic**: Suffix-safe renaming and non-destructive operations.

---

## Installation

### 1. Clone the repository

```shell
git clone https://github.com/luzankia/Copyscan-AllInOne.git
cd Copyscan-AllInOne
```

### 2. Install dependencies

```shell
pip install -r requirements.txt
```

### 3. Install **ImageMagick 7+** and **7-Zip**

* **ImageMagick**: Download and install from the [official site](https://imagemagick.org/script/download.php). Ensure `magick` is in your system PATH. *(Required: Build with AVIF/HEIC support for relevant file types).*
* **7-Zip**: Install and ensure `7z` or `7za` is accessible via your PATH.

---

## Directory Structure

Standard hierarchy (default):

```text
Root/
  └─ Site (Parent1)/
      └─ Serie (Parent2)/
          └─ Chapter (Leaf)/
                ├─ image1.jpg
                └─ ...
```

Flat hierarchy (with `--local`):

```text
Root/
  └─ Chapter (Leaf)/
        ├─ image1.jpg
        └─ ...
```

---

## Quick Start

1. **Configure**: Copy `config.example.yaml` to `config.yaml`, then edit `config.yaml` to set your input/output paths and feature toggles. `config.yaml` is gitignored, so your local paths are never committed.
2. **Organize**: Place data into the hierarchy described above.
3. **Execute**:
```shell
python main.py
```

* **CLI Overrides**:
  * `--root-dir <path>` / `--dest-dir <path>` — override `root_dir` / `dest_dir` from `config.yaml` for a single run.
  * `--log-path <path>` — override `log_path` from `config.yaml` for a single run; the destination folder is created automatically if it doesn't exist.
  * `--local` — treat Leaf folders as sitting directly under `root_dir` (flat `root_dir/Leaf` layout) instead of the standard `root_dir/Parent1/Parent2/Leaf` hierarchy. Affects Steps 1 through 6 and Step 8's final move. Step 7 (CSV Operations) is automatically skipped in this mode, since it relies on the Parent1/Parent2 hierarchy — use `--skip-step 7` explicitly, or leave it enabled and it will simply no-op.
  * `--skip-step <steps>` — bypass one or more stages for this run without editing `config.yaml`. Accepts any of `1 2 3 4 5 5.1 6 7 8` (the `5.1` sub-step, hash-suffix cleanup, can be skipped independently of `5`). Multiple values can be combined: `python main.py --skip-step 2 5.1 6`.
  * `python main.py --help` for the full option list.
* On startup, `config.yaml` is validated: missing keys or values of the wrong type stop the run immediately with a clear error message instead of failing mid-workflow.
* If `step_1` is active and ImageMagick can't be found, the CLI asks whether to skip Step 1 for this run rather than aborting outright. If Step 1 is disabled (in `config.yaml` or via `--skip-step 1`), ImageMagick isn't checked at all.
* If neither `7z` nor `7za` can be found, the CLI asks whether to fall back to Python's `zipfile` module for Step 6 instead of aborting.
* At the end of a successful run, the console pauses on `Press ENTER to close this window...` so the summary stays visible when launched by double-click.

---

## Workflow Steps Overview

1. **Integrity Check**: Strict validation of images using `magick identify -verbose`, run in parallel across files. Skipped entirely (no ImageMagick lookup) if `step_1` is disabled.
2. **Manual Sort & Edit (Web UI)**: A local Flask server (`127.0.0.1` only) opens in your browser for:
   * Global review — flag first pages for deletion across all chapters at once.
   * Per-chapter editing — for any chapter, delete pages, merge consecutive images vertically (with a review/undo step before finalizing), split one image into several with an interactive marker-based tool, or jump to the previous/next chapter directly.
   * Confirmation popups and alerts can be auto-accepted via `mask_security_popups` for a faster review pass.
3. **Regex Cleanup**: Automated deletion of files matching patterns in `delete_regex` (e.g., stray `.nomedia` files).
4. **Empty Folder Pruning**: Recursive cleanup of empty directory structures.
5. **Leaf Folder Renaming**: Regex-based, conflict-proof renaming of chapter folders using the first matching rule in `rename_regex`.
   * **5.1 — Hash Suffix Cleaning**: Automatically strips trailing hashes (e.g., `_a1b2c3d4`) from folder names. Runs by default whenever step 5 is active, but can be disabled independently.
6. **Compression**: Parallelized compression of chapter folders into `[Chapter].cbz`, with archive validation and a bounded worker pool to limit disk contention. Uses 7-Zip by default, or Python's `zipfile` module as a fallback if 7-Zip isn't available.
7. **CSV Operations**: Batch rename and merge Parent2 (series) folders based on external CSV mappings (`csv_1_path`, `csv_2_path`). Automatically skipped when `--local` is used.
8. **Final Move & Cleanup**: Deployment of results to `dest_dir` and final purge of empty source folders. With `--local`, Leaf folders are moved directly to `dest_dir` instead of their Parent2 folders.

Each step can be toggled on or off in `config.yaml` under `steps_active`, or skipped for a single run via `--skip-step`.

---

## Advanced Configuration

`config.example.yaml` is the template — copy it to `config.yaml` and adjust every path. All keys below are required; the script validates their presence and type at startup.

```yaml
# Paths (all required)
root_dir: "C:\\Path\\To\\Your\\Input"
dest_dir: "C:\\Path\\To\\Your\\Output"
csv_1_path: "C:\\Path\\To\\Your\\Project\\exception.txt"
csv_2_path: "C:\\Path\\To\\Your\\Project\\batchexception.txt"
log_path: "C:\\Path\\To\\Your\\Project\\workflow.log"
log_enabled: true   # Set to false to disable the log file entirely

# Supported file extensions
supported_extensions:
  - ".jpg"
  - ".jpeg"
  - ".png"
  - ".webp"
  - ".avif"
  - ".bmp"
  - ".gif"

# Timing & Web UI
sleep_time: 1      # Pause (seconds) between steps
im_timeout: 20      # Timeout (seconds) per ImageMagick call
web_port: 5002      # Local port for the Web UI
thumb_size: "220px" # Thumbnail width in the Web UI

# Enable/disable individual steps (step_5_1 is optional; defaults to step_5's value if omitted)
steps_active:
  step_1: true
  step_2: true
  step_3: true
  step_4: true
  step_5: true
  step_5_1: true
  step_6: true
  step_7: true
  step_8: true

# Security Configurations
mask_security_popups: false  # true = auto-accept every confirm/alert popup in the Web UI

# Files to delete outright (step 3)
delete_regex:
  - '^\.nomedia$'

# Chapter folder renaming rules — first match wins (step 5)
rename_regex:
  - pattern: '^(?:.*_)?(?:Chapter|Ch\.)\s*(\d+(?:\.\d+)?)\s+-\s+Episode\s+\d+(?:\.\d+)?(.*)'
    replacement: 'Ch.\1\2'
  # ...see config.example.yaml for the full rule set
```

---

## Troubleshooting

* **Missing Binaries**: If `magick` isn't found and `step_1` is active, the CLI offers to skip Step 1 for this run. If neither `7z` nor `7za` is found, it offers to fall back to Python's `zipfile` module for compression. Declining either prompt aborts the run with the list of missing tools.
* **Invalid or incomplete `config.yaml`**: The script exits immediately with the list of missing/mistyped keys — check against `config.example.yaml`.
* **AVIF Issues**: Verify that your ImageMagick installation includes `libavif` support if Step 1 fails on valid files.
* **Locked Files**: Ensure no external programs (viewers, file explorers) are locking your directories during the workflow.
* **Web UI unreachable**: The server only binds to `127.0.0.1:<web_port>` — it's local-only by design and won't be reachable from other devices.
* **Logging**: Every operation is logged in the path specified by `log_path` in `config.yaml` (UTF-8 encoded), unless `log_enabled` is set to `false`. The destination folder is created automatically if missing; use `--log-path` to override the location for a single run.

---

## Security Notice

* All operations are **non-destructive** by default logic: suffix-safe renaming resolves naming conflicts, and the destination folder is never automatically cleaned by the tool.
* The Web UI server is bound to `127.0.0.1` only and is never exposed to the network.
* `config.yaml` is excluded from version control (see `.gitignore`); only `config.example.yaml`, with placeholder paths, is tracked.
* `mask_security_popups: true` auto-accepts every confirmation dialog in the Web UI (including destructive actions like deletions and splits) — only enable it once you trust your review workflow, since it removes the "are you sure?" safety net.