# Image Workflow Automation Toolkit

> **Automated, multi-step workflow for comic, manga, and webtoon image sets. CLI and Web hybrid.**

---

## Features

* **Cross-platform**: Windows & Linux compatible.
* **Customizable**: Via `config.yaml` and CLI overrides.
* **CLI UX**: Color, progress, and error reporting powered by [rich](https://github.com/Textualize/rich).
* **Advanced Web UI**:
* **Global Sort**: Fast manual review of all directories.
* **Leaf Editor**: Open specific leaf folders in an independent tab to:
* Permanently delete individual pages.
* **Vertically merge consecutive images** (In case the hosting site do some weerd horizontal cut).


* Responsive CSS tooltips and dynamic thumbnails.


* **External tools integration**: [ImageMagick](https://imagemagick.org/) (for integrity checks) and [7-Zip](https://www.7-zip.org/) (for compression).
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

* **ImageMagick**: Download and install from [official site](https://imagemagick.org/script/download.php). Ensure `magick` is in your system PATH. *(Required: Build with AVIF/HEIC support for relevant file types).*
* **7-Zip**: Install and ensure `7z` or `7za` is accessible via your PATH.

---

## Directory Structure

Required hierarchy for processing:

```text
Root/
  └─ Site (Parent1)/
      └─ Serie (Parent2)/
          └─ Chapter (Leaf)/
                ├─ image1.jpg
                └─ ...

```

---

## Quick Start

1. **Configure**: Edit `config.yaml` to set your input/output paths and feature toggles.
2. **Organize**: Place data into the hierarchy described above.
3. **Execute**:
```shell
python main.py

```



* **CLI Overrides**: Use `--skip-step` (e.g., `python main.py --skip-step 2`) to bypass specific stages. Use `python main.py --help` for full options.

---

## Workflow Steps Overview

1. **Integrity Check**: Strict validation of images using `magick identify -verbose`.
2. **Manual Sort & Edit (Web UI)**: Global review to delete files, with access to a dedicated editor for every Leaf folder to perform complex image merging or targeted deletions.
3. **Regex Cleanup**: Automated deletion of files matching defined patterns.
4. **Empty Folder Pruning**: Recursive cleanup of empty directory structures.
5. **Leaf Folder Renaming**: Regex-based, conflict-proof folder naming.
5.1. **Hash Suffix Cleaning**: Automatically strips trailing hashes (e.g., `_a1b2c3d4`) from folder names.
6. **Compression**: Parallelized compression of Leaf folders into `[Leaf].cbz` with archive validation.
7. **CSV Operations**: Batch rename and merge Parent2 folders based on external CSV mapping.
8. **Final Move & Cleanup**: Deployment of results to the destination directory and final purge of empty source folders.

---

## Advanced Configuration

`config.yaml` provides fine-grained control:

```yaml
# Example snippet
root_dir: "./data_input"
dest_dir: "./data_output"
web_port: 5002
thumb_size: "220px"
delete_regex:
  - '^\.nomedia$'
rename_regex:
  - pattern: '^(?:.*_)?(?:Chapter|Ch\.)\s*(\d+(?:\.\d+)?)...'
    replacement: 'Ch.\1'

```

---

## Troubleshooting

* **Missing Binaries**: The CLI will alert you if `magick` or `7z` are not found in your PATH.
* **AVIF Issues**: Verify that your ImageMagick installation includes `libavif` support if Step 1 fails on valid files.
* **Locked Files**: Ensure no external programs (viewers, file explorers) are locking your directories during the workflow.
* **Logging**: Every operation is logged in the path specified in `config.yaml` (UTF-8 encoded).

---

## Security Notice

* All operations are **non-destructive**. The script uses suffix-safe logic to resolve conflicts, and the destination folder is never automatically cleaned by the tool.