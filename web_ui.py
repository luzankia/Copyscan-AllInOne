import threading
import base64
import logging
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.serving import make_server
from PIL import Image

class ServerThread(threading.Thread):
    def __init__(self, app, port):
        threading.Thread.__init__(self)
        self.server = make_server('127.0.0.1', port, app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

def start_web_ui(images_list, port, thumb_size, supported_extensions, mask_popups=False):
    """Starts the Flask server for manual sorting, merging, and image splitting."""
    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    completion_event = threading.Event()
    
    # Shared registries within the server instance
    path_map = {}
    PENDING_MERGES = {} # b64_fusion -> { 'merged_path', 'top_path', 'bottom_path', 'filename', 'leaf_dir' }

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

    def get_natural_key(path):
        """Allows natural sorting of files (e.g., 2.jpg comes before 10.jpg)."""
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', path.name)]

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
                'site': site_dir.name if site_dir else "Unknown"
            })
        return render_template('main.html', images=main_images_data, thumb_size=thumb_size, mask_popups=mask_popups)

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
                try:
                    # INDEPENDENT MANAGEMENT: Safety check if already deleted via edit tab
                    if file_to_del.exists():
                        file_to_del.unlink()
                        logging.info(f"Web UI Deleted: {file_to_del}")
                    else:
                        logging.info(f"Web UI Delete skipped (Already deleted via edit tab): {file_to_del}")
                except Exception as e:
                    errors.append(str(file_to_del))
                    logging.error(f"Failed to delete {file_to_del}: {e}")
                    
        completion_event.set()
        return jsonify({"status": "ok", "errors": errors})

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
        
        if not folder_merges:
            # Phase 1: Selection and actions (Delete / Merge / Split)
            
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
            
            try:
                files = sorted([
                    f for f in leaf_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in supported_extensions and not f.name.startswith("fus-")
                ], key=get_natural_key)
            except Exception as e:
                return f"Error accessing leaf folder: {e}", 500
                
            images_data = []
            for f in files:
                f_b64 = base64.urlsafe_b64encode(str(f).encode('utf-8')).decode('utf-8')
                path_map[f_b64] = f # Enables image viewing via /image/<b64>
                
                images_data.append({
                    'b64': f_b64,
                    'display_name': f.name,
                    'chapter': leaf_dir.name,
                    'serie': serie_dir.name if serie_dir else "Unknown",
                    'site': site_dir.name if site_dir else "Unknown"
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
                mask_popups=mask_popups
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
                prev_url=None,
                next_url=None,
                mask_popups=mask_popups
            )

    @app.route('/split/<b64>')
    def split_page(b64):
        if b64 not in path_map:
            return "Image not found", 404
        return_to = request.args.get('return_to', '')
        return render_template('split.html', b64=b64, return_to=return_to, mask_popups=mask_popups)

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
                            
            # Remove original file safely
            target_path.unlink()
            
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
                try:
                    file_to_del.unlink()
                    logging.info(f"Editor Tab Deleted: {file_to_del}")
                except Exception as e:
                    logging.error(f"Failed to delete {file_to_del} from editor tab: {e}")
        return jsonify({"status": "ok"})

    @app.route('/edit_merge', methods=['POST'])
    def edit_merge():
        data = request.json
        selected_b64s = data.get('selected', [])
        main_b64 = data.get('main_b64')
        
        main_path = path_map.get(main_b64)
        if not main_path:
            return jsonify({"status": "error", "message": "Parent reference lost."})
            
        leaf_dir = main_path.parent
        selected_paths = [path_map[b64] for b64 in selected_b64s if b64 in path_map]
        
        # Natural sorting of selected files, independently of gaps in numbering
        selected_paths.sort(key=get_natural_key)
        
        i = 0
        # We process the files 2 by 2
        while i < len(selected_paths) - 1:
            top_path = selected_paths[i]
            bottom_path = selected_paths[i + 1]
            
            out_name = f"fus-{top_path.name}"
            out_path = leaf_dir / out_name
            
            try:
                merge_images_func(top_path, bottom_path, out_path)
                m_b64 = base64.urlsafe_b64encode(str(out_path).encode('utf-8')).decode('utf-8')
                
                PENDING_MERGES[m_b64] = {
                    'merged_path': out_path,
                    'top_path': top_path,
                    'bottom_path': bottom_path,
                    'filename': out_name,
                    'leaf_dir': leaf_dir
                }
            except Exception as e:
                logging.error(f"Assembly error between {top_path.name} and {bottom_path.name}: {e}")
            
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
                if m_path.exists(): m_path.unlink()
            else:
                try:
                    if t_path.exists(): t_path.unlink()
                    if b_path.exists(): b_path.unlink()
                    
                    if m_path.name.startswith("fus-"):
                        new_name = m_path.name[4:]
                        new_path = m_path.parent / new_name
                        m_path.rename(new_path)
                except Exception as e:
                    logging.error(f"Error during final validation of file {m_path.name}: {e}")
            
            # Clean up temporary registry
            PENDING_MERGES.pop(m_b64, None)
            
        return jsonify({"status": "ok"})

    # --- SERVER LAUNCH ---
    # We start the server thread here. 
    # workflow.py will open the browser, wait for event and stop the thread.
    server_thread = ServerThread(app, port)
    server_thread.start()
    
    return server_thread, completion_event