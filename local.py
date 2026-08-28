import sys
import zipfile
import argparse
import subprocess
import yaml
from pathlib import Path
from typing import Optional

from utils import console


# Extensions used when no config.yaml is found (or it's missing/invalid),
# so subfolder-image detection still works. Kept in sync with
# config.example.yaml's default `supported_extensions` list.
DEFAULT_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp", ".gif"}


def has_single_root_folder(zip_ref: zipfile.ZipFile) -> bool:
    """
    Check whether the ZIP archive's content sits under a single root folder.
    """
    namelist = zip_ref.namelist()

    # Grab the first path component of every entry (the root-level name)
    root_items = set(path.split('/')[0] for path in namelist if path.strip('/'))

    # If there is a single root item, confirm it really is a folder
    if len(root_items) == 1:
        root_item = next(iter(root_items))
        for path in namelist:
            # A path with a '/' inside that root item means it's a folder
            if '/' in path and path.startswith(root_item + '/'):
                return True

    return False


def get_supported_extensions() -> set:
    """
    Read `supported_extensions` from config.yaml next to this script (the
    same file main.py will load), falling back to DEFAULT_SUPPORTED_EXTENSIONS
    if config.yaml is missing, unreadable, or doesn't define the key.

    local.py deliberately doesn't hard-fail on a config problem here: it's
    only used to detect whether pre-existing subfolders already contain
    images, main.py will do the real, strict config validation right after.
    """
    config_path = Path(__file__).resolve().parent / "config.yaml"
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        extensions = config.get('supported_extensions')
        if isinstance(extensions, list) and extensions:
            return {str(ext).lower() for ext in extensions}
    except Exception:
        pass
    return DEFAULT_SUPPORTED_EXTENSIONS


def find_image_subfolders(target_path: Path, extensions: set) -> list:
    """
    Return the direct subfolders of target_path that contain at least one
    image file (searched recursively inside each subfolder), matching the
    flat `root_dir/Leaf` layout expected by --local.
    """
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
    """
    Try to open a native folder-selection dialog (tkinter). Returns the
    selected path, or None if no GUI toolkit / display is available, or the
    user cancels the dialog -- callers should fall back to a text prompt.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        # tkinter isn't installed -- common on minimal/headless Linux setups,
        # e.g. RHEL without the python3-tkinter package.
        return None

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(title="Select the folder containing the .cbz files")
        root.destroy()
    except tk.TclError:
        # tkinter is installed but no display is available (e.g. an SSH
        # session without X11 forwarding).
        return None

    return selected or None


def main():
    # 1. Command-line argument handling
    parser = argparse.ArgumentParser(description="Smartly extract .cbz archives.")
    parser.add_argument("-d", "--dir", type=str, help="Path to the folder containing the .cbz archives")
    parser.add_argument(
        "--no-gui", action="store_true",
        help="Skip the folder-picker dialog and always prompt on the command line"
    )
    args = parser.parse_args()

    target_dir = args.dir

    # 2. Interactive selection if no argument was given: try the GUI folder
    # picker first (unless disabled or unavailable), then fall back to a
    # plain text prompt.
    if not target_dir and not args.no_gui:
        target_dir = pick_directory_via_gui()

    if not target_dir:
        target_dir = input("Please enter the path to the folder containing the .cbz files: ").strip()

    if not target_dir:
        console.print("[bold red]Error: no folder was provided.[/bold red]")
        sys.exit(1)

    # Clean up and validate the path
    target_path = Path(target_dir).resolve()
    if not target_path.is_dir():
        console.print(f"[bold red]Error: the specified folder was not found -> {target_path}[/bold red]")
        sys.exit(1)

    # 3. Look for .cbz files
    cbz_files = sorted(p for p in target_path.iterdir() if p.is_file() and p.suffix.lower() == '.cbz')

    if cbz_files:
        console.print(f"[bold magenta]Found {len(cbz_files)} .cbz file(s). Starting processing...[/bold magenta]")

        all_successful = True
        processed_paths = []

        # 4. Extract archives
        for cbz_path in cbz_files:
            try:
                with zipfile.ZipFile(cbz_path, 'r') as zip_ref:
                    if has_single_root_folder(zip_ref):
                        console.print(f"[cyan][Single folder][/cyan] Extracting: {cbz_path.name}")
                        # Extract straight into target_path; the archive's own root folder does the rest
                        zip_ref.extractall(path=target_path)
                    else:
                        console.print(f"[cyan][Multiple files][/cyan] Extracting: {cbz_path.name}")
                        # Create a folder named after the archive
                        extract_path = target_path / cbz_path.stem
                        extract_path.mkdir(exist_ok=True)
                        zip_ref.extractall(path=extract_path)

                processed_paths.append(cbz_path)

            except Exception as e:
                console.print(f"[bold red]Error while extracting {cbz_path.name}: {e}[/bold red]")
                all_successful = False

        # 5. Remove the archives and launch the secondary script
        if not all_successful:
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
        # No .cbz found: check whether the folder already holds subfolders
        # with images (e.g. archives extracted manually beforehand), and if
        # so, skip straight to the processing step without touching anything.
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

    # Resolve main.py relative to this script's own directory rather than the
    # process's current working directory, so `local.py` also works when
    # launched from somewhere other than the repo folder.
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
        # Run main.py, letting it take over and print its own output
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"\n[bold red]Error: main.py exited with error code {e.returncode}[/bold red]")
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
