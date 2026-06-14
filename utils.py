import os
import sys
import io
import shutil
import logging
import re
from pathlib import Path
from rich.console import Console

console = Console()

def setup_environment(log_path):
    """Enforce UTF-8 encoding and setup logging."""
    if os.name == 'nt':
        os.system('chcp 65001 >nul 2>&1')
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    logging.basicConfig(
        filename=log_path,
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        encoding='utf-8'
    )
    logging.info("Workflow started.")

def check_prerequisites():
    """Check for ImageMagick and 7-Zip."""
    missing = []
    if not shutil.which('magick'):
        missing.append("ImageMagick v7+ (magick)")
    if not shutil.which('7z') and not shutil.which('7za'):
        missing.append("7-Zip (7z)")
        
    if missing:
        console.print("[bold red]Critical Error: Missing Prerequisites[/bold red]")
        for item in missing:
            console.print(f"[red]- {item}[/red]")
        console.print("\n[yellow]Please install the missing tools and ensure they are added to your system PATH.[/yellow]")
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