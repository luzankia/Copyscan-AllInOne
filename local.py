"""
Local extraction helper for Copyscan-AllInOne.

Launcher for a one-off, flat-layout run: point it at a folder of .cbz
archives (or already-extracted image subfolders) and it prepares that
folder, then hands off to main.py with --local and only the steps that
make sense on raw, unprocessed images (Renaming, Renumbering, Compression).

Folder selection order: -d/--dir CLI arg > GUI folder picker (tkinter) >
plain text prompt. Pass --no-gui to skip the picker.

If .cbz files are found: each is extracted (into the target folder itself
if its content already sits under a single root folder, otherwise into a
folder named after the archive), then the archives are deleted.

If no .cbz is found: falls back to scanning for subfolders that already
contain images, so pre-extracted content is picked up without any archive
to unzip. Extensions come from config.yaml's `supported_extensions`.

Either way, main.py is then launched with:
    --local --skip-step 1 3 4 5.1 8 9
"""

import sys
import zipfile
import argparse
import subprocess
import yaml
from pathlib import Path
from typing import Optional

from utils import console


def has_single_root_folder(zip_ref: zipfile.ZipFile) -> bool:
    """Check whether the archive's content sits under one single root folder."""
    namelist = zip_ref.namelist()
    root_items = set(path.split('/')[0] for path in namelist if path.strip('/'))

    if len(root_items) == 1:
        root_item = next(iter(root_items))
        for path in namelist:
            if '/' in path and path.startswith(root_item + '/'):
                return True

    return False


def get_supported_extensions() -> set:
    """Read `supported_extensions` from config.yaml next to this script.
    """
    config_path = Path(__file__).resolve().parent / "config.yaml"
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        console.print(f"[bold red]Error: could not read config.yaml ({config_path}): {e}[/bold red]")
        console.print("[yellow]Copy 'config.example.yaml' to 'config.yaml' and adjust it to your setup.[/yellow]")
        sys.exit(1)

    extensions = config.get('supported_extensions')
    if not isinstance(extensions, list) or not extensions:
        console.print(f"[bold red]Error: 'supported_extensions' is missing or invalid in {config_path}.[/bold red]")
        sys.exit(1)

    return {str(ext).lower() for ext in extensions}


def find_image_subfolders(target_path: Path, extensions: set) -> list:
    """Return direct subfolders of target_path containing at least one image
    (searched recursively), matching the flat root_dir/Leaf layout."""
    subfolders = []
    for entry in sorted(target_path.iterdir()):
        if not entry.is_dir():
            continue
        has_image = any(
            f.is_file() and f.suffix.lower() in extensions
            for f in entry.rglob('*')
        )
        if has_image:
            subfolders.append(entry)
    return subfolders


def pick_directory_via_gui() -> Optional[str]:
    """Open a native folder picker (tkinter). Returns None if tkinter isn't
    installed, no display is available, or the user cancels."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(title="Select the folder containing the .cbz files")
        root.destroy()
    except tk.TclError:
        # No display available (e.g. SSH session without X11 forwarding).
        return None

    return selected or None


def main():
    parser = argparse.ArgumentParser(description="Smartly extract .cbz archives.")
    parser.add_argument("-d", "--dir", type=str, help="Path to the folder containing the .cbz archives")
    parser.add_argument(
        "--no-gui", action="store_true",
        help="Skip the folder-picker dialog and always prompt on the command line"
    )
    args = parser.parse_args()

    target_dir = args.dir

    # Interactive fallback if no --dir was given: GUI picker first, then a plain text prompt.
    if not target_dir and not args.no_gui:
        target_dir = pick_directory_via_gui()
    if not target_dir:
        target_dir = input("Please enter the path to the folder containing the .cbz files: ").strip()

    if not target_dir:
        console.print("[bold red]Error: no folder was provided.[/bold red]")
        sys.exit(1)

    target_path = Path(target_dir).resolve()
    if not target_path.is_dir():
        console.print(f"[bold red]Error: the specified folder was not found -> {target_path}[/bold red]")
        sys.exit(1)

    cbz_files = sorted(p for p in target_path.iterdir() if p.is_file() and p.suffix.lower() == '.cbz')

    if cbz_files:
        console.print(f"[bold magenta]Found {len(cbz_files)} .cbz file(s). Starting processing...[/bold magenta]")

        all_successful = True
        processed_paths = []

        for cbz_path in cbz_files:
            try:
                with zipfile.ZipFile(cbz_path, 'r') as zip_ref:
                    if has_single_root_folder(zip_ref):
                        # Archive's own root folder becomes the chapter folder.
                        console.print(f"[cyan][Single folder][/cyan] Extracting: {cbz_path.name}")
                        zip_ref.extractall(path=target_path)
                    else:
                        console.print(f"[cyan][Multiple files][/cyan] Extracting: {cbz_path.name}")
                        extract_path = target_path / cbz_path.stem
                        extract_path.mkdir(exist_ok=True)
                        zip_ref.extractall(path=extract_path)

                processed_paths.append(cbz_path)

            except Exception as e:
                console.print(f"[bold red]Error while extracting {cbz_path.name}: {e}[/bold red]")
                all_successful = False

        if not all_successful:
            # Any failure keeps every .cbz on disk and aborts before main.py runs.
            console.print(
                "\n[bold red]Errors occurred during extraction. For safety, the original .cbz files "
                "were not removed and main.py will not be run.[/bold red]"
            )
            sys.exit(1)

        console.print("\n[bold green]Extraction completed successfully. Cleaning up archives...[/bold green]")
        for cbz_path in processed_paths:
            try:
                cbz_path.unlink()
                console.print(f"  -> Removed: {cbz_path.name}")
            except Exception as e:
                console.print(f"[bold red]Error while removing {cbz_path.name}: {e}[/bold red]")

    else:
        # No archive: check for subfolders already containing images (manual extraction).
        extensions = get_supported_extensions()
        image_subfolders = find_image_subfolders(target_path, extensions)

        if not image_subfolders:
            console.print("[yellow]No .cbz file and no subfolder containing images was found in the folder.[/yellow]")
            sys.exit(0)

        console.print(
            f"[bold magenta]No .cbz file found, but {len(image_subfolders)} subfolder(s) with images "
            f"were detected. Skipping extraction and moving straight to processing...[/bold magenta]"
        )
        for folder in image_subfolders:
            console.print(f"[cyan][Detected][/cyan] {folder.name}")

    # Resolved next to this script so local.py works regardless of the launch cwd.
    main_py = Path(__file__).resolve().parent / "main.py"
    if not main_py.is_file():
        console.print(f"[bold red]Error: could not find main.py next to {Path(__file__).name}.[/bold red]")
        sys.exit(1)

    console.print("\n[bold magenta]Launching main.py...[/bold magenta]")
    cmd = [
        sys.executable, str(main_py),
        "--root-dir", str(target_path),
        "--skip-step", "1", "3", "4", "5.1", "8", "9",
        "--local"
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"\n[bold red]Error: main.py exited with error code {e.returncode}[/bold red]")
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()