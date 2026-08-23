"""
Standalone maintenance tool for Copyscan-AllInOne's credit-hash databases
(credit_hashes.json / credit_banners.json).

Runs completely independently from the main workflow (main.py) - invoke it
whenever you want to review, prune, or seed the hash databases, without going
through the normal step-by-step pipeline.

Usage:
    python hash_maintenance.py
    python hash_maintenance.py --config my_config.yaml
    python hash_maintenance.py --credit-hashes-path C:\\path\\credit_hashes.json --credit-banners-path C:\\path\\credit_banners.json
"""

import argparse
import sys
import tempfile
import threading
import uuid
import webbrowser
from pathlib import Path

import yaml
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.serving import make_server

from utils import (
    console, load_credit_hashes, save_credit_hashes,
    load_credit_banners, save_credit_banners,
    compute_phash, is_known_credit_hash, find_redundant_clusters,
    compute_banner_slice_hash, find_free_port, resolve_project_path,
    DEFAULT_KEYBOARD_SHORTCUTS
)

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.yaml"


def load_settings(config_path, cli_args) -> dict:
    """Resolve credit-hash settings from (in priority order) explicit CLI flags,
    then a config.yaml file, failing clearly if neither provides what's needed."""
    config = {}
    config_file = Path(config_path)
    if config_file.exists():
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            console.print(f"[bold red]Failed to read {config_file}: {e}[/bold red]")
    else:
        console.print(f"[bold red]Config file not found: {config_file}[/bold red]")

    settings = {
        'credit_hashes_path': cli_args.credit_hashes_path or config.get('credit_hashes_path'),
        'credit_banners_path': cli_args.credit_banners_path or config.get('credit_banners_path'),
        'credit_hash_threshold': cli_args.credit_hash_threshold or config.get('credit_hash_threshold', 8),
        'credit_banner_threshold': cli_args.credit_banner_threshold or config.get('credit_banner_threshold', 16),
        'port': cli_args.port or config.get('web_port', 5051),
    }

    missing = [k for k in ('credit_hashes_path', 'credit_banners_path') if not settings[k]]
    if missing:
        console.print(f"[bold red]Missing required setting(s): {', '.join(missing)}[/bold red]")
        console.print(
            "[yellow]Provide them via --credit-hashes-path/--credit-banners-path, "
            "or point --config at a valid config.yaml containing them.[/yellow]"
        )
        sys.exit(1)

    return settings


def build_section(title: str, bucket: str, hash_list: list, threshold: int) -> dict:
    """Prepares one bucket's rows for display: clusters near-duplicate hashes
    together (by Hamming distance, no image/visual representation involved)
    so redundancy is visible directly from the hash values."""
    clusters, _ = find_redundant_clusters(hash_list, threshold)
    # Bigger clusters (more likely redundant) shown first.
    clusters_sorted = sorted(enumerate(clusters), key=lambda c: (-len(c[1]), c[0]))

    rows = []
    redundant_count = 0
    cluster_count = 0
    for cluster_id, member_indices in clusters_sorted:
        if len(member_indices) > 1:
            cluster_count += 1
            redundant_count += len(member_indices) - 1
        for pos, idx in enumerate(member_indices):
            rows.append({
                'hash': hash_list[idx],
                'cluster_id': cluster_id,
                'cluster_size': len(member_indices),
                'is_first_in_cluster': pos == 0,
            })

    return {
        'title': title,
        'bucket': bucket,
        'rows': rows,
        'redundant_count': redundant_count,
        'cluster_count': cluster_count,
    }


def create_app(credit_hashes_path: Path, credit_banners_path: Path,
               credit_hash_threshold: int, credit_banner_threshold: int) -> Flask:
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))

    credit_hashes = load_credit_hashes(credit_hashes_path)
    known_banners = load_credit_banners(credit_banners_path)

    def get_bucket(bucket: str):
        """Returns (mutable list reference, threshold) for a given bucket name."""
        if bucket == 'credit_hashes':
            return credit_hashes, credit_hash_threshold
        elif bucket == 'banner_top':
            return known_banners['top'], credit_banner_threshold
        elif bucket == 'banner_bottom':
            return known_banners['bottom'], credit_banner_threshold
        return None, None

    def persist(bucket: str):
        if bucket == 'credit_hashes':
            save_credit_hashes(credit_hashes_path, credit_hashes)
        else:
            save_credit_banners(credit_banners_path, known_banners)

    @app.route('/')
    def index():
        sections = [
            build_section("Credit Pages (whole-page duplicates)", 'credit_hashes',
                           credit_hashes, credit_hash_threshold),
            build_section("Credit Banners — Top", 'banner_top',
                           known_banners['top'], credit_banner_threshold),
            build_section("Credit Banners — Bottom", 'banner_bottom',
                           known_banners['bottom'], credit_banner_threshold),
        ]
        return render_template('hash_maintenance.html', sections=sections)

    # In-memory registry of uploaded reference images awaiting review:
    # token -> temp file Path. Cleaned up as soon as the user validates/cancels.
    reference_uploads = {}

    @app.route('/upload_reference', methods=['POST'])
    def upload_reference():
        file = request.files.get('image')
        if not file or file.filename == '':
            return jsonify({"status": "error", "message": "No image provided."})

        suffix = Path(file.filename).suffix or '.jpg'
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        tmp_path = Path(tmp_name)
        with open(fd, 'wb') as f:
            file.save(f)

        token = uuid.uuid4().hex
        reference_uploads[token] = tmp_path
        return jsonify({"status": "ok", "token": token})

    @app.route('/reference_image/<token>')
    def reference_image(token):
        tmp_path = reference_uploads.get(token)
        if not tmp_path or not tmp_path.exists():
            return "Reference image not found (it may have already been processed).", 404
        return send_file(tmp_path)

    @app.route('/learn/<token>')
    def learn_page(token):
        if token not in reference_uploads:
            return "Reference image not found (it may have already been processed).", 404
        return render_template(
            'split.html', b64='', return_to='', mask_popups=False,
            suggest_pct=None, suggest_side='', suggest_hash='',
            context='maintenance', token=token, image_url=f'/reference_image/{token}',
            progress_pct=None, shortcuts=DEFAULT_KEYBOARD_SHORTCUTS
        )

    def _cleanup_reference(token: str):
        tmp_path = reference_uploads.pop(token, None)
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()

    @app.route('/learn_whole', methods=['POST'])
    def learn_whole():
        data = request.json or {}
        token = data.get('token')
        tmp_path = reference_uploads.get(token)
        if not tmp_path or not tmp_path.exists():
            return jsonify({"status": "error", "message": "Reference image not found."})

        img_hash = compute_phash(tmp_path)
        if img_hash is None:
            return jsonify({"status": "error", "message": "Could not read this image."})

        learned = not is_known_credit_hash(img_hash, credit_hashes, credit_hash_threshold)
        if learned:
            credit_hashes.append(img_hash)
            persist('credit_hashes')

        _cleanup_reference(token)
        return jsonify({"status": "ok", "learned": learned})

    @app.route('/learn_banner', methods=['POST'])
    def learn_banner():
        data = request.json or {}
        token = data.get('token')
        side = data.get('side')
        cut_percent = data.get('cut_percent')

        tmp_path = reference_uploads.get(token)
        if not tmp_path or not tmp_path.exists():
            return jsonify({"status": "error", "message": "Reference image not found."})
        if side not in ('top', 'bottom'):
            return jsonify({"status": "error", "message": "Invalid side."})
        try:
            cut_percent = float(cut_percent)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid cut position."})

        banner_hash = compute_banner_slice_hash(tmp_path, cut_percent, side)
        if banner_hash is None:
            return jsonify({"status": "error", "message": "Could not process this image."})

        bucket = 'banner_top' if side == 'top' else 'banner_bottom'
        target_list, threshold = get_bucket(bucket)
        learned = not is_known_credit_hash(banner_hash, target_list, threshold)
        if learned:
            target_list.append(banner_hash)
            persist(bucket)

        _cleanup_reference(token)
        return jsonify({"status": "ok", "learned": learned})

    @app.route('/cancel_reference', methods=['POST'])
    def cancel_reference():
        data = request.json or {}
        _cleanup_reference(data.get('token', ''))
        return jsonify({"status": "ok"})

    @app.route('/delete', methods=['POST'])
    def delete_hashes():
        data = request.json or {}
        bucket = data.get('bucket')
        to_delete = set(data.get('hashes', []))

        target_list, _ = get_bucket(bucket)
        if target_list is None:
            return jsonify({"status": "error", "message": "Invalid bucket."})

        before = len(target_list)
        target_list[:] = [h for h in target_list if h not in to_delete]
        removed = before - len(target_list)
        persist(bucket)

        return jsonify({"status": "ok", "removed": removed})

    return app


def main():
    parser = argparse.ArgumentParser(
        description="Standalone maintenance tool for Copyscan-AllInOne's credit-hash "
                    "databases. Runs independently from the main workflow."
    )
    parser.add_argument('--config', type=str, default=str(DEFAULT_CONFIG_PATH),
                        help=f"Path to config.yaml to read hash settings from (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument('--credit-hashes-path', type=str, help="Override path to credit_hashes.json")
    parser.add_argument('--credit-banners-path', type=str, help="Override path to credit_banners.json")
    parser.add_argument('--credit-hash-threshold', type=int, help="Override whole-page dedup threshold")
    parser.add_argument('--credit-banner-threshold', type=int, help="Override banner dedup threshold")
    parser.add_argument('--port', type=int, default=None,
                        help="Preferred port (default: web_port in config.yaml, else 5051). "
                             "If busy, the next free port is used automatically.")
    parser.add_argument('--no-browser', action='store_true', help="Don't auto-open a browser tab")
    args = parser.parse_args()

    settings = load_settings(args.config, args)

    credit_hashes_path = Path(resolve_project_path(settings['credit_hashes_path'], SCRIPT_DIR))
    credit_banners_path = Path(resolve_project_path(settings['credit_banners_path'], SCRIPT_DIR))

    # Print exactly what's being read, in full, so a path mismatch (wrong config
    # picked up, wrong working directory, stale/duplicate files, etc.) is obvious
    # immediately instead of silently showing unexpected data.
    console.print(f"[cyan]Config file used:[/cyan] {Path(args.config).resolve()}")
    console.print(f"[cyan]credit_hashes_path:[/cyan] {credit_hashes_path} "
                  f"({'found' if credit_hashes_path.exists() else '[red]NOT FOUND - will be created[/red]'})")
    console.print(f"[cyan]credit_banners_path:[/cyan] {credit_banners_path} "
                  f"({'found' if credit_banners_path.exists() else '[red]NOT FOUND - will be created[/red]'})")

    app = create_app(
        credit_hashes_path,
        credit_banners_path,
        settings['credit_hash_threshold'],
        settings['credit_banner_threshold'],
    )
    console.print(f"[cyan]Loaded:[/cyan] {len(load_credit_hashes(credit_hashes_path))} credit page hash(es), "
                  f"{sum(len(v) for v in load_credit_banners(credit_banners_path).values())} banner hash(es)")

    port = find_free_port(settings['port'])
    if port != settings['port']:
        console.print(f"[yellow]Port {settings['port']} is busy, using {port} instead.[/yellow]")
    url = f"http://127.0.0.1:{port}"
    console.print(f"[bold green]Credit Hash Maintenance running at {url}[/bold green]")
    console.print("[yellow]Press Ctrl+C to stop.[/yellow]")

    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    server = make_server('127.0.0.1', port, app)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[blue]Shutting down.[/blue]")


if __name__ == '__main__':
    main()
