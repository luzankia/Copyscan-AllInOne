"""
Copyscan-AllInOne - shared utility functions.

Covers: config path resolution, port/host/PIN resolution for the Web UI,
environment/logging setup, keyboard shortcut resolution, natural sort and
folder traversal helpers, safe folder merging, perceptual-hash based
credit-page/banner detection (via imagehash + numpy, with per-file caches so
reloading a chapter page stays fast), on-disk thumbnails for the Web UI
galleries, and the trash (recycle bin) system used by every Step 2 deletion.
"""

import os
import sys
import io
import shutil
import logging
import re
import json
import socket
import uuid
import hmac
import secrets
import time
import hashlib
import tempfile
import ipaddress
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt
from PIL import Image
import imagehash
import numpy as np

console = Console()

def resolve_project_path(path_str: str, base_dir: Path) -> str:
    """Resolves a config path relative to base_dir instead of the process's
    cwd. Absolute paths are returned unchanged."""
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    return str((base_dir / p).resolve())

def find_free_port(start_port: int, host: str = '127.0.0.1', max_attempts: int = 50) -> int:
    """Returns the first available TCP port at or after start_port (tested
    by binding a socket). Raises RuntimeError if none is free within
    max_attempts."""
    port = start_port
    for _ in range(max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if os.name == 'nt':
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"No free port found starting at {start_port} (tried {max_attempts} ports).")

def resolve_web_ui_host(config: dict) -> str:
    """Resolves the Web UI bind host from config.yaml's `web_ui_network_access`
    (default False = localhost-only). When network access is enabled, a PIN
    (`web_ui_pin`) is mandatory: clients connecting from a non-loopback
    address are asked for it (see setup_pin_protection below)."""
    network_access = config.get('web_ui_network_access', False)

    if not isinstance(network_access, bool):
        console.print(
            "[yellow]Warning: 'web_ui_network_access' must be true/false in config.yaml; "
            "defaulting to localhost-only (127.0.0.1).[/yellow]"
        )
        return '127.0.0.1'

    if network_access:
        if not resolve_web_ui_pin(config):
            console.print(
                "[bold red]Error: 'web_ui_network_access' is enabled but 'web_ui_pin' is "
                "missing or empty in config.yaml.[/bold red]"
            )
            console.print(
                "[yellow]Set 'web_ui_pin' to the code asked of any device connecting from "
                "your network (127.0.0.1 access never asks for it), or set "
                "'web_ui_network_access: false' to stay localhost-only.[/yellow]"
            )
            sys.exit(1)
        console.print(
            "[bold red]⚠ Web UI network access is ENABLED: the server will bind to 0.0.0.0 "
            "and be reachable from other devices on your network. Those devices must enter "
            "the PIN from 'web_ui_pin' before they can do anything.[/bold red]"
        )
        return '0.0.0.0'

    return '127.0.0.1'


def resolve_web_ui_pin(config: dict):
    """Returns the normalized Web UI PIN from config.yaml's `web_ui_pin`
    (number or string, stripped of whitespace), or None when unset/empty."""
    pin = config.get('web_ui_pin')
    if pin is None or isinstance(pin, bool):
        return None
    if not isinstance(pin, (str, int)):
        console.print(
            "[yellow]Warning: 'web_ui_pin' must be a number or a string in config.yaml; "
            "PIN protection is disabled.[/yellow]"
        )
        return None
    return str(pin).strip() or None


def setup_pin_protection(app, pin):
    """Installs the PIN gate on a Flask app (main Web UI and hash maintenance
    tool alike): every request from a non-loopback client must have entered
    the PIN once -- the acceptance is remembered in a signed session cookie
    for the server's lifetime (re-asked after a restart). Requests from
    127.0.0.1/::1 are never gated, so working on the machine itself stays
    friction-free."""
    from flask import request, session, redirect, render_template, jsonify

    # Fresh secret at every launch: cookies from a previous run are invalid,
    # forcing remote clients to re-enter the PIN once per session.
    app.secret_key = secrets.token_hex(32)

    def is_loopback(addr):
        try:
            return ipaddress.ip_address(addr).is_loopback
        except ValueError:
            return False

    @app.before_request
    def _pin_gate():
        if is_loopback(request.remote_addr) or session.get('pin_ok'):
            return None
        if request.endpoint == '_pin_verify':
            return None
        if request.method == 'GET':
            return render_template('pin.html'), 401
        return jsonify({"status": "error", "message": "PIN required."}), 401

    @app.route('/pin_verify', methods=['POST'])
    def _pin_verify():
        entered = request.form.get('pin') or (request.get_json(silent=True) or {}).get('pin') or ''
        # Encoded to bytes so compare_digest also accepts non-ASCII input.
        if hmac.compare_digest(str(entered).strip().encode('utf-8'), str(pin).strip().encode('utf-8')):
            session['pin_ok'] = True
            logging.info(f"PIN accepted for client {request.remote_addr}")
            return redirect('/')
        # Slow down brute-force attempts and leave a trace in the log.
        logging.warning(f"Failed PIN attempt from client {request.remote_addr}")
        time.sleep(1.5)
        return render_template('pin.html', error=True), 401


def get_local_ip() -> str:
    """Best-effort guess of this machine's LAN IP, for printing a convenient
    URL when the Web UI is opened to the network. Falls back to 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP "connect" is local-only (no packet sent) -- just a trick to
        # learn the outbound interface address.
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()

def setup_environment(log_path, log_enabled=True):
    """Enforces UTF-8 encoding on Windows consoles and configures logging
    (or routes it to a null handler if log_enabled is False)."""
    if os.name == 'nt':
        os.system('chcp 65001 >nul 2>&1')
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    if not log_enabled:
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
    """Checks for ImageMagick (if Step 1 is active) and 7-Zip, prompting
    the user to skip/fallback or abort when either is missing."""
    steps_active = config.get('steps_active', {})
    step_1_active = steps_active.get('step_1', True)

    # ImageMagick is only required when Step 1 (Integrity Check) is active.
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

    # 7-Zip stays the preferred compressor; zipfile is only a fallback.
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

# Web UI keyboard shortcuts (Chapter Editor and Split Studio). Each value is
# a single JavaScript KeyboardEvent.key, matched case-insensitively. Shift on
# delete_selection/remember_credit/validate_merges also jumps to the next
# chapter -- that's fixed behavior, not a separate binding.
DEFAULT_KEYBOARD_SHORTCUTS = {
    "prev_chapter": "ArrowLeft",
    "next_chapter": "ArrowRight",
    "delete_selection": "Delete",
    "remember_credit": "C",
    "merge_pairs": "M",
    "validate_merges": "V",
    "execute_split": "X",
}

def resolve_keyboard_shortcuts(config: dict) -> dict:
    """Merges config.yaml's `keyboard_shortcuts` over the defaults (partial
    overrides keep the rest). Warns (without failing) on unknown actions,
    invalid values, or two actions sharing the same key."""
    shortcuts = dict(DEFAULT_KEYBOARD_SHORTCUTS)
    user_shortcuts = config.get('keyboard_shortcuts') or {}

    for action, key in user_shortcuts.items():
        if action not in shortcuts:
            console.print(f"[yellow]Warning: unknown keyboard_shortcuts entry '{action}' in config.yaml (ignored).[/yellow]")
            continue
        if not isinstance(key, str) or not key.strip():
            console.print(f"[yellow]Warning: keyboard_shortcuts.{action} must be a non-empty string; keeping default '{shortcuts[action]}'.[/yellow]")
            continue
        shortcuts[action] = key.strip()

    seen = {}
    for action, key in shortcuts.items():
        norm = key.lower()
        if norm in seen:
            console.print(f"[yellow]Warning: keyboard_shortcuts '{seen[norm]}' and '{action}' are both bound to '{key}' -- only one will trigger.[/yellow]")
        else:
            seen[norm] = action

    return shortcuts

def resolve_preload_settings(config: dict):
    """Resolves the optional background chapter-preload settings from
    config.yaml."""
    chapters_ahead = config.get('preload_chapters_ahead', 1)
    workers = config.get('preload_workers', 1)

    if isinstance(chapters_ahead, bool) or not isinstance(chapters_ahead, int) or chapters_ahead < 0:
        console.print(
            "[yellow]Warning: 'preload_chapters_ahead' must be a non-negative integer in "
            "config.yaml; defaulting to 1.[/yellow]"
        )
        chapters_ahead = 1

    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        console.print(
            "[yellow]Warning: 'preload_workers' must be a positive integer in config.yaml; "
            "defaulting to 1.[/yellow]"
        )
        workers = 1

    return chapters_ahead, workers

def natural_sort_key(path: Path):
    """Splits a filename on digit runs for natural ordering
    (e.g. 'Ch.9' < 'Ch.20' < 'Ch.110')."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', path.name)]

def _sorted_subdirs(parent: Path):
    """Directly-contained subdirectories of parent, in natural sort order."""
    return sorted((p for p in parent.iterdir() if p.is_dir()), key=natural_sort_key)

def get_leaf_dirs(root_dir: Path, local_mode=False):
    """Yields all Leaf directories in natural sort order: Root/Leaf in
    local_mode, otherwise Root/Parent1/Parent2/Leaf."""
    if not root_dir.exists():
        return
    if local_mode:
        for leaf in _sorted_subdirs(root_dir):
            yield leaf
        return
    for p1 in _sorted_subdirs(root_dir):
        for p2 in _sorted_subdirs(p1):
            for leaf in _sorted_subdirs(p2):
                yield leaf

def get_parent2_dirs(root_dir: Path):
    """Yield all Parent2 directories, in natural sort order."""
    if not root_dir.exists():
        return
    for p1 in _sorted_subdirs(root_dir):
        for p2 in _sorted_subdirs(p1):
            yield p1, p2

def resolve_conflict(target_path: Path, is_file=False) -> Path:
    """Resolves a naming conflict by appending ' (1)', ' (2)', etc."""
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
    """Safely merges src_dir into dest_dir, resolving file-name conflicts
    without overwriting anything."""
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
        
        # Remove the now-empty source directory.
        if not any(src_dir.iterdir()):
            src_dir.rmdir()
    except Exception as e:
        error_list.append(f"Merge error {src_dir} -> {dest_dir}: {str(e)}")
        logging.error(f"Merge error {src_dir}: {str(e)}")

def load_credit_banners(path: Path) -> dict:
    """Loads known embedded-banner hashes, keyed by 'top'/'bottom'. Returns
    an empty structure if the file doesn't exist or is unreadable."""
    default = {"top": [], "bottom": []}
    if not path.exists():
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                "top": [str(h) for h in data.get("top", [])],
                "bottom": [str(h) for h in data.get("bottom", [])],
            }
        logging.warning(f"Credit banner file {path} is not a JSON object; ignoring.")
        return default
    except Exception as e:
        logging.error(f"Failed to load credit banners from {path}: {e}")
        return default

def save_credit_banners(path: Path, banners: dict):
    """Persists known embedded-banner hashes to a JSON file (deduplicated)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        cleaned = {
            "top": list(dict.fromkeys(banners.get("top", []))),
            "bottom": list(dict.fromkeys(banners.get("bottom", []))),
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cleaned, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save credit banners to {path}: {e}")

# (path, mtime_ns, size, position, min_pct, max_pct, step_pct)
#   -> (heights_pct, bit_matrix) | None
# The key changes whenever the file is modified, so entries never go stale.
_BANNER_CANDIDATES_CACHE = {}

def get_banner_candidates(image_path: Path, position: str,
                          min_pct: float = 3, max_pct: float = 35, step_pct: float = 0.5):
    """Perceptual hashes of every candidate banner slice at the given edge
    ('top'/'bottom'), returned as (heights_pct, bit_matrix) with one matrix
    row per candidate height, or None if the image can't be read.

    The expensive part of banner detection (one decode + ~64 crop/phash)
    depends only on the image, not on the known-banner database, so it is
    cached per (path, mtime, size). Matching against the database is then a
    cheap numpy comparison that always reflects the current database."""
    try:
        stat = image_path.stat()
    except OSError:
        return None

    key = (str(image_path), stat.st_mtime_ns, stat.st_size, position, min_pct, max_pct, step_pct)
    if key in _BANNER_CANDIDATES_CACHE:
        return _BANNER_CANDIDATES_CACHE[key]

    result = None
    try:
        with Image.open(image_path) as img:
            img.load()
            width, height = img.size
            pcts, rows = [], []
            # Steps are computed from the loop index (not accumulated via +=)
            # to avoid floating-point drift across ~64 iterations.
            step_count = int(round((max_pct - min_pct) / step_pct)) + 1
            for i in range(step_count):
                pct = min_pct + i * step_pct
                cut_px = max(1, min(height - 1, int(height * pct / 100)))
                box = (0, 0, width, cut_px) if position == 'top' else (0, height - cut_px, width, height)
                try:
                    rows.append(imagehash.phash(img.crop(box)).hash.flatten())
                except Exception:
                    continue
                pcts.append(pct)
        if rows:
            result = (pcts, np.array(rows))
    except Exception as e:
        logging.warning(f"Could not open {image_path} for banner detection: {e}")

    _BANNER_CANDIDATES_CACHE[key] = result
    return result

def suggest_banner_cut(image_path: Path, position: str, known_hashes: list, threshold: int,
                        min_pct: float = 3, max_pct: float = 35, step_pct: float = 0.5):
    """Looks for the best match between candidate banner heights near the
    given edge ('top'/'bottom') and known_hashes. Returns (cut_pct,
    matched_hash) using the same Y-from-top convention as manual markers, or
    None if nothing matches within threshold."""
    if not known_hashes:
        return None
    known_matrix, index_map = _stack_hashes(known_hashes)
    if known_matrix is None:
        return None

    candidates = get_banner_candidates(image_path, position, min_pct, max_pct, step_pct)
    if candidates is None:
        return None
    pcts, candidate_matrix = candidates

    # (candidates, known, bits) -> Hamming distance for every pair. Ties
    # resolve to the first candidate height, then the first known hash,
    # same as the previous sweep-based implementation.
    distances = np.count_nonzero(
        candidate_matrix[:, None, :] != known_matrix[None, :, :], axis=2
    )
    cand_idx, row_idx = (int(v) for v in np.unravel_index(int(distances.argmin()), distances.shape))
    if distances[cand_idx, row_idx] > threshold:
        return None

    banner_height_pct = pcts[cand_idx]
    cut_y_pct = banner_height_pct if position == 'top' else (100 - banner_height_pct)
    return cut_y_pct, known_hashes[index_map[row_idx]]

def compute_banner_slice_hash(image_path: Path, cut_percent: float, side: str):
    """Computes the perceptual hash of just the top/bottom slice at
    cut_percent (Y-from-top), without modifying the source file. Used by the
    hash-maintenance tool to learn a banner hash from a reference upload."""
    try:
        img = Image.open(image_path)
        img.load()
        width, height = img.size
        marker_px = max(1, min(height - 1, int(round(height * cut_percent / 100))))
        box = (0, 0, width, marker_px) if side == 'top' else (0, marker_px, width, height)
        slice_hash = str(imagehash.phash(img.crop(box)))
        img.close()
        return slice_hash
    except Exception as e:
        logging.error(f"Failed to compute banner slice hash from {image_path}: {e}")
        return None

def crop_remove_banner(image_path: Path, cut_percent: float, remove_side: str):
    """Crops out a banner slice at cut_percent (Y-from-top) and overwrites
    image_path with the remainder. remove_side='top' drops everything above
    the marker, 'bottom' drops everything below. Returns the removed slice's
    phash, or None on failure.

    Caller must back up the pre-crop file themselves (e.g. to the trash)
    before calling this -- the original is not recoverable afterward."""
    try:
        img = Image.open(image_path)
        img.load()
        width, height = img.size
        marker_px = max(1, min(height - 1, int(round(height * cut_percent / 100))))

        if remove_side == 'top':
            banner_box, keep_box = (0, 0, width, marker_px), (0, marker_px, width, height)
        else:
            banner_box, keep_box = (0, marker_px, width, height), (0, 0, width, marker_px)

        banner_crop = img.crop(banner_box)
        keep_crop = img.crop(keep_box)
        banner_hash = str(imagehash.phash(banner_crop))
        img.close()

        keep_crop.save(image_path)
        return banner_hash
    except Exception as e:
        logging.error(f"Failed to crop banner from {image_path}: {e}")
        return None

def load_credit_hashes(path: Path) -> list:
    """Loads known 'credit page' phashes (hex strings). Returns an empty
    list if the file doesn't exist yet or is unreadable."""
    if not path.exists():
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(h) for h in data]
        logging.warning(f"Credit hash file {path} does not contain a JSON list; ignoring.")
        return []
    except Exception as e:
        logging.error(f"Failed to load credit hashes from {path}: {e}")
        return []

def save_credit_hashes(path: Path, hashes: list):
    """Persists known 'credit page' phashes to a JSON file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Deduplicate while preserving order.
        unique_hashes = list(dict.fromkeys(hashes))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(unique_hashes, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save credit hashes to {path}: {e}")

# (path, mtime_ns, size) -> phash hex string (or None if unreadable).
# The key changes whenever the file is modified, so entries never go stale.
_PHASH_CACHE = {}

def compute_phash(image_path: Path):
    """Computes an image's perceptual hash as a hex string, or None if it
    can't be opened/read. Results are cached per (path, mtime, size), so
    reloading a chapter page doesn't re-decode every image."""
    try:
        stat = image_path.stat()
    except OSError:
        return None

    key = (str(image_path), stat.st_mtime_ns, stat.st_size)
    if key in _PHASH_CACHE:
        return _PHASH_CACHE[key]

    try:
        with Image.open(image_path) as img:
            result = str(imagehash.phash(img))
    except Exception as e:
        logging.warning(f"Could not compute perceptual hash for {image_path}: {e}")
        result = None

    _PHASH_CACHE[key] = result
    return result

_STACK_CACHE = {}

def _stack_hashes(hash_hex_list: list):
    """Converts hex-string phashes into one 2D boolean numpy array (one row
    per valid hash), for vectorized Hamming-distance comparisons."""
    if not hash_hex_list:
        return None, []

    content_key = tuple(hash_hex_list)
    cached = _STACK_CACHE.get(id(hash_hex_list))
    if cached is not None and cached[0] == content_key:
        return cached[1], cached[2]

    rows, index_map = [], []
    for i, hex_str in enumerate(hash_hex_list):
        try:
            rows.append(imagehash.hex_to_hash(hex_str).hash.flatten())
            index_map.append(i)
        except Exception:
            logging.warning(f"Skipping unparsable hash in database: {hex_str!r}")

    matrix = np.array(rows) if rows else None
    _STACK_CACHE[id(hash_hex_list)] = (content_key, matrix, index_map)
    return matrix, index_map

def is_known_credit_hash(image_hash_hex: str, known_hashes: list, threshold: int) -> bool:
    """Returns whether image_hash_hex is within `threshold` Hamming distance
    of any hash in known_hashes."""
    if not image_hash_hex or not known_hashes:
        return False
    try:
        candidate = imagehash.hex_to_hash(image_hash_hex).hash.flatten()
    except Exception:
        return False
    matrix, _ = _stack_hashes(known_hashes)
    if matrix is None:
        return False
    distances = np.count_nonzero(matrix != candidate, axis=1)
    return bool(distances.min() <= threshold)

def find_known_credit_match(image_hash_hex: str, known_hashes: list, threshold: int):
    """Like is_known_credit_hash(), but returns the specific matching hash
    (hex string) instead of a bool, so callers can offer to delete that exact
    database entry. Returns None if there's no match within threshold."""
    if not image_hash_hex or not known_hashes:
        return None
    try:
        candidate = imagehash.hex_to_hash(image_hash_hex).hash.flatten()
    except Exception:
        return None
    matrix, index_map = _stack_hashes(known_hashes)
    if matrix is None:
        return None
    distances = np.count_nonzero(matrix != candidate, axis=1)
    best_row = int(distances.argmin())
    if distances[best_row] <= threshold:
        return known_hashes[index_map[best_row]]
    return None

def find_redundant_clusters(hash_list: list, threshold: int):
    """Single-linkage clusters hash_list's indices: two hashes share a
    cluster if a chain of within-threshold neighbors connects them. O(n^2)
    in time and memory -- fine for hundreds of hashes, but costly if the
    database grows into the thousands. Returns (clusters, dist_matrix);
    dist_matrix only covers the parseable hashes (see _stack_hashes)."""
    n = len(hash_list)
    if n == 0:
        return [], None

    matrix, index_map = _stack_hashes(hash_list)
    if matrix is None:
        return [[i] for i in range(n)], None

    valid_n = len(index_map)
    # Row-by-row (not a full NxN broadcast) to keep memory usage linear in n.
    dist_matrix = np.zeros((valid_n, valid_n), dtype=int)
    for i in range(valid_n):
        dist_matrix[i] = np.count_nonzero(matrix != matrix[i], axis=1)

    parent = list(range(valid_n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(valid_n):
        for j in range(i + 1, valid_n):
            if dist_matrix[i, j] <= threshold:
                union(i, j)

    groups = {}
    for i in range(valid_n):
        groups.setdefault(find(i), []).append(index_map[i])

    clusters = list(groups.values())
    # An unparsable hash is still shown to the user (as its own singleton
    # "cluster") instead of silently vanishing from the review table.
    valid_set = set(index_map)
    clusters.extend([i] for i in range(n) if i not in valid_set)

    return clusters, dist_matrix


# ---------------------------------------------------------------------------
# Thumbnails: the Web UI galleries never need full-size images. Thumbnails
# are generated on demand, cached on disk (keyed by path + mtime + size, so
# an edited/cropped file automatically gets a fresh one), and served by the
# /thumb/ route in web_ui.py.
# ---------------------------------------------------------------------------

THUMB_CACHE_DIR = Path(tempfile.gettempdir()) / "copyscan_thumbs"
THUMB_QUALITY = 80

def parse_thumb_px(thumb_size: str, default: int = 220) -> int:
    """Extracts the pixel value from config's thumb_size ('220px' -> 220).
    Falls back to `default` for any other unit (%, rem, ...)."""
    match = re.match(r'^\s*(\d+)\s*px\s*$', str(thumb_size))
    return int(match.group(1)) if match else default

def get_image_size(image_path: Path):
    """Returns (width, height) by reading the file header only (no full
    decode), or None if the file can't be opened."""
    try:
        with Image.open(image_path) as img:
            return img.size
    except Exception:
        return None

def _thumb_cache_path(image_path: Path, max_w: int, max_h: int) -> Path:
    stat = image_path.stat()
    key = f"{image_path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{max_w}x{max_h}"
    digest = hashlib.sha1(key.encode('utf-8')).hexdigest()
    return THUMB_CACHE_DIR / f"{digest}.jpg"

def get_or_create_thumbnail(image_path: Path, max_w: int, max_h: int):
    """Returns the path of a cached JPEG thumbnail fitting in max_w x max_h,
    creating it if needed. Returns None on failure (unsupported format,
    unreadable file...) so the caller can fall back to the original."""
    try:
        thumb_path = _thumb_cache_path(image_path, max_w, max_h)
        if thumb_path.exists():
            return thumb_path

        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with Image.open(image_path) as img:
            width, height = img.size
            scale = min(max_w / width, max_h / height, 1.0)
            # JPEG only (no-op for other formats): the decoder downscales by
            # 1/2, 1/4 or 1/8 while decoding, far cheaper than a full decode.
            img.draft('RGB', (max(1, int(width * scale)), max(1, int(height * scale))))
            thumb = img if img.mode == 'RGB' else img.convert('RGB')
            thumb.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)

            # Write to a temp name then atomically swap in, so two concurrent
            # requests never serve a half-written file.
            tmp_path = thumb_path.with_name(f"{thumb_path.name}.{uuid.uuid4().hex[:8]}.tmp")
            thumb.save(tmp_path, format="JPEG", quality=THUMB_QUALITY)
        os.replace(tmp_path, thumb_path)
        return thumb_path
    except Exception as e:
        logging.warning(f"Could not build thumbnail for {image_path}: {e}")
        return None

def purge_thumb_cache() -> int:
    """Empties the thumbnail cache. Returns the number of files removed."""
    if not THUMB_CACHE_DIR.exists():
        return 0
    count = 0
    for f in THUMB_CACHE_DIR.iterdir():
        if f.is_file():
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
    return count


# ---------------------------------------------------------------------------
# Trash: every Step 2 deletion goes through send_to_trash() instead of
# Path.unlink(). A JSON manifest (trash_index.json) tracks each entry's
# original path, reason, and timestamp, so a restore can put it back exactly
# where it came from. Auto-purged at the start of the next Step 2 run.
# ---------------------------------------------------------------------------

TRASH_INDEX_FILENAME = "trash_index.json"

# Human-readable labels for the /trash page. Keep in sync with the reason
# strings passed to send_to_trash() throughout web_ui.py.
TRASH_REASON_LABELS = {
    "manual_delete": "Deleted manually",
    "credit_page": "Deleted as credit page",
    "split_original": "Original before split",
    "merge_source": "Merged into another page",
    "merge_rejected": "Rejected merge result",
    "banner_crop_source": "Original before banner crop",
}

def load_trash_index(trash_dir: Path) -> list:
    """Loads the trash manifest. Returns an empty list if trash_dir or the
    index file doesn't exist yet, or is unreadable."""
    index_path = trash_dir / TRASH_INDEX_FILENAME
    if not index_path.exists():
        return []
    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logging.error(f"Failed to load trash index from {index_path}: {e}")
        return []

def save_trash_index(trash_dir: Path, entries: list):
    """Persists the trash manifest."""
    index_path = trash_dir / TRASH_INDEX_FILENAME
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Failed to save trash index to {index_path}: {e}")

def _generate_trash_name(original_path: Path) -> str:
    """Builds a unique trash filename (keeps the original extension so
    thumbnails still work) to avoid collisions between same-named files."""
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    unique = uuid.uuid4().hex[:8]
    return f"{timestamp}_{unique}_{original_path.name}"

def send_to_trash(file_path: Path, trash_dir: Path, reason: str, mode: str = "move") -> bool:
    """Moves (default) or copies file_path into trash_dir and records it in
    the manifest. On failure, file_path is left untouched. Returns True on
    success."""
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        trash_name = _generate_trash_name(file_path)
        trash_path = trash_dir / trash_name

        if mode == "copy":
            # Used when the caller needs to overwrite file_path in place
            # right after (e.g. banner cropping): back up first, keep the
            # original path alive for the overwrite.
            shutil.copy2(str(file_path), str(trash_path))
        else:
            shutil.move(str(file_path), str(trash_path))

        index = load_trash_index(trash_dir)
        index.append({
            "trash_name": trash_name,
            "original_path": str(file_path.resolve()),
            "reason": reason,
            "deleted_at": datetime.now().isoformat(timespec='seconds'),
        })
        save_trash_index(trash_dir, index)
        logging.info(f"Trashed ({reason}, {mode}): {file_path} -> {trash_name}")
        return True
    except Exception as e:
        logging.error(f"Failed to send {file_path} to trash: {e}")
        return False

def restore_from_trash(trash_name: str, trash_dir: Path):
    """Restores one trashed file to its recorded original location
    (recreating the folder if needed, conflict-safe renaming if something
    already sits there). Returns (success, message)."""
    index = load_trash_index(trash_dir)
    entry = next((e for e in index if e.get('trash_name') == trash_name), None)
    if entry is None:
        return False, "Trash entry not found in the index."

    trash_path = trash_dir / trash_name
    if not trash_path.exists():
        # Manifest references a file no longer physically present: drop the
        # stale entry so it stops showing up in /trash.
        save_trash_index(trash_dir, [e for e in index if e.get('trash_name') != trash_name])
        return False, "File missing from the trash folder (index entry removed)."

    original_path = Path(entry['original_path'])
    try:
        original_path.parent.mkdir(parents=True, exist_ok=True)
        target_path = original_path
        if target_path.exists():
            target_path = resolve_conflict(target_path, is_file=True)
        shutil.move(str(trash_path), str(target_path))
    except Exception as e:
        logging.error(f"Failed to restore {trash_name} from trash: {e}")
        return False, f"Restore failed: {e}"

    save_trash_index(trash_dir, [e for e in index if e.get('trash_name') != trash_name])
    logging.info(f"Restored from trash: {trash_name} -> {target_path}")
    return True, str(target_path)

def purge_trash(trash_dir: Path) -> int:
    """Permanently empties the trash (files + manifest). Safe to call when
    trash_dir doesn't exist yet. Returns the number of files removed."""
    if not trash_dir.exists():
        return 0

    index = load_trash_index(trash_dir)
    count = 0
    for entry in index:
        trash_path = trash_dir / entry.get('trash_name', '')
        try:
            if trash_path.exists():
                trash_path.unlink()
                count += 1
        except Exception as e:
            logging.error(f"Failed to purge trashed file {trash_path}: {e}")

    # Defensive sweep: also remove files present on disk but missing from
    # the manifest (e.g. after a manual index edit).
    try:
        for f in trash_dir.iterdir():
            if f.is_file() and f.name != TRASH_INDEX_FILENAME:
                try:
                    f.unlink()
                    count += 1
                except Exception as e:
                    logging.error(f"Failed to purge orphan trash file {f}: {e}")
    except Exception:
        pass

    save_trash_index(trash_dir, [])
    logging.info(f"Trash purged: {count} file(s) permanently removed.")
    return count
