import subprocess
import time
import re
import csv
import os
import logging
import shutil
import webbrowser
import zipfile
from pathlib import Path
from rich.progress import Progress
from rich.prompt import Prompt
from concurrent.futures import ThreadPoolExecutor

from utils import console, get_leaf_dirs, get_parent2_dirs, merge_directories, resolve_conflict
from web_ui import start_web_ui

def handle_step_error(errors, step_name, allow_rescan=False) -> str:
    """Standardized error handler for all steps."""
    if not errors:
        console.print(f"[bold green]✓ {step_name} completed successfully.[/bold green]")
        return "next"
    
    console.print(f"[bold red]✗ Errors occurred during {step_name}:[/bold red]")
    for err in errors:
        logging.warning(f"[{step_name}] {err}")
    
    for err in errors:
        console.print(f"[red]  - {err}[/red]")
        
    choices = ["1", "2", "3"] if allow_rescan else ["1", "2"]
    prompt_msg = "[1] Rescan, [2] Next step, [3] Quit" if allow_rescan else "[1] Next step, [2] Quit"
    
    choice = Prompt.ask(f"[bold yellow]Action required for {step_name}[/bold yellow]: {prompt_msg}", choices=choices)
    
    if allow_rescan:
        if choice == "1": return "rescan"
        if choice == "2": return "next"
        if choice == "3": return "quit"
    else:
        if choice == "1": return "next"
        if choice == "2": return "quit"

def step_1_integrity(config):
    root_dir = Path(config['root_dir'])
    timeout = config['im_timeout']
    exts = set(config['supported_extensions'])

    while True:
        errors = []
        files_to_check = []
        for leaf in get_leaf_dirs(root_dir):
            files_to_check.extend([
                f for f in leaf.iterdir()
                if f.is_file() and f.suffix.lower() in exts
            ])

        if not files_to_check:
            console.print("[blue]Step 1: No image files found to check.[/blue]")
            return "next"

        # Function for checking a single file
        def check_single_file(file_path):
            try:
                # Adding the "-verbose" option for ultra-strict detection
                res = subprocess.run(
                    ["magick", "identify", "-verbose", "-regard-warnings", str(file_path)],
                    capture_output=True,
                    timeout=timeout
                )
                if res.returncode != 0:
                    logging.warning(f"Corrupted image: {file_path}")
                    return f"Corrupted: {file_path}"
            except subprocess.TimeoutExpired:
                logging.warning(f"ImageMagick timeout: {file_path}")
                return f"Timeout: {file_path}"
            except Exception as e:
                return f"Error {file_path}: {e}"
            return None

        with Progress() as progress:
            task = progress.add_task("[cyan]Step 1: Checking image integrity (Strict Parallel)...", total=len(files_to_check))
            
            # Using ThreadPoolExecutor for parallelizing process calls
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                futures = [executor.submit(check_single_file, f) for f in files_to_check]
                
                for future in futures:
                    result = future.result()
                    if result:
                        errors.append(result)
                    progress.advance(task)
        
        action = handle_step_error(errors, "Step 1 (Integrity Check)", allow_rescan=True)
        if action != "rescan":
            return action

def step_2_web_ui(config):
    root_dir = Path(config['root_dir'])
    port = config['web_port']
    exts = set(config['supported_extensions'])

    first_images = []
    for leaf in get_leaf_dirs(root_dir):
        files = sorted([
            f for f in leaf.iterdir() 
            if f.is_file() and f.suffix.lower() in exts
        ], key=lambda x: x.name)

        if files:
            first_images.append(files[0])

    if not first_images:
        console.print("[blue]Step 2: No images found for Web UI.[/blue]")
        return "next"

    console.print(f"[cyan]Step 2: Starting Web Server[/cyan]")

    mask_popups = config.get('mask_security_popups', False)
    server_thread, completion_event = start_web_ui(first_images, port, config['thumb_size'], exts, mask_popups)
    
    url = f"http://127.0.0.1:{port}"
    webbrowser.open(url)
    console.print(f"[bold green]Web UI ready. Open {url} if your browser didn't launch.[/bold green]")
    console.print("[yellow]Waiting for validation from Web UI...[/yellow]")
    
    completion_event.wait()
    server_thread.shutdown()
    server_thread.join()
    
    console.print("[bold green]✓ Web UI manual sort completed.[/bold green]")
    return "next"

def step_3_regex_clean(config):
    root_dir = Path(config['root_dir'])
    patterns = [re.compile(p) for p in config['delete_regex']]
    errors = []
    
    files = []
    for leaf in get_leaf_dirs(root_dir):
        files.extend([f for f in leaf.iterdir() if f.is_file()])
        
    with Progress() as progress:
        task = progress.add_task("[cyan]Step 3: Regex file cleanup...", total=len(files))
        for f in files:
            for pattern in patterns:
                if pattern.match(f.name):
                    try:
                        f.unlink()
                        logging.info(f"Regex deleted: {f}")
                    except Exception as e:
                        errors.append(str(f))
                        logging.error(f"Regex delete failed {f}: {e}")
                    break
            progress.advance(task)
            
    return handle_step_error(errors, "Step 3 (Regex Cleanup)")

def step_4_delete_empty(config):
    root_dir = str(Path(config['root_dir']).resolve())
    errors = []

    dirs_to_check = [
        d for d, _, _ in os.walk(root_dir, topdown=False)
        if d != root_dir
    ]

    with Progress() as progress:
        task = progress.add_task("[cyan]Step 4: Deleting empty folders...", total=len(dirs_to_check))
        for dirpath in dirs_to_check:
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except Exception as e:
                errors.append(dirpath)
                logging.error(f"Could not remove empty dir {dirpath}: {e}")
            progress.advance(task)

    return handle_step_error(errors, "Step 4 (Empty Folder Deletion)")

def step_5_rename_leaf(config):
    root_dir = Path(config['root_dir'])
    rename_rules = config['rename_regex']
    errors = []
    
    leafs = list(get_leaf_dirs(root_dir))
    
    with Progress() as progress:
        task = progress.add_task("[cyan]Step 5: Renaming Leaf folders...", total=len(leafs))
        for leaf in leafs:
            matched = False
            for rule in rename_rules:
                pattern = re.compile(rule['pattern'])
                if pattern.match(leaf.name):
                    new_name = pattern.sub(rule['replacement'], leaf.name)
                    new_path = leaf.parent / new_name
                    
                    if new_path != leaf:
                        new_path = resolve_conflict(new_path, is_file=False)
                        try:
                            leaf.rename(new_path)
                            logging.info(f"Renamed leaf: {leaf.name} -> {new_path.name}")
                        except Exception as e:
                            errors.append(str(leaf))
                            logging.error(f"Rename failed {leaf} -> {new_path}: {e}")
                    matched = True
                    break
            if not matched:
                console.print(f"[blue]Info: No regex match for Leaf -> {leaf.parent.name}/{leaf.name}[/blue]")
            progress.advance(task)
            
    return handle_step_error(errors, "Step 5 (Rename Leaf)")

def step_5_1_clean_hash_suffix(config):
    root_dir = Path(config['root_dir'])
    errors = []
    
    pattern = re.compile(r'^(.+)_[A-Za-z0-9]+$')
    leafs = list(get_leaf_dirs(root_dir))
    
    with Progress() as progress:
        task = progress.add_task("[cyan]Step 5.1: Cleaning hash suffixes from Leaf folders...", total=len(leafs))
        for leaf in leafs:
            match = pattern.match(leaf.name)
            if match:
                # The first group contains the cleaned folder name
                new_name = match.group(1).strip()
                new_path = leaf.parent / new_name
                
                if new_path != leaf:
                    # Native conflict resolution (handles duplicates ' (1)', ' (2)', etc.)
                    new_path = resolve_conflict(new_path, is_file=False)
                    try:
                        leaf.rename(new_path)
                        logging.info(f"Cleaned hash suffix: {leaf.name} -> {new_path.name}")
                    except Exception as e:
                        errors.append(str(leaf))
                        logging.error(f"Hash cleanup failed {leaf} -> {new_path}: {e}")
            progress.advance(task)
            
    return handle_step_error(errors, "Step 5.1 (Clean Hash Suffix)")

def step_6_compress(config):
    root_dir = Path(config['root_dir'])
    errors = []
    
    leafs = list(get_leaf_dirs(root_dir))
    use_zipfile_fallback = config.get('use_zipfile_fallback', False)
    executable = "7za" if shutil.which("7za") else "7z"

    def compress_with_zipfile(leaf, archive_path):
        """Fallback compressor using Python's built-in zipfile module (no 7-Zip required)."""
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file in leaf.rglob('*'):
                if file.is_file():
                    zf.write(file, arcname=file.relative_to(leaf))

        # Integrity test: testzip() returns the name of the first bad file, or None if all is well
        with zipfile.ZipFile(archive_path, 'r') as zf:
            bad_file = zf.testzip()
            if bad_file is not None:
                raise RuntimeError(f"Corrupted entry in archive: {bad_file}")

    def compress_with_7z(leaf, archive_path):
        """Preferred compressor: 7-Zip via subprocess."""
        # Note: we force 7z to only use 2 threads.
        # To avoid a single archive from consuming all CPU cores.
        cmd_add = [executable, "a", "-tzip", "-r", "-mmt=2", str(archive_path), str(leaf) + os.sep]
        res_add = subprocess.run(cmd_add, capture_output=True)
        if res_add.returncode != 0:
            logging.error(f"7z compress stderr: {res_add.stderr.decode(errors='replace')}")
            raise RuntimeError(f"Compression failed: {leaf}")

        cmd_test = [executable, "t", str(archive_path)]
        res_test = subprocess.run(cmd_test, capture_output=True)
        if res_test.returncode != 0:
            logging.error(f"7z test stderr: {res_test.stderr.decode(errors='replace')}")
            raise RuntimeError(f"Test failed for archive: {archive_path}")

    # Function for compressing a single Leaf folder
    def compress_single_leaf(leaf):
        archive_path = leaf.parent / f"{leaf.name}.cbz"
        archive_path = resolve_conflict(archive_path, is_file=True)
        
        try:
            if use_zipfile_fallback:
                compress_with_zipfile(leaf, archive_path)
            else:
                compress_with_7z(leaf, archive_path)

            shutil.rmtree(leaf)
            logging.info(f"Compressed and removed: {leaf}")
        except Exception as e:
            return f"Compression error on {leaf}: {e}"
        return None

    with Progress() as progress:
        task = progress.add_task("[cyan]Step 6: Compressing Leaf folders (Parallel)...", total=len(leafs))
        
        # We limit the number of concurrent threads to a reasonable amount (e.g., 4 folders at a time)
        # to avoid overwhelming the disk with simultaneous write operations.
        max_compress_threads = min(4, os.cpu_count() or 1)
        with ThreadPoolExecutor(max_workers=max_compress_threads) as executor:
            futures = [executor.submit(compress_single_leaf, leaf) for leaf in leafs]
            
            for future in futures:
                result = future.result()
                if result:
                    errors.append(result)
                progress.advance(task)
            
    return handle_step_error(errors, "Step 6 (Compression)")

def step_7_csv_rename(config):
    root_dir = Path(config['root_dir'])
    csv_1 = Path(config['csv_1_path'])
    csv_2 = Path(config['csv_2_path'])
    errors = []

    # Step 7.1: Rename
    if csv_1.exists():
        with open(csv_1, 'r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            for row in reader:
                if len(row) >= 3:
                    p1, p2, new_p2 = row[0].strip(), row[1].strip(), row[2].strip()
                    src_dir = root_dir / p1 / p2
                    dest_dir = root_dir / p1 / new_p2
                    
                    if src_dir.exists():
                        merge_directories(src_dir, dest_dir, errors)

    # Step 7.2: Suffix
    if csv_2.exists():
        with open(csv_2, 'r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            for row in reader:
                if len(row) >= 2:
                    p1, suffix = row[0].strip(), row[1].strip()
                    p1_dir = root_dir / p1
                    if p1_dir.exists():
                        for p2 in list(p1_dir.iterdir()):
                            if p2.is_dir():
                                dest_dir = p1_dir / f"{p2.name} {suffix}"
                                merge_directories(p2, dest_dir, errors)

    return handle_step_error(errors, "Step 7 (CSV Operations)")

def step_8_final_move(config):
    root_dir = Path(config['root_dir'])
    dest_dir = Path(config['dest_dir'])
    errors = []
    
    parent2_dirs = list(get_parent2_dirs(root_dir))
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    with Progress() as progress:
        task = progress.add_task("[cyan]Step 8: Moving and final cleanup...", total=len(parent2_dirs))
        
        # Move Phase
        for p1, p2 in parent2_dirs:
            target_p2 = dest_dir / p2.name
            merge_directories(p2, target_p2, errors)
            progress.advance(task)
            
    # Cleanup Phase (Delete empty folders in root)
    for dirpath, dirnames, filenames in os.walk(str(root_dir), topdown=False):
        if dirpath == str(root_dir):
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                logging.info(f"Removed empty dir: {dirpath}")
        except Exception as e:
            logging.warning(f"Cleanup: could not remove {dirpath}: {e}")
            pass  # Silent ignore for cleanup
            
    return handle_step_error(errors, "Step 8 (Final Move)")