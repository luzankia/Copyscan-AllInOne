# CopyScan - All in One

> **Automated, multi-step workflow for comic, manga, and webtoon image sets. CLI and Web hybrid.**

---

## Features

* **Cross-platform**: Windows & Linux compatible. Mostly tested on Windows so far, bugs to be expected on Linux...
* **Customizable**: Via `config.yaml` and CLI overrides.
* **CLI UX**: Color, progress, and error reporting powered by [rich](https://github.com/Textualize/rich).
* **Advanced Web UI**, all in a single browser tab:
  * **Global Sort**: Fast manual review of every chapter, with thumbnails always showing the current first page (self-healing after edits, deletions, merges or splits).
  * **Chapter Editor**: Opens a specific chapter folder to:
    * Permanently delete individual pages.
    * **Vertically merge consecutive images** (useful when a hosting site does an unwanted horizontal cut), with a dedicated validation step to accept or reject each generated merge before it's finalized.
    * **Split a single image** into multiple pages via an interactive cutting tool (horizontal markers, zoom), with automatic, conflict-free renumbering of the whole folder afterward.
  * **Optional popup masking**: confirmation dialogs and alerts in the Web UI can be silently auto-accepted via `mask_security_popups` in `config.yaml`, for a faster, uninterrupted review pass.
  * Responsive CSS tooltips and dynamic thumbnails.
* **External tools integration**: [ImageMagick](https://imagemagick.org/) (for integrity checks) and [7-Zip](https://www.7-zip.org/) (for compression).
  * If ImageMagick is missing, the CLI offers to skip the integrity check step for the current run instead just stopping the process.
  * If 7-Zip is missing, the CLI offers to fall back to Python's built-in `zipfile` module for compression (7-Zip remains the preferred, faster option when available).
* **Portable configuration**: `config.yaml` is looked up next to the script by default (`--config <path>` to point elsewhere), and the project-related paths inside it (`csv_1_path`, `csv_2_path`, `log_path`, `credit_hashes_path`, `credit_banners_path`) can be given as relative paths — they're resolved against the script's own folder, not the current working directory, so the whole setup stays portable regardless of where you launch it from.
* **Natural chapter ordering**: chapters are listed everywhere (main gallery, previous/next navigation) in natural numeric order (`Ch.2 < Ch.9 < Ch.20 < Ch.110`), not plain lexical order.
* **Flexible logging**: log file location can be overridden per run via `--log-path`, disabled entirely via `log_enabled: false` in `config.yaml`.
* **Flexible folder layout**: works with a standard `root_dir/Parent1/Parent2/Leaf` hierarchy by default, or with a flat `root_dir/Leaf` layout via `--local`, for one-off or single-series batches.
* **Automatic port collision avoidance**: the Web UI and the standalone [Hash Maintenance tool](#hash-maintenance-tool) share a single `web_port` setting. If it's already taken (e.g. both are running at once), the next free port is found automatically and used instead — no manual port juggling needed.
* **Safe, no-data-loss logic**: Suffix-safe renaming and non-destructive operations. Your destination folder should never loss data.

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

1. **Configure**: Copy `config.example.yaml` to `config.yaml`, then edit `config.yaml` to set your input/output paths and feature toggles.
2. **Organize**: Place data into the hierarchy described above (Mihon's style).
3. **Execute**:
```shell
python main.py
```

* **CLI Overrides**:
  * `--config <path>` — use a specific `config.yaml` instead of the one next to `main.py`.
  * `--root-dir <path>` / `--dest-dir <path>` — override `root_dir` / `dest_dir` from `config.yaml` for a single run.
  * `--log-path <path>` — override `log_path` from `config.yaml` for a single run; the destination folder is created automatically if it doesn't exist.
  * `--local` — treat Leaf folders as sitting directly under `root_dir` (flat `root_dir/Leaf` layout) instead of the standard `root_dir/Parent1/Parent2/Leaf` hierarchy. Affects Steps 1 through 7 and Step 9's final move. Step 8 (CSV Operations) is automatically skipped in this mode, since it relies on the Parent1/Parent2 hierarchy — use `--skip-step 8` explicitly, or leave it enabled and it will simply no-op.
  * `--skip-step <steps>` — bypass one or more stages for this run without editing `config.yaml`. Accepts any of `1 2 3 4 5 5.1 6 7 8` (the `5.1` sub-step, hash-suffix cleanup, can be skipped independently of `5`). Multiple values can be combined: `python main.py --skip-step 2 5.1 6`.
  * `python main.py --help` for the full option list.
* On startup, `config.yaml` is validated: missing keys or values of the wrong type stop the run immediately with a clear error message instead of failing mid-workflow.
* If `step_1` is active and ImageMagick can't be found, the CLI asks whether to skip Step 1 for this run rather than aborting outright. If Step 1 is disabled (in `config.yaml` or via `--skip-step 1`), ImageMagick isn't checked at all.
* If neither `7z` nor `7za` can be found, the CLI asks whether to fall back to Python's `zipfile` module for Step 7 instead of aborting.
* At the end of a successful run, the console pauses on `Press ENTER to close this window...` so the summary stays visible when launched by double-click.

---

## Workflow Steps Overview

1. **Integrity Check**: Strict validation of images using `magick identify -verbose`, run in parallel across files. Skipped entirely (no ImageMagick lookup) if `step_1` is disabled.
2. **Manual Sort & Edit (Web UI)**: A local Flask server (`127.0.0.1` only) opens in your browser for:
   * Global review — every chapter is listed with its current first page (self-healing after edits, deletions, merges or splits), in natural numeric order. Click anywhere on a chapter to open it in the Chapter Editor; validate once you're done reviewing to move on to the next workflow step.
   * Per-chapter editing — for any chapter, delete pages (optionally jumping straight to the next chapter afterward), merge consecutive images vertically (with a dedicated review step — **Validate**, or **Cancel Merges** to discard every pending merge and restore the original unmerged pages), split one image into several with an interactive marker-based tool, or jump to the previous/next chapter directly. Selected images (pending deletion or merge) are highlighted in red with a translucent overlay, so it's clear at a glance which pages will be affected. On the last chapter, "Next Chapter" (and its keyboard shortcut) falls back to the main review page instead of being disabled. The merge-validation step only shows its Cancel/Validate buttons — no chapter navigation there.
   * A thin, high-contrast progress bar is pinned to the top of the webUI, showing overall progress through the chapter list (no numbers, just a proportional fill).
   * Keyboard shortcuts (all rebindable via `keyboard_shortcuts` in `config.yaml`; defaults shown below) :
     * **←** / **→** previous/next chapter (both phases),
     * Selection phase — **Delete** = Delete Selection,
     * **Shift+Delete** = Delete Sel. & jump to next chapter.
     * **C** = Remember Credit, 
     * **Shift+C** = Remember Credit & jump to next chapter, 
     * **M** = Merge Pairs,
     * **V** = Validate Merges, 
     * **X** executes split,
     * Holding **Shift** show "& ⏭" and work for the mouse too,
     * A "?" icon next to these buttons shows the behavior on hover. 
   * Split tool markers can be placed by clicking, and dragged to fine-tune their position (cursor turns into a resize arrow when hovering a marker).
   * Confirmation popups and alerts can be auto-accepted via `mask_security_popups` for a faster review pass.
   * **Known credit page detection**: every image is compared (perceptual hash, via `imagehash`) against a growing local database of previously-confirmed "credit page" images. A match is flagged with a "Known credit" tag (informational — shown in the global review and inside the Chapter Editor). Inside the Chapter Editor, select the matching page(s) and click **"🧠 Remember Credit"** to delete it and teach the system that image for future chapters/series; it takes effect immediately for the rest of the current session, and persists across runs via `credit_hashes_path`.
   * **Embedded credit banner detection**: for banners merged into the top of a chapter's first page or the bottom of its last page (rather than a standalone page), the first/last image is scanned for a match against a growing local database of known banner crops. A match shows a "📎 Likely banner" tag on the chapter's grid view — clicking it opens the Split tool with the boundary already marked at the detected position (still draggable for fine-tuning), alongside a banner naming which known hash triggered the suggestion and a **"🗑 Delete this hash"** button to remove that specific entry from the database if the suggestion turns out to be badly placed or unusable. Confirm with **"🧠 Remove Above"** / **"🧠 Remove Below"** to crop it out in place and remember it for next time (persists via `credit_banners_path`). A banner merged in the *middle* of a page is rare enough to not be worth automating: split the page into pieces with the existing Split tool, delete the middle slice, then use "Merge Pairs" to stitch the remaining pieces back together.
3. **Regex Cleanup**: Automated deletion of files matching patterns in `delete_regex` (e.g., stray `.nomedia` files).
4. **Empty Folder Pruning**: Recursive cleanup of empty directory structures.
5. **Leaf Folder Renaming**: Regex-based, conflict-proof renaming of chapter folders using the first matching rule in `rename_regex`.
   * **5.1 — Hash Suffix Cleaning**: Automatically strips trailing hashes (e.g., `_a1b2c3d4`) from folder names. Runs by default whenever step 5 is active, but can be disabled independently.
6. **Renumbering**: Scans each Leaf folder for purely-numeric filenames (e.g. `002.jpg`) and, if any gaps exist in the sequence, recalibrates them into a contiguous run starting at 1 (e.g. `002, 003, 004` -> `001, 002, 003`), preserving each file's original zero-padding width. Folders that are already contiguous, or that contain no numeric filenames, are left untouched.
7. **Compression**: Parallelized compression of chapter folders into `[Chapter].cbz`, with archive validation and a bounded worker pool to limit disk contention. Uses 7-Zip by default, or Python's `zipfile` module as a fallback if 7-Zip isn't available.
8. **CSV Operations**: Batch rename and merge Parent2 (series) folders based on external CSV mappings (`csv_1_path`, `csv_2_path`). Automatically skipped when `--local` is used.
9. **Final Move & Cleanup**: Deployment of results to `dest_dir` and final purge of empty source folders. With `--local`, Leaf folders are moved directly to `dest_dir` instead of their Parent2 folders.

Each step can be toggled on or off in `config.yaml` under `steps_active`, or skipped for a single run via `--skip-step`.

---

## Hash Maintenance Tool

`hash_maintenance.py` is a **standalone** Flask tool for reviewing and curating the credit-page / credit-banner hash databases used by Step 2 — it's completely independent of the main workflow and can be launched any time, whether or not `main.py` is running.

```shell
python hash_maintenance.py
```

* Reads `credit_hashes_path`, `credit_banners_path`, `credit_hash_threshold`, `credit_banner_threshold`, and `web_port` from `config.yaml` next to the script by default (`--config <path>` to point elsewhere; `--credit-hashes-path`, `--credit-banners-path`, `--credit-hash-threshold`, `--credit-banner-threshold`, `--port` to override individually without touching the file).
* **Add a hash**: a single upload/drop zone accepts any reference image. It then opens the same Split tool used by the main workflow, letting you either **validate the whole image as-is** (added to the credit-page database) or **mark a top/bottom slice** and confirm it as a banner (added to the corresponding banner database) — no need to pick a category up front.
* **Review & prune**: each database is listed as a table. Near-duplicate hashes are automatically clustered by Hamming distance and tagged (e.g. "cluster #2 (3)"), with a one-click "Select redundant duplicates" action to pre-check every member but the first in each cluster before deleting.
* On startup, the console prints the exact resolved path of the config file and both hash databases.
* Shares the `web_port` setting with the main Web UI (see [Automatic port collision avoidance](#features) above): whichever of the two starts first gets that port, the other automatically moves to the next free one.

---

## Local Extraction Helper (`local.py`)

`local.py` is a small launcher utility for a local workflow execution: you have a folder of downloaded `.cbz` archives (or already-extracted image subfolders) and want it fed straight into `main.py`, without manually unzipping each archive first.

```shell
python local.py
```

* **Folder selection**, in order of priority: `-d/--dir <path>` on the command line; otherwise a native folder-picker dialog (via `tkinter`); if that's unavailable (no `tkinter` installed, or no display — e.g. an SSH session without X11 forwarding), it falls back to a plain text prompt. Pass `--no-gui` to skip the dialog and always use the text prompt.
* **Smart extraction**: for each `.cbz`, detects whether the archive already has a single root folder at its top level. If so, extracts directly into the target folder (letting that root folder become the chapter folder); otherwise, creates a folder named after the archive and extracts into it.
* On success, the original `.cbz` files are deleted and `main.py` is launched automatically against the target folder, with `--local` and `--skip-step 1 3 4 5.1 8 9` (i.e. only Renaming, Renumbering, and Compression run — the steps that make sense on already-extracted, unprocessed images).
* **Fallback — no `.cbz` found**: if the target folder contains no archive, `local.py` looks for subfolders that already contain images (searched recursively inside each one) instead of giving up. If it finds at least one, extraction is skipped entirely — nothing is touched or deleted — and `main.py` is launched directly against the target folder with the same `--local --skip-step 1 3 4 5.1 8 9` flags as above. This covers folders you've already extracted by hand, or received pre-extracted.
  * Which extensions count as "image" is read from `supported_extensions` in the `config.yaml` sitting next to `local.py` (same file `main.py` uses); if that config can't be read, a default list (`jpg, jpeg, png, webp, avif, bmp, gif`) is used instead.
  * If neither a `.cbz` nor an image subfolder is found, `local.py` exits cleanly (status `0`) without launching `main.py`.
* If any archive fails to extract, none of the `.cbz` files are deleted and `main.py` is **not** launched, so you can inspect the problem safely.
* Exits with a non-zero status code on any extraction failure or if `main.py` itself fails, so the script can be chained in automation that checks the exit code.

---

## Advanced Configuration

`config.example.yaml` is the template — copy it to `config.yaml` and adjust every path. All keys below are required; the script validates their presence and type at startup.

---

## Troubleshooting

* **Missing Binaries**: If `magick` isn't found and `step_1` is active, the CLI offers to skip Step 1 for this run. If neither `7z` nor `7za` is found, it offers to fall back to Python's `zipfile` module for compression. Declining either prompt aborts the run with the list of missing tools.
* **Invalid or incomplete `config.yaml`**: The script exits immediately with the list of missing/mistyped keys — check against `config.example.yaml`.
* **AVIF Issues**: Verify that your ImageMagick installation includes `libavif` support if Step 1 fails on valid files.
* **Locked Files**: Ensure no external programs (viewers, file explorers) are locking your directories during the workflow.
* **Web UI unreachable**: The server only binds to `127.0.0.1:<web_port>` — it's local-only by design and won't be reachable from other devices. If `web_port` is already taken (e.g. the Hash Maintenance tool is already running), the next free port is used automatically and printed to the console at startup — check there for the actual URL if it's not the one you expected.
* **Hash Maintenance tool shows unexpected/missing hashes**: this is almost always a path mismatch — the tool defaults to the `config.yaml` sitting next to `hash_maintenance.py`. Check the console output at startup: it prints the exact config file and both database paths it resolved, plus how many hashes were loaded from each.
* **Logging**: Every operation is logged in the path specified by `log_path` in `config.yaml` (UTF-8 encoded), unless `log_enabled` is set to `false`. The destination folder is created automatically if missing; use `--log-path` to override the location for a single run.
* **Keyboard shortcut not working as expected**: an unknown entry name, an empty value, or two actions bound to the same key under `keyboard_shortcuts` in `config.yaml` all print a `Warning:` line to the console at startup (the affected binding falls back to its default) — check there first.

---

## Security Notice

* All operations are **non-destructive** by default logic: suffix-safe renaming resolves naming conflicts, and the destination folder is never automatically cleaned by the tool.
* The Web UI server is bound to `127.0.0.1` only and is never exposed to the network. The standalone Hash Maintenance tool follows the same rule.
* `mask_security_popups: true` auto-accepts every confirmation dialog in the Web UI (including destructive actions like deletions and splits) — only enable it once you trust your review workflow, since it removes the "are you sure?" safety net.
