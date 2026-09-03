import threading
import base64
import logging
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.serving import make_server, BaseWSGIServer
from PIL import Image

from utils import (
    load_credit_hashes, save_credit_hashes, compute_phash, is_known_credit_hash,
    find_known_credit_match,
    load_credit_banners, save_credit_banners, suggest_banner_cut, crop_remove_banner,
    natural_sort_key as get_natural_key, DEFAULT_KEYBOARD_SHORTCUTS,
    send_to_trash, load_trash_index, restore_from_trash, purge_trash, TRASH_REASON_LABELS
)

BaseWSGIServer.allow_reuse_address = False

class ServerThread(threading.Thread):
    def __init__(self, app, host, port):
        threading.Thread.__init__(self)
        self.server = make_server(host, port, app, threaded=True)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

def start_web_ui(images_list, host, port, thumb_size, supported_extensions, mask_popups=False,
                  credit_hashes_path=None, credit_hash_threshold=8,
                  credit_banners_path=None, credit_banner_threshold=10,
                  shortcuts=None, trash_dir=None, mobile_mini_mode=False):
    
    """Starts the Flask server for manual sorting, merging, and image splitting."""
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    completion_event = threading.Event()
    shortcuts = shortcuts or DEFAULT_KEYBOARD_SHORTCUTS

    # Defensive fallback: workflow.py always resolves and passes trash_dir
    # from config.yaml's required `trash_dir` key, so this should never
    # actually trigger -- but a Web UI that can't delete anything safely is
    # worse than one that keeps working with a sane default location.
    trash_dir = Path(trash_dir) if trash_dir else Path(__file__).resolve().parent / "trash"
    
    # Shared registries within the server instance
    path_map = {}
    PENDING_MERGES = {} # b64_fusion -> { 'merged_path', 'top_path', 'bottom_path', 'filename', 'leaf_dir' }

    # Known "credit page" perceptual hashes (whole duplicated pages), loaded once
    # and grown in-memory as the user confirms new ones through the UI (also
    # persisted to disk immediately).
    known_credit_hashes = load_credit_hashes(credit_hashes_path) if credit_hashes_path else []

    # Known embedded "credit banner" hashes (top/bottom slices merged into an
    # otherwise real content page), same learning principle as above.
    known_banners = load_credit_banners(credit_banners_path) if credit_banners_path else {"top": [], "bottom": []}

    def check_credit_match(file_path):
        """Returns the known credit-page hash (hex string) that file_path's
        perceptual hash matches within credit_hash_threshold, or None if it
        doesn't match anything. Callers wanting a plain bool should compare
        the result to None -- this is deliberately not a bool return so the
        Web UI can offer a "delete this exact hash" action on a match."""
        img_hash = compute_phash(file_path)
        if img_hash is None:
            return None
        return find_known_credit_match(img_hash, known_credit_hashes, credit_hash_threshold)

    def check_banner_suggestion(file_path, position):
        """Returns a (cut_pct, matched_hash) tuple if file_path looks like it contains
        a known embedded credit banner at the given edge, else None."""
        return suggest_banner_cut(
            file_path, position, known_banners.get(position, []), credit_banner_threshold
        )

    # We only store leaf folders refrence list. First picture of each leaf folder will be computed on-demand in index().
    # It will always reflect the actual first image of each leaf folder (even after a delete/fuse/split task).
    leaf_dirs = list(dict.fromkeys(img_path.parent for img_path in images_list))

    def merge_images_func(top_path, bottom_path, output_path):
        with Image.open(top_path) as top_img, Image.open(bottom_path) as bottom_img:
            width = max(top_img.width, bottom_img.width)
            height = top_img.height + bottom_img.height
            merged = Image.new("RGB", (width, height))
            merged.paste(top_img, (0, 0))
            merged.paste(bottom_img, (0, top_img.height))
            merged.save(output_path)

    def merge_images_side_by_side_func(left_path, right_path, output_path):
        with Image.open(left_path) as left_img, Image.open(right_path) as right_img:
            width = left_img.width + right_img.width
            height = max(left_img.height, right_img.height)
            merged = Image.new("RGB", (width, height))
            merged.paste(left_img, (0, 0))
            merged.paste(right_img, (left_img.width, 0))
            merged.save(output_path)

    def get_current_first_image(leaf_dir):
        """Returns the current first valid image in leaf_dir (natural sort), or None if the folder has become empty/unreadable."""
        try:
            files = sorted([
                f for f in leaf_dir.iterdir()
                if f.is_file() and f.suffix.lower() in supported_extensions and not f.name.startswith("fus-")
            ], key=get_natural_key)
        except Exception:
            return None
        return files[0] if files else None

    def resequence_folder(leaf_dir, exts):
        """Renames all valid files in a folder sequentially (001.ext, 002.ext...) to preserve order."""
        files = sorted([
            f for f in leaf_dir.iterdir() 
            if f.is_file() and f.suffix.lower() in exts and not f.name.startswith("fus-")
        ], key=get_natural_key)
        
        if not files:
            return

        # Already sequential? Compare each file's current name against its
        # expected "NNN.ext" target -- if everything matches, nothing to do.
        already_sequential = all(
            f.name == f"{str(i + 1).zfill(3)}{f.suffix}"
            for i, f in enumerate(files)
        )
        if already_sequential:
            return
        # Step 1: Temporary rename to prevent overwriting conflicts
        temp_files = []
        for i, f in enumerate(files):
            tmp_path = f.with_name(f"__temp_seq_{i}{f.suffix}")
            f.rename(tmp_path)
            temp_files.append(tmp_path)
            
        # Step 2: Final rename to 001.ext, 002.ext, etc.
        for i, f in enumerate(temp_files):
            final_name = f"{str(i+1).zfill(3)}{f.suffix}"
            f.rename(f.with_name(final_name))

    # --- FLASK ROUTES ---
    @app.route('/')
    def index():
        main_images_data = []
        for leaf_dir in leaf_dirs:
            current_first = get_current_first_image(leaf_dir)
            if not current_first:
                # In case of empty folders after delete task in edit mode.
                continue

            b64 = base64.urlsafe_b64encode(str(current_first).encode('utf-8')).decode('utf-8')
            path_map[b64] = current_first

            serie_dir = leaf_dir.parent
            site_dir = serie_dir.parent if serie_dir else None

            main_images_data.append({
                'b64': b64,
                'chapter': leaf_dir.name,
                'serie': serie_dir.name if serie_dir else "Unknown",
                'site': site_dir.name if site_dir else "Unknown",
                'is_credit_match': check_credit_match(current_first) is not None
            })

        trash_count = len(load_trash_index(trash_dir))
        return render_template('main.html', images=main_images_data, thumb_size=thumb_size,
                                mask_popups=mask_popups, trash_count=trash_count)

    @app.route('/image/<b64_path>')
    def serve_image(b64_path):
        real_path = path_map.get(b64_path)
        if not real_path and b64_path in PENDING_MERGES:
            real_path = PENDING_MERGES[b64_path]['merged_path']
            
        if real_path and real_path.exists():
            return send_file(str(real_path))
        return "Image not found", 404

    @app.route('/validate', methods=['POST'])
    def validate():
        data = request.json
        to_delete_b64 = data.get('to_delete', [])
        
        errors = []
        for b64 in to_delete_b64:
            file_to_del = path_map.get(b64)
            if file_to_del:
                # INDEPENDENT MANAGEMENT: Safety check if already deleted via edit tab
                if file_to_del.exists():
                    if send_to_trash(file_to_del, trash_dir, "manual_delete"):
                        logging.info(f"Web UI Trashed: {file_to_del}")
                    else:
                        errors.append(str(file_to_del))
                else:
                    logging.info(f"Web UI Delete skipped (Already deleted via edit tab): {file_to_del}")
                    
        completion_event.set()
        return jsonify({"status": "ok", "errors": errors})

    @app.route('/mark_credit', methods=['POST'])
    def mark_credit():
        """Trashes the selected images and remembers their perceptual hash as a
        known 'credit page', so future chapters with the same image are
        pre-flagged for deletion automatically (still requiring user validation)."""
        data = request.json
        selected_b64s = data.get('selected', [])

        errors = []
        learned = 0
        for b64 in selected_b64s:
            file_path = path_map.get(b64)
            if not file_path or not file_path.exists():
                continue

            img_hash = compute_phash(file_path)
            if img_hash and not is_known_credit_hash(img_hash, known_credit_hashes, credit_hash_threshold):
                known_credit_hashes.append(img_hash)
                learned += 1

            if send_to_trash(file_path, trash_dir, "credit_page"):
                logging.info(f"Marked as credit page and trashed: {file_path}")
            else:
                errors.append(str(file_path))

        if learned and credit_hashes_path:
            save_credit_hashes(credit_hashes_path, known_credit_hashes)

        return jsonify({"status": "ok", "errors": errors, "learned": learned})

    @app.route('/api_delete_credit_hash', methods=['POST'])
    def api_delete_credit_hash():
        """Removes a single hash from the known credit-page database (e.g. when
        a 'Known credit' tag turns out to be a false positive). Mirrors
        /api_delete_banner_hash below, for the whole-page credit database
        instead of the top/bottom banner ones."""
        data = request.json or {}
        hash_value = data.get('hash')

        if not hash_value:
            return jsonify({"status": "error", "message": "Missing hash."})

        if hash_value not in known_credit_hashes:
            return jsonify({"status": "ok", "removed": False})

        known_credit_hashes.remove(hash_value)
        if credit_hashes_path:
            save_credit_hashes(credit_hashes_path, known_credit_hashes)
        logging.info(f"Removed credit-page hash from database: {hash_value}")
        return jsonify({"status": "ok", "removed": True})

    @app.route('/edit/<main_b64>')
    def edit_folder(main_b64):
        main_path = path_map.get(main_b64)
        if not main_path:
            return "Main image ID not found", 404
            
        leaf_dir = main_path.parent
        serie_dir = leaf_dir.parent
        site_dir = serie_dir.parent if serie_dir else None

        # Identify pending merges specific to this leaf directory
        folder_merges = {k: v for k, v in PENDING_MERGES.items() if v['leaf_dir'] == leaf_dir}

        # Computed once, used by both phase 1 and phase 2 (navigating away from an
        # unfinished merge review should still be able to jump to the next chapter).
        try:
            current_idx = leaf_dirs.index(leaf_dir)
        except ValueError:
            current_idx = -1

        prev_url = None
        if current_idx > 0:
            prev_leaf = leaf_dirs[current_idx - 1]
            prev_first = get_current_first_image(prev_leaf)
            if prev_first:
                prev_b64 = base64.urlsafe_b64encode(str(prev_first).encode('utf-8')).decode('utf-8')
                path_map[prev_b64] = prev_first
                prev_url = f"/edit/{prev_b64}"

        next_url = None
        if current_idx != -1 and current_idx < len(leaf_dirs) - 1:
            next_leaf = leaf_dirs[current_idx + 1]
            next_first = get_current_first_image(next_leaf)
            if next_first:
                next_b64 = base64.urlsafe_b64encode(str(next_first).encode('utf-8')).decode('utf-8')
                path_map[next_b64] = next_first
                next_url = f"/edit/{next_b64}"

        # Overall progress through the chapter list, used to draw the thin
        # progress bar at the top of the editor page.
        total_chapters = len(leaf_dirs)
        progress_pct = round(((current_idx + 1) / total_chapters) * 100, 2) if (current_idx != -1 and total_chapters > 0) else None

        if not folder_merges:
            # Phase 1: Selection and actions (Delete / Merge / Split)
            try:
                files = sorted([
                    f for f in leaf_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in supported_extensions and not f.name.startswith("fus-")
                ], key=get_natural_key)
            except Exception as e:
                return f"Error accessing leaf folder: {e}", 500
                
            images_data = []
            last_idx = len(files) - 1
            for idx, f in enumerate(files):
                f_b64 = base64.urlsafe_b64encode(str(f).encode('utf-8')).decode('utf-8')
                path_map[f_b64] = f # Enables image viewing via /image/<b64>

                banner_suggestion = None
                if idx == 0:
                    top_result = check_banner_suggestion(f, 'top')
                    if top_result is not None:
                        top_cut, top_hash = top_result
                        banner_suggestion = {'position': 'top', 'cut_pct': top_cut, 'hash': top_hash}
                if idx == last_idx and banner_suggestion is None:
                    bottom_result = check_banner_suggestion(f, 'bottom')
                    if bottom_result is not None:
                        bottom_cut, bottom_hash = bottom_result
                        banner_suggestion = {'position': 'bottom', 'cut_pct': bottom_cut, 'hash': bottom_hash}

                credit_match_hash = check_credit_match(f)

                images_data.append({
                    'b64': f_b64,
                    'display_name': f.name,
                    'chapter': leaf_dir.name,
                    'serie': serie_dir.name if serie_dir else "Unknown",
                    'site': site_dir.name if site_dir else "Unknown",
                    'is_credit_match': credit_match_hash is not None,
                    'credit_hash': credit_match_hash,
                    'banner_suggestion': banner_suggestion
                })
                
            return render_template(
                'editor.html',
                phase=1,
                title=f"Folder Management: {site_dir.name} / {serie_dir.name} / {leaf_dir.name}",
                instructions="Select consecutive images to merge them, click ✂️ to split, or delete them.",
                images=images_data,
                main_b64=main_b64,
                thumb_size=thumb_size,
                prev_url=prev_url,
                next_url=next_url,
                progress_pct=progress_pct,
                mask_popups=mask_popups,
                shortcuts=shortcuts,
                mobile_mini_mode=mobile_mini_mode
            )
        else:
            # Phase 2: Validation of generated merges
            images_data = []
            for b64, info in folder_merges.items():
                images_data.append({
                    'b64': b64,
                    'display_name': info['filename'],
                    'chapter': leaf_dir.name,
                    'serie': serie_dir.name if serie_dir else "Unknown",
                    'site': site_dir.name if site_dir else "Unknown"
                })
            return render_template(
                'editor.html',
                phase=2,
                title=f"Merge Validation: {site_dir.name} / {serie_dir.name} / {leaf_dir.name}",
                instructions="Verify the results. Green borders are kept; click to reject an assembly.",
                images=images_data,
                main_b64=main_b64,
                thumb_size=thumb_size,
                prev_url=prev_url,
                next_url=next_url,
                progress_pct=progress_pct,
                mask_popups=mask_popups,
                shortcuts=shortcuts,
                mobile_mini_mode=mobile_mini_mode
            )

    @app.route('/split/<b64>')
    def split_page(b64):
        if b64 not in path_map:
            return "Image not found", 404
        return_to = request.args.get('return_to', '')
        suggest_pct = request.args.get('suggest_pct', type=float)
        suggest_side = request.args.get('suggest_side', '')
        suggest_hash = request.args.get('suggest_hash', '')

        # Same progress-bar math as the Chapter Editor, derived from this
        # image's own leaf folder so the bar stays accurate when arriving
        # here from the split icon on any chapter.
        leaf_dir = path_map[b64].parent
        try:
            current_idx = leaf_dirs.index(leaf_dir)
        except ValueError:
            current_idx = -1
        total_chapters = len(leaf_dirs)
        progress_pct = round(((current_idx + 1) / total_chapters) * 100, 2) if (current_idx != -1 and total_chapters > 0) else None

        return render_template(
            'split.html', b64=b64, return_to=return_to, mask_popups=mask_popups,
            suggest_pct=suggest_pct, suggest_side=suggest_side, suggest_hash=suggest_hash,
            context='workflow', token='', image_url=f'/image/{b64}',
            progress_pct=progress_pct, shortcuts=shortcuts, mobile_mini_mode=mobile_mini_mode
        )

    @app.route('/api_delete_banner_hash', methods=['POST'])
    def api_delete_banner_hash():
        """Removes a single hash from the known-banner database (e.g. when a
        pre-placed suggestion turns out to be unusable/badly located)."""
        data = request.json
        side = data.get('side')
        hash_value = data.get('hash')

        if side not in ('top', 'bottom') or not hash_value:
            return jsonify({"status": "error", "message": "Invalid side or hash."})

        bucket = known_banners.get(side, [])
        if hash_value not in bucket:
            return jsonify({"status": "ok", "removed": False})

        bucket.remove(hash_value)
        if credit_banners_path:
            save_credit_banners(credit_banners_path, known_banners)
        logging.info(f"Removed banner hash from database ({side}): {hash_value}")
        return jsonify({"status": "ok", "removed": True})

    @app.route('/api_remove_banner', methods=['POST'])
    def api_remove_banner():
        """Crops out a marked top/bottom slice (embedded credit banner) from a
        single image in place, and remembers its hash for future auto-suggestion.
        The pre-crop original is backed up (copied, not moved -- the file must
        still exist for crop_remove_banner to overwrite) to the trash first, so
        the crop can be undone via /trash if it's placed wrong."""
        data = request.json
        b64 = data.get('b64')
        cut_percent = data.get('cut_percent')
        remove_side = data.get('remove_side')

        target_path = path_map.get(b64)
        if not target_path or not target_path.exists():
            return jsonify({"status": "error", "message": "Image not found or already deleted."})
        if remove_side not in ('top', 'bottom'):
            return jsonify({"status": "error", "message": "Invalid side."})
        try:
            cut_percent = float(cut_percent)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid cut position."})
        if not (0 < cut_percent < 100):
            return jsonify({"status": "error", "message": "Cut position out of range."})

        # Back up the pre-crop original before touching the real file. If the
        # backup fails, refuse the crop entirely rather than lose the discarded
        # slice permanently.
        if not send_to_trash(target_path, trash_dir, "banner_crop_source", mode="copy"):
            return jsonify({"status": "error", "message": "Could not back up the original image to the trash; crop aborted to avoid data loss."})

        banner_hash = crop_remove_banner(target_path, cut_percent, remove_side)
        if banner_hash is None:
            return jsonify({"status": "error", "message": "Failed to crop the image."})

        bucket = known_banners.setdefault(remove_side, [])
        learned = not is_known_credit_hash(banner_hash, bucket, credit_banner_threshold)
        if learned:
            bucket.append(banner_hash)
            if credit_banners_path:
                save_credit_banners(credit_banners_path, known_banners)

        return jsonify({"status": "ok", "learned": learned})

    @app.route('/api_do_split', methods=['POST'])
    def api_do_split():
        data = request.json
        b64 = data.get('b64')
        cuts_h = data.get('cuts_h', [])
        cuts_v = data.get('cuts_v', [])
        
        target_path = path_map.get(b64)
        if not target_path or not target_path.exists():
            return jsonify({"status": "error", "message": "Original image lost or deleted."})
            
        leaf_dir = target_path.parent
        
        try:
            with Image.open(target_path) as img:
                width, height = img.size
                
                # Sort cut points and add boundaries
                h_points = sorted([0] + cuts_h + [100])
                v_points = sorted([0] + cuts_v + [100])
                
                part_counter = 1
                
                # Grid slicing loop
                for i in range(len(h_points) - 1):
                    for j in range(len(v_points) - 1):
                        top = int((h_points[i] / 100) * height)
                        bottom = int((h_points[i+1] / 100) * height)
                        left = int((v_points[j] / 100) * width)
                        right = int((v_points[j+1] / 100) * width)
                        
                        # Prevent generating 0px images on overlaps
                        if bottom - top > 0 and right - left > 0:
                            cropped = img.crop((left, top, right, bottom))
                            
                            # Safety check to prevent saving RGBA/Palette as pure JPEG
                            if cropped.mode in ("RGBA", "P") and target_path.suffix.lower() in [".jpg", ".jpeg"]:
                                cropped = cropped.convert("RGB")
                            
                            temp_name = f"{target_path.stem}_split_{part_counter}{target_path.suffix}"
                            cropped.save(leaf_dir / temp_name)
                            part_counter += 1
                            
            # Move the original to the trash instead of deleting it outright,
            # so it can be recovered from /trash if the split cuts were wrong.
            # The split pieces are already saved at this point -- if the trash
            # move fails, we deliberately leave the original in place (rather
            # than losing it) and surface the error instead of resequencing.
            if not send_to_trash(target_path, trash_dir, "split_original"):
                return jsonify({"status": "error", "message": "Split pieces were saved, but moving the original to the trash failed; the original was left in place to avoid data loss."})
            
            # Reprocess all files inside the folder to ensure logical sequencing without conflict
            resequence_folder(leaf_dir, supported_extensions)
            
            return jsonify({"status": "ok"})
            
        except Exception as e:
            logging.error(f"Error during image split for {target_path.name}: {e}")
            return jsonify({"status": "error", "message": str(e)})

    @app.route('/edit_delete', methods=['POST'])
    def edit_delete():
        data = request.json
        selected_b64s = data.get('selected', [])

        for b64 in selected_b64s:
            file_to_del = path_map.get(b64)
            if file_to_del and file_to_del.exists():
                if send_to_trash(file_to_del, trash_dir, "manual_delete"):
                    logging.info(f"Editor Tab Trashed: {file_to_del}")
                else:
                    logging.error(f"Failed to trash {file_to_del} from editor tab")
        return jsonify({"status": "ok"})

    @app.route('/edit_merge', methods=['POST'])
    def edit_merge():
        data = request.json
        selected_b64s = data.get('selected', [])
        main_b64 = data.get('main_b64')
        # 'h' = empile haut/bas (comportement historique, undo d'une Horizontal Cut).
        # 'v' = côte à côte (nouveau, undo d'une Vertical Cut).
        direction = data.get('direction', 'h')
        if direction not in ('h', 'v'):
            direction = 'h'

        main_path = path_map.get(main_b64)
        if not main_path:
            return jsonify({"status": "error", "message": "Parent reference lost."})

        leaf_dir = main_path.parent
        selected_paths = [path_map[b64] for b64 in selected_b64s if b64 in path_map]

        # Natural sorting of selected files, independently of gaps in numbering
        selected_paths.sort(key=get_natural_key)

        merge_func = merge_images_func if direction == 'h' else merge_images_side_by_side_func

        i = 0
        # We process the files 2 by 2
        while i < len(selected_paths) - 1:
            first_path = selected_paths[i]
            second_path = selected_paths[i + 1]

            out_name = f"fus-{first_path.name}"
            out_path = leaf_dir / out_name

            try:
                merge_func(first_path, second_path, out_path)
                m_b64 = base64.urlsafe_b64encode(str(out_path).encode('utf-8')).decode('utf-8')

                # 'top_path'/'bottom_path' restent des noms génériques pour les 2
                # originaux, peu importe la direction -- edit_finalize ne s'en
                # sert que pour les trasher/restaurer, la sémantique du nom
                # n'a pas d'impact fonctionnel.
                PENDING_MERGES[m_b64] = {
                    'merged_path': out_path,
                    'top_path': first_path,
                    'bottom_path': second_path,
                    'filename': out_name,
                    'leaf_dir': leaf_dir
                }
            except Exception as e:
                logging.error(f"Assembly error between {first_path.name} and {second_path.name}: {e}")

            i += 2

        return jsonify({"status": "ok"})

    @app.route('/edit_finalize', methods=['POST'])
    def edit_finalize():
        data = request.json
        rejected_b64s = data.get('rejected', [])
        main_b64 = data.get('main_b64')
        
        main_path = path_map.get(main_b64)
        if not main_path:
            return jsonify({"status": "error", "message": "Invalid folder"})
            
        leaf_dir = main_path.parent
        folder_merges = {k: v for k, v in PENDING_MERGES.items() if v['leaf_dir'] == leaf_dir}
        
        for m_b64, info in folder_merges.items():
            m_path = info['merged_path']
            t_path = info['top_path']
            b_path = info['bottom_path']
            
            if m_b64 in rejected_b64s:
                # Rejected: the fused result itself is the discardable piece --
                # top_path/bottom_path are untouched and still on disk.
                if m_path.exists():
                    send_to_trash(m_path, trash_dir, "merge_rejected")
            else:
                try:
                    # Accepted: the two originals are what's now "cut away" --
                    # everything the user might want back after a bad merge.
                    if t_path.exists():
                        send_to_trash(t_path, trash_dir, "merge_source")
                    if b_path.exists():
                        send_to_trash(b_path, trash_dir, "merge_source")
                    
                    if m_path.name.startswith("fus-"):
                        new_name = m_path.name[4:]
                        new_path = m_path.parent / new_name
                        m_path.rename(new_path)
                except Exception as e:
                    logging.error(f"Error during final validation of file {m_path.name}: {e}")
            
            # Clean up temporary registry
            PENDING_MERGES.pop(m_b64, None)
            
        return jsonify({"status": "ok"})

    # --- TRASH / CORBEILLE ROUTES ---
    @app.route('/trash')
    def trash_page():
        index = load_trash_index(trash_dir)
        # Most recently trashed first.
        index_sorted = sorted(index, key=lambda e: e.get('deleted_at', ''), reverse=True)

        entries = []
        for e in index_sorted:
            original = Path(e.get('original_path', ''))
            reason = e.get('reason', '')
            entries.append({
                'trash_name': e.get('trash_name', ''),
                'file_name': original.name,
                'leaf_name': original.parent.name if original.parent else '',
                'reason_label': TRASH_REASON_LABELS.get(reason, reason or 'Unknown'),
                'deleted_at': e.get('deleted_at', ''),
            })

        return render_template('trash.html', entries=entries, thumb_size=thumb_size, mask_popups=mask_popups)

    @app.route('/trash_image/<trash_name>')
    def trash_image(trash_name):
        # Only serve names present in the manifest -- guards against path
        # traversal via a crafted trash_name in the URL.
        index = load_trash_index(trash_dir)
        if not any(e.get('trash_name') == trash_name for e in index):
            return "Not found", 404
        trash_path = trash_dir / trash_name
        if trash_path.exists():
            return send_file(str(trash_path))
        return "Image not found", 404

    @app.route('/trash_restore', methods=['POST'])
    def trash_restore():
        data = request.json or {}
        selected = data.get('selected', [])

        results = []
        for trash_name in selected:
            ok, message = restore_from_trash(trash_name, trash_dir)
            results.append({'trash_name': trash_name, 'ok': ok, 'message': message})

        return jsonify({"status": "ok", "results": results})

    @app.route('/trash_purge', methods=['POST'])
    def trash_purge():
        count = purge_trash(trash_dir)
        return jsonify({"status": "ok", "purged": count})

    # --- SERVER LAUNCH ---
    # We start the server thread here. 
    # workflow.py will open the browser, wait for event and stop the thread.
    server_thread = ServerThread(app, host, port)
    server_thread.start()
    
    return server_thread, completion_event