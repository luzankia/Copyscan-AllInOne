import os
import sys
import io
import shutil
import logging
import re
import json
import socket
import uuid
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt
from PIL import Image
import imagehash
import numpy as np

console = Console()

def resolve_project_path(path_str: str, base_dir: Path) -> str:
    """Resolve a config path relative to base_dir (typically the script's own
    directory) rather than the process's current working directory.

    Absolute paths (e.g. "C:\\Data\\..." or "Z:\\...") are returned unchanged.
    Relative paths (e.g. "exception.txt", "logs/workflow.log") are anchored to
    base_dir, so config.yaml stays portable regardless of where the script is
    launched from."""
    p = Path(path_str)
    if p.is_absolute():
        return str(p)
    return str((base_dir / p).resolve())

def find_free_port(start_port: int, host: str = '127.0.0.1', max_attempts: int = 50) -> int:
    """Returns the first available TCP port at or after start_port, found by
    attempting to bind a socket. Lets multiple tools share a single configured
    port: the first one to start takes it, subsequent ones automatically move
    to the next free port instead of failing on a collision.

    Deliberately does NOT set SO_REUSEADDR on the test socket: on Windows,
    that flag lets a bind() succeed even when another process already holds
    the port, making the availability check unreliable (two tools could both
    "detect" the port as free and collide). A plain bind() gives an accurate,
    exclusive test on both Windows and Linux.

    Raises RuntimeError if no free port is found within max_attempts."""
    port = start_port
    for _ in range(max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"No free port found starting at {start_port} (tried {max_attempts} ports).")

def resolve_web_ui_host(config: dict) -> str:
    """Determines the bind host for the Web UI Flask servers from the optional
    `web_ui_network_access` key in config.yaml (defaults to False, i.e.
    localhost-only, same as before this option existed). When enabled, prints
    a clear security warning since the interface has no authentication and
    exposes destructive actions (deletion, merge, split) to anyone able to
    reach the port."""
    network_access = config.get('web_ui_network_access', False)

    if not isinstance(network_access, bool):
        console.print(
            "[yellow]Warning: 'web_ui_network_access' must be true/false in config.yaml; "
            "defaulting to localhost-only (127.0.0.1).[/yellow]"
        )
        return '127.0.0.1'

    if network_access:
        console.print(
            "[bold red]⚠ Web UI network access is ENABLED: the server will bind to 0.0.0.0 "
            "and be reachable from other devices on your network. There is no authentication "
            "-- anyone who can reach this port can delete, merge, or split your files.[/bold red]"
        )
        return '0.0.0.0'

    return '127.0.0.1'


def get_local_ip() -> str:
    """Best-effort guess of this machine's LAN IP address, used only to print
    a convenient URL when the Web UI is opened to the network. Falls back to
    '127.0.0.1' if it can't be determined (e.g. no active network interface).
    Uses a UDP 'connect' to a public IP as a trick to learn the outbound
    interface address -- no actual packet is sent (UDP connect is local-only)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()
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

# Web UI keyboard shortcuts (Chapter Editor and Split Studio). Every entry is
# a single JavaScript KeyboardEvent.key value -- see:
# https://developer.mozilla.org/en-US/docs/Web/API/UI_Events/Keyboard_event_key_values
# Single letters are matched case-insensitively, so "m" and "M" are the same
# binding. Holding Shift on delete_selection / remember_credit / validate_merges
# also jumps to the next chapter afterward -- that's fixed behavior, not a
# separate binding to configure.
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
    """Merges config.yaml's optional `keyboard_shortcuts` section over
    DEFAULT_KEYBOARD_SHORTCUTS, so a partial override doesn't drop the other
    bindings. Unknown action names and empty/non-string values are ignored
    (with a warning) and fall back to their default. Also warns -- without
    failing -- if two actions end up bound to the same key."""
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

def natural_sort_key(path: Path):
    """Splits a path's name on digit runs for natural, human-friendly ordering
    (e.g. 'Ch.9' < 'Ch.20' < 'Ch.110', instead of the plain lexical order
    that would put 'Ch.110' before 'Ch.20')."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', path.name)]

def _sorted_subdirs(parent: Path):
    """Directly-contained subdirectories of parent, in natural sort order."""
    return sorted((p for p in parent.iterdir() if p.is_dir()), key=natural_sort_key)

def get_leaf_dirs(root_dir: Path, local_mode=False):
    """Yield all Leaf directories, in natural sort order.
    Standard layout: Root -> Parent1 -> Parent2 -> Leaf.
    Local layout (--local): Root -> Leaf (Leaf folders sit directly under root_dir).
    """
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

def load_credit_banners(path: Path) -> dict:
    """Load known embedded-banner hashes, keyed by position ('top'/'bottom').
    Returns {'top': [], 'bottom': []} if the file doesn't exist or is unreadable."""
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
    """Persist known embedded-banner hashes to a JSON file."""
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

def suggest_banner_cut(image_path: Path, position: str, known_hashes: list, threshold: int,
                        min_pct: float = 3, max_pct: float = 35, step_pct: float = 0.5):
    """Sweeps candidate banner heights near the given edge ('top' or 'bottom') of the
    image, looking for the slice that best matches a known banner hash. Returns a
    (cut_pct, matched_hash) tuple where cut_pct is the resulting cut boundary as a Y
    position (percentage from the TOP of the image, 0-100) - the same convention used
    when a marker is placed by clicking on the image - so the caller can use it
    directly to pre-fill the split tool's marker and later pass it to
    crop_remove_banner unchanged. matched_hash is the specific known hash (hex string)
    that produced the best match, so the caller can offer to delete just that entry
    if the suggestion turns out to be unusable. Returns None if no match is found
    within `threshold`. Height is unknown in advance (banners vary in size), hence
    the sweep instead of a fixed offset."""
    if not known_hashes:
        return None
    try:
        img = Image.open(image_path)
        img.load()
    except Exception as e:
        logging.warning(f"Could not open {image_path} for banner detection: {e}")
        return None

    width, height = img.size
    known_matrix = _stack_hashes(known_hashes)
    if known_matrix is None:
        img.close()
        return None

    best_banner_height_pct, best_dist, best_hash_idx = None, None, None
    pct = min_pct
    while pct <= max_pct:
        # pct here is the candidate banner HEIGHT (from the edge), used only for
        # the search - converted to a Y-from-top position just before returning.
        cut_px = max(1, min(height - 1, int(height * pct / 100)))
        box = (0, 0, width, cut_px) if position == 'top' else (0, height - cut_px, width, height)
        try:
            candidate = imagehash.phash(img.crop(box)).hash.flatten()
        except Exception:
            pct += step_pct
            continue

        distances = np.count_nonzero(known_matrix != candidate, axis=1)
        min_dist = int(distances.min())
        if best_dist is None or min_dist < best_dist:
            best_dist = min_dist
            best_banner_height_pct = pct
            best_hash_idx = int(distances.argmin())
        pct += step_pct

    img.close()

    if best_dist is None or best_dist > threshold:
        return None

    # Convert "banner height from edge" to "Y position from top", matching the
    # convention used by manual marker placement and crop_remove_banner.
    cut_y_pct = best_banner_height_pct if position == 'top' else (100 - best_banner_height_pct)
    return cut_y_pct, known_hashes[best_hash_idx]

def compute_banner_slice_hash(image_path: Path, cut_percent: float, side: str):
    """Computes the perceptual hash of just the top/bottom slice of an image at
    the given marker position (Y-from-top, same convention as crop_remove_banner)
    WITHOUT modifying the source file. Used by the standalone hash-maintenance
    tool to learn a banner hash from a throwaway reference upload."""
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
    """Crops out a banner slice using cut_percent as the marker's Y position measured
    from the TOP of the image (0-100), matching how markers are placed by clicking
    on the image in the split tool - regardless of remove_side.
    - remove_side='top': removes everything ABOVE the marker (rows 0..marker),
      keeps everything below.
    - remove_side='bottom': removes everything BELOW the marker (rows marker..end),
      keeps everything above.
    Overwrites image_path with the remaining content, and returns the phash (hex
    string) of the removed slice, or None on failure.

    NOTE: this overwrites image_path in place. Callers that want the pre-crop
    version recoverable (see web_ui.py's /api_remove_banner) must back it up
    to the trash themselves BEFORE calling this."""
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
    """Load known 'credit page' perceptual hashes (hex strings) from a JSON file.
    Returns an empty list if the file doesn't exist yet or is unreadable."""
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
    """Persist known 'credit page' perceptual hashes (hex strings) to a JSON file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Deduplicate while preserving order.
        unique_hashes = list(dict.fromkeys(hashes))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(unique_hashes, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save credit hashes to {path}: {e}")

def compute_phash(image_path: Path):
    """Compute the perceptual hash (phash) of an image as a hex string.
    Returns None if the image can't be opened/read."""
    try:
        with Image.open(image_path) as img:
            return str(imagehash.phash(img))
    except Exception as e:
        logging.warning(f"Could not compute perceptual hash for {image_path}: {e}")
        return None

def _stack_hashes(hash_hex_list: list):
    """Converts a list of hex-string perceptual hashes into a single 2D numpy
    boolean array (one row per hash) for fast vectorized Hamming-distance
    computation against many known hashes at once. Returns None if the list is
    empty or contains no valid hashes."""
    rows = []
    for hex_str in hash_hex_list:
        try:
            rows.append(imagehash.hex_to_hash(hex_str).hash.flatten())
        except Exception:
            continue
    return np.array(rows) if rows else None

def is_known_credit_hash(image_hash_hex: str, known_hashes: list, threshold: int) -> bool:
    """Check whether image_hash_hex is within `threshold` Hamming distance of any
    hash in known_hashes (both as hex strings from imagehash). Vectorized so the
    cost stays negligible even as the known-hash database grows into the
    thousands."""
    if not image_hash_hex or not known_hashes:
        return False
    try:
        candidate = imagehash.hex_to_hash(image_hash_hex).hash.flatten()
    except Exception:
        return False
    matrix = _stack_hashes(known_hashes)
    if matrix is None:
        return False
    distances = np.count_nonzero(matrix != candidate, axis=1)
    return bool(distances.min() <= threshold)

def find_redundant_clusters(hash_list: list, threshold: int):
    """Groups the indices of hash_list into clusters using single-linkage
    clustering: two hashes end up in the same cluster if there's a chain of
    hashes between them where each consecutive pair is within `threshold`
    Hamming distance of each other. This surfaces near-duplicate hashes
    (accumulated e.g. from minor marker adjustments across sessions) without
    needing any visual/image representation - purely from the hash values.

    Returns (clusters, dist_matrix) where clusters is a list of lists of
    indices into hash_list (singletons included), and dist_matrix is an NxN
    numpy array of pairwise Hamming distances (None if hash_list is empty)."""
    n = len(hash_list)
    if n == 0:
        return [], None

    matrix = _stack_hashes(hash_list)
    if matrix is None:
        return [[i] for i in range(n)], None

    # Row-by-row (not a full NxN broadcast) to keep memory usage linear in n.
    dist_matrix = np.zeros((n, n), dtype=int)
    for i in range(n):
        dist_matrix[i] = np.count_nonzero(matrix != matrix[i], axis=1)

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if dist_matrix[i, j] <= threshold:
                union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    return list(groups.values()), dist_matrix


# ---------------------------------------------------------------------------
# Trash / Corbeille
#
# Every interactive deletion made during Step 2 (manual delete, credit-page
# deletion, the original image consumed by a split, the two halves consumed
# by a merge, the pre-crop original consumed by a banner removal) goes
# through send_to_trash() instead of Path.unlink(). Nothing is permanently
# lost until the trash itself is purged -- either manually from the /trash
# web page, or automatically at the very start of the NEXT Step 2 run (see
# workflow.step_2_web_ui), which keeps the trash from growing unbounded
# across sessions while still giving the user a full session to notice and
# undo a mistake.
#
# A JSON manifest (trash_index.json, inside trash_dir) tracks each trashed
# file's original absolute path, why it was trashed, and when, so a restore
# can put it back exactly where it came from.
# ---------------------------------------------------------------------------

TRASH_INDEX_FILENAME = "trash_index.json"

# Human-readable labels for the internal reason codes, used by the /trash
# web page. Keep in sync with the reason strings passed to send_to_trash()
# throughout web_ui.py.
TRASH_REASON_LABELS = {
    "manual_delete": "Deleted manually",
    "credit_page": "Deleted as credit page",
    "split_original": "Original before split",
    "merge_source": "Merged into another page",
    "merge_rejected": "Rejected merge result",
    "banner_crop_source": "Original before banner crop",
}

def load_trash_index(trash_dir: Path) -> list:
    """Load the trash manifest. Returns an empty list if trash_dir or the
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
    """Persist the trash manifest."""
    index_path = trash_dir / TRASH_INDEX_FILENAME
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        with open(index_path, 'w', encoding='utf-8') as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Failed to save trash index to {index_path}: {e}")

def _generate_trash_name(original_path: Path) -> str:
    """Builds a unique trash filename that still ends in the original
    extension (so served thumbnails keep working) while avoiding collisions
    between same-named files from different chapters."""
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    unique = uuid.uuid4().hex[:8]
    return f"{timestamp}_{unique}_{original_path.name}"

def send_to_trash(file_path: Path, trash_dir: Path, reason: str, mode: str = "move") -> bool:
    """Moves (mode='move', the default) or copies (mode='copy') file_path
    into trash_dir and records it in the manifest so it can be restored later.

    mode='copy' is for operations that overwrite a file in place (e.g. banner
    cropping): the original content needs a backup in the trash *before* the
    overwrite happens, while file_path itself must still exist afterward for
    the caller to write the new content into.

    Returns True on success. On failure, file_path is left completely
    untouched (shutil.move/copy either succeeds or raises without partially
    deleting the source) -- so a failed trash attempt never means data loss,
    it just means the caller should treat it like any other failed delete."""
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        trash_name = _generate_trash_name(file_path)
        trash_path = trash_dir / trash_name

        if mode == "copy":
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
    """Restores one trashed file back to its recorded original location.
    If the original folder no longer exists (renamed/moved/deleted since),
    it's recreated. If a file already sits at the exact original path, the
    restored file is given a conflict-safe name instead of overwriting it.
    Returns (success: bool, message: str) -- message is either the restored
    path (on success) or a human-readable reason (on failure)."""
    index = load_trash_index(trash_dir)
    entry = next((e for e in index if e.get('trash_name') == trash_name), None)
    if entry is None:
        return False, "Trash entry not found in the index."

    trash_path = trash_dir / trash_name
    if not trash_path.exists():
        # Manifest references a file that's no longer physically there
        # (manually removed from disk?) -- drop the stale entry so it stops
        # showing up in the /trash page.
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
    trash_dir doesn't exist yet (first run). Returns the number of files
    actually removed, for a console/UI summary."""
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

    # Defensive sweep: also remove any file physically present in trash_dir
    # but missing from the manifest (e.g. after a manual index edit).
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
