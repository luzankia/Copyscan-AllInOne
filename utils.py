import os
import sys
import io
import shutil
import logging
import re
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt

console = Console()

def setup_environment(log_path, log_enabled=True):
    """Enforce UTF-8 encoding and setup logging."""
    if os.name == 'nt':
        os.system('chcp 65001 >nul 2>&1')
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    if not log_enabled:
        # Logging disabled via config.yaml (log_enabled: false): route logs to a
        # null handler so any logging.info/error call elsewhere in the code
        # never touches disk and never crashes.
        logging.basicConfig(handlers=[logging.NullHandler()], level=logging.CRITICAL)
        return

    log_path = Path(log_path)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        console.print(f"[bold red]Unable to create log directory '{log_path.parent}': {e}[/bold red]")
        sys.exit(1)

    logging.basicConfig(
        filename=str(log_path),
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        encoding='utf-8'
    )
    logging.info("Workflow started.")

def check_prerequisites(config):
    """Check for ImageMagick and 7-Zip, accounting for whether Step 1 is active
    and offering a zipfile fallback for compression when 7-Zip is missing."""
    steps_active = config.get('steps_active', {})
    step_1_active = steps_active.get('step_1', True)

    # --- ImageMagick check (only relevant if Step 1 is active) ---
    if step_1_active and not shutil.which('magick'):
        console.print("[bold red]ImageMagick (magick) is required for Step 1 (Integrity Check) but was not found.[/bold red]")
        choice = Prompt.ask(
            "[bold yellow]Action required[/bold yellow]: [1] Skip Step 1 and continue, [2] Abort",
            choices=["1", "2"]
        )
        if choice == "1":
            steps_active['step_1'] = False
            config['steps_active'] = steps_active
            console.print("[yellow]Step 1 will be skipped for this run.[/yellow]")
        else:
            console.print("[bold red]Critical Error: Missing Prerequisites[/bold red]")
            console.print("[red]- ImageMagick v7+ (magick)[/red]")
            console.print("\n[yellow]Please install ImageMagick and ensure it is added to your system PATH.[/yellow]")
            input("\nPress Enter to exit...")
            sys.exit(1)

    # --- 7-Zip check (7-Zip stays the preferred compressor; zipfile is a fallback) ---
    if shutil.which('7z') or shutil.which('7za'):
        config['use_zipfile_fallback'] = False
    else:
        console.print("[bold red]7-Zip (7z/7za) was not found.[/bold red]")
        console.print("[yellow]7-Zip remains the preferred compressor, but Python's built-in zipfile module can be used as a fallback for Step 6.[/yellow]")
        choice = Prompt.ask(
            "[bold yellow]Action required[/bold yellow]: [1] Use the zipfile fallback, [2] Abort",
            choices=["1", "2"]
        )
        if choice == "1":
            config['use_zipfile_fallback'] = True
            console.print("[yellow]Step 6 will use Python's zipfile module instead of 7-Zip.[/yellow]")
        else:
            console.print("[bold red]Critical Error: Missing Prerequisites[/bold red]")
            console.print("[red]- 7-Zip (7z)[/red]")
            console.print("\n[yellow]Please install 7-Zip and ensure it is added to your system PATH.[/yellow]")
            input("\nPress Enter to exit...")
            sys.exit(1)

def get_leaf_dirs(root_dir: Path):
    """Yield all Leaf directories (Root -> Parent1 -> Parent2 -> Leaf)."""
    if not root_dir.exists():
        return
    for p1 in root_dir.iterdir():
        if p1.is_dir():
            for p2 in p1.iterdir():
                if p2.is_dir():
                    for leaf in p2.iterdir():
                        if leaf.is_dir():
                            yield leaf

def get_parent2_dirs(root_dir: Path):
    """Yield all Parent2 directories."""
    if not root_dir.exists():
        return
    for p1 in root_dir.iterdir():
        if p1.is_dir():
            for p2 in p1.iterdir():
                if p2.is_dir():
                    yield p1, p2

def resolve_conflict(target_path: Path, is_file=False) -> Path:
    """Resolve naming conflicts by appending ' (x)'."""
    if not target_path.exists():
        return target_path
    
    directory = target_path.parent
    name = target_path.stem
    ext = target_path.suffix if is_file else ""
    
    counter = 1
    while True:
        new_name = f"{name} ({counter}){ext}"
        new_path = directory / new_name
        if not new_path.exists():
            return new_path
        counter += 1

def merge_directories(src_dir: Path, dest_dir: Path, error_list: list):
    """Safely merge src_dir into dest_dir, handling file conflicts without data loss."""
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in src_dir.iterdir():
            if item.is_file():
                dest_file = dest_dir / item.name
                if dest_file.exists():
                    dest_file = resolve_conflict(dest_file, is_file=True)
                shutil.move(str(item), str(dest_file))
            elif item.is_dir():
                merge_directories(item, dest_dir / item.name, error_list)
        
        # Remove empty source directory after merge
        if not any(src_dir.iterdir()):
            src_dir.rmdir()
    except Exception as e:
        error_list.append(f"Merge error {src_dir} -> {dest_dir}: {str(e)}")
        logging.error(f"Merge error {src_dir}: {str(e)}")