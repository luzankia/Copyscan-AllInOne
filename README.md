# Image Workflow Automation Toolkit

> **Automated, multi-step workflow for images sets. CLI and Web hybrid.**

---

## Features

- **Cross-platform**: Windows & Linux compatible
- **Customizable** via `config.yaml` and CLI overrides
- **CLI UX**: Color, progress & errors with [rich](https://github.com/Textualize/rich)
- **Fast, responsive Web UI** (for manual sorting)
- **External tools integration**: [ImageMagick](https://imagemagick.org/) and [7-Zip](https://www.7-zip.org/)
- **Safe, no-data-loss logic** in all rename/merge/move operations

---

## Installation

### 1. Clone or download this repository

```shell
git clone <your-repo-url>
cd <repo-folder>
```

### 2. Install required Python dependencies

```shell
pip install -r requirements.txt
```

### 3. Install **ImageMagick 7+** and **7-Zip**

- [Download ImageMagick](https://imagemagick.org/script/download.php) and add `magick` to your system PATH.
- [Download 7-Zip](https://www.7-zip.org/) (or install via your package manager) and add `7z` (or `7za`) to your PATH.

---

## Directory Structure

Input hierarchy must be:

```
Root/
  └─ Parent1/
      └─ Parent2/
          └─ Leaf/
                ├─ image1.jpg
                └─ ...
```
- **Root** contains only directories.
- **Parent1** contains only directories.
- **Parent2** contains only directories (before Step 5), then `.cbz` files.
- **Leaf** contains only image files before Step 5 and are replaced by .cbz files after.

---

## Quick Start

1. **Edit `config.yaml`** to set your paths and options.
2. **Put your data in the hierarchy described above.**
3. **Run the workflow:**
   ```shell
   python main.py
   ```

**Examples:**
- To skip the manual sort step (Web UI):  
  `python main.py --skip-step 2`
- To change destination folder via CLI:  
  `python main.py --dest-dir ./other_output/`

See `python main.py --help` for advanced options.

---

## Workflow Steps Overview

1. **Integrity Check** (*magick*): Detect and report corrupted images.
2. **Manual Sort (Web UI)**: Review/delete first image of every Leaf via browser.
3. **Regex Cleanup**: Delete files matching configured patterns.
4. **Empty Folder Pruning**: Remove all empty folders.
5. **Leaf Folder Renaming**: Regex-based, conflict-proof.
6. **Compression**: Each Leaf folder to `[Leaf].cbz` with archive validation.
7. **Rename Parent2 from CSV**: Rename/merge and append suffixes according to `csv_1_path` and `csv_2_path`.
8. **Final Move & Cleanup**: Move results to output, handle all merge conflicts, purge empty folders.

**Each step can be activated/deactivated in `config.yaml` and/or via CLI.**

---

## Advanced Configuration

- **All defaults and enabling/disabling steps are in `config.yaml`.**
- To override (except regex): use CLI arguments. All options documented in `main.py` and comments in `config.yaml`.

#### Example `config.yaml` (simplified)

```yaml
root_dir: "./data_input"
dest_dir: "./data_output"
sleep_time: 5
im_timeout: 10
web_port: 5000
delete_regex:
  - '(?i).*\.txt$'
rename_regex:
  - pattern: '(?i)^chapter\s*(\d+)$'
    replacement: 'Ch \1'
steps_active:
  step_1: true
  step_2: true
  ...
```

---

## Troubleshooting

- **Missing magick/7z:** The CLI will exit and print installation help if a binary is not detected.
- **Encoding issues on Windows:** The script enforces UTF-8 in the console. If you still see garbled Unicode, ensure your terminal supports UTF-8.
- **Locked files (especially on Windows):** Close all folders/files you may have open before running.

---

## Logging

- All workflow actions, errors, and warnings are stored in the file set by `log_path` in UTF-8.

---

## Security Notice

- **No original files are lost** due to naming conflicts: all merges/renames/compressions are suffix-safe and non-destructive.
- Your output folder will never be cleaned; only empty source subfolders will be deleted at the end as specified.

---

**Made for high-volume batch image pipelines for my personal use.**

---
