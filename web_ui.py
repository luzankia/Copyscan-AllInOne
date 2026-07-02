import threading
import base64
import logging
import re
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_file
from werkzeug.serving import make_server
from PIL import Image
from utils import console

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


def start_web_ui(images_list, port, thumb_size, supported_extensions):
    """Starts the Flask server for manual sorting and per-folder advanced editing."""
    app = Flask(__name__)
    completion_event = threading.Event()
    
    # Shared registries within the server instance
    path_map = {}
    main_images_data = []
    PENDING_MERGES = {} # b64_fusion -> { 'merged_path', 'top_path', 'bottom_path', 'filename', 'leaf_dir' }

    # Initial population for the main page
    for img_path in images_list:
        b64 = base64.urlsafe_b64encode(str(img_path).encode('utf-8')).decode('utf-8')
        path_map[b64] = img_path
        
        leaf_dir = img_path.parent
        serie_dir = leaf_dir.parent
        site_dir = serie_dir.parent if serie_dir else None
        
        main_images_data.append({
            'b64': b64,
            'chapter': leaf_dir.name,
            'serie': serie_dir.name if serie_dir else "Unknown",
            'site': site_dir.name if site_dir else "Unknown"
        })

    # --- MERGE HELPERS ---
    def get_number(filename):
        match = re.search(r"\d+", filename)
        if match:
            return int(match.group())
        raise ValueError("No number found in filename")

    def merge_images_func(top_path, bottom_path, output_path):
        with Image.open(top_path) as top_img, Image.open(bottom_path) as bottom_img:
            width = max(top_img.width, bottom_img.width)
            height = top_img.height + bottom_img.height
            merged = Image.new("RGB", (width, height))
            merged.paste(top_img, (0, 0))
            merged.paste(bottom_img, (0, top_img.height))
            merged.save(output_path)

    # --- HTML TEMPLATES ---
    MAIN_HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Manual Image Sorting</title>
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; background: #121212; color: #fff; margin: 0; padding: 20px; }
            .header { display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; background: #1a1a1a; padding: 15px; z-index: 1000; border-bottom: 2px solid #333; border-radius: 8px; }
            .gallery { display: flex; flex-wrap: wrap; gap: 20px; justify-content: center; margin-top: 20px; }
            
            .card { position: relative; cursor: pointer; border: 4px solid transparent; border-radius: 8px; transition: 0.2s; background: #222; text-align: center; width: {{ thumb_size }}; }
            .card.selected { border-color: #ef4444; box-shadow: 0 0 12px #ef4444; }
            .card-label { padding: 6px; font-size: 11px; background: #1a1a1a; color: #aaa; word-break: break-all; border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }
            
            img { width: 100%; height: auto; max-height: 400px; display: block; object-fit: contain; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            
            .edit-icon { position: absolute; top: 6px; right: 6px; background: rgba(0, 0, 0, 0.8); padding: 5px; border-radius: 5px; cursor: pointer; z-index: 10; color: #fff; font-size: 16px; transition: 0.2s; border: 1px solid #444; line-height: 1; }
            .edit-icon:hover { background: #3b82f6; border-color: #60a5fa; transform: scale(1.1); }
            
            .tooltip { position: absolute; bottom: -20px; left: 50%; transform: translateX(-50%); background: rgba(0, 0, 0, 0.95); color: #fff; padding: 12px; border-radius: 6px; font-size: 13px; white-space: nowrap; opacity: 0; transition: all 0.2s ease; pointer-events: none; z-index: 9999; border: 1px solid #555; box-shadow: 0 4px 10px rgba(0,0,0,0.5); text-align: left; }
            .card:hover .tooltip { opacity: 1; bottom: 35px; }
            
            .btn { background: #ef4444; color: white; padding: 12px 24px; border: none; border-radius: 5px; cursor: pointer; font-size: 15px; font-weight: bold; transition: 0.2s; }
            .btn:hover { opacity: 0.9; }
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <h2 style="margin:0;">Manual Sort & Advanced Editing</h2>
                <p style="margin:5px 0 0 0; color:#aaa;">Select images to discard, or click ✏️ to open the leaf folder in an independent merge/delete tool.</p>
            </div>
            <button class="btn" onclick="submitSelection()">Validate & Delete Selection</button>
        </div>
        
        <div class="gallery">
            {% for item in images %}
                <div class="card" id="card-{{ item.b64 }}" onclick="toggleSelect('{{ item.b64 }}')">
                    <div class="edit-icon" onclick="openEditor(event, '{{ item.b64 }}')" title="Open leaf folder manager">✏️</div>
                    <img src="/image/{{ item.b64 }}" loading="lazy" />
                    <div class="card-label">Leaf Preview</div>
                    <div class="tooltip">
                        <strong>Chapter:</strong> {{ item.chapter }}<br>
                        <strong>Serie:</strong> {{ item.serie }}<br>
                        <strong>Site:</strong> {{ item.site }}
                    </div>
                </div>
            {% endfor %}
        </div>

        <script>
            let toDelete = [];

            function toggleSelect(b64) {
                const el = document.getElementById('card-' + b64);
                el.classList.toggle('selected');
                if (toDelete.includes(b64)) {
                    toDelete = toDelete.filter(id => id !== b64);
                } else {
                    toDelete.push(b64);
                }
            }

            function openEditor(event, b64) {
                event.stopPropagation();
                window.open('/edit/' + b64, '_blank');
            }

            function submitSelection() {
                if (toDelete.length === 0) {
                    if (!confirm("No images selected for global deletion. Proceed to next step?")) return;
                } else {
                    if (!confirm("Are you sure you want to permanently delete the " + toDelete.length + " selected item(s)?")) return;
                }
                
                fetch('/validate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({to_delete: toDelete})
                }).then(res => res.json()).then(data => {
                    document.body.innerHTML = "<div style='text-align:center; margin-top:100px;'><h1>Changes saved!</h1><p>The workflow is resuming in the console...</p></div>";
                });
            }
        </script>
    </body>
    </html>
    """

    EDITOR_HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>{{ title }}</title>
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; background: #121212; color: #fff; margin: 0; padding: 20px; }
            .header { position: sticky; top: 0; background: #1a1a1a; padding: 15px; z-index: 1000; border-bottom: 2px solid #333; display: flex; justify-content: space-between; align-items: center; border-radius: 8px; }
            .instructions { font-size: 14px; color: #aaa; margin: 5px 0 0 0; }
            .actions { display: flex; gap: 10px; }
            .gallery { display: flex; flex-wrap: wrap; gap: 20px; justify-content: center; margin-top: 20px; }
            
            .card { position: relative; cursor: pointer; border: 4px solid transparent; border-radius: 8px; transition: 0.2s; background: #222; text-align: center; width: {{ thumb_size }}; }
            .card.selected { border-color: #3b82f6; box-shadow: 0 0 12px #3b82f6; }
            .card.approved { border-color: #10b981; }
            .card.rejected { border-color: #ef4444; opacity: 0.3; }
            
            .card-label { padding: 6px; font-size: 12px; background: #1a1a1a; color: #ccc; word-break: break-all; border-bottom-left-radius: 4px; border-bottom-right-radius: 4px; }
            
            img { width: 100%; height: auto; display: block; object-fit: contain; max-height: 400px; border-top-left-radius: 4px; border-top-right-radius: 4px; }
            
            .tooltip { position: absolute; bottom: -20px; left: 50%; transform: translateX(-50%); background: rgba(0, 0, 0, 0.95); color: #fff; padding: 12px; border-radius: 6px; font-size: 13px; white-space: nowrap; opacity: 0; transition: all 0.2s ease; pointer-events: none; z-index: 9999; border: 1px solid #555; box-shadow: 0 4px 10px rgba(0,0,0,0.5); text-align: left; }
            .card:hover .tooltip { opacity: 1; bottom: 45px; }
            
            .btn { color: white; padding: 12px 20px; border: none; border-radius: 5px; cursor: pointer; font-size: 14px; font-weight: bold; transition: 0.2s; }
            .btn.primary { background: #3b82f6; }
            .btn.secondary { background: #4b5563; }
            .btn.danger { background: #ef4444; }
            .btn.success { background: #10b981; }
            .btn:hover { opacity: 0.9; }
        </style>
    </head>
    <body>
        <div class="header">
            <div>
                <h2 style="margin:0;">{{ title }}</h2>
                <p class="instructions">{{ instructions }}</p>
            </div>
            <div class="actions">
                {% if phase == 1 %}
                    <button class="btn secondary" onclick="selectAll()">Select All</button>
                    <button class="btn secondary" onclick="deselectAll()">Deselect All</button>
                    <button class="btn primary" onclick="submitSelection('merge')">Merge Pairs</button>
                    <button class="btn danger" onclick="submitSelection('delete')">Delete Selection</button>
                {% else %}
                    <button class="btn success" onclick="submitValidation()">Validate & Finalize Merges</button>
                {% endif %}
            </div>
        </div>
        
        <div class="gallery">
            {% for item in images %}
                {% if phase == 1 %}
                    <div class="card" id="card-{{ item.b64 }}" data-b64="{{ item.b64 }}" onclick="toggleSelect('{{ item.b64 }}')">
                        <img src="/image/{{ item.b64 }}" loading="lazy" />
                        <div class="card-label">{{ item.display_name }}</div>
                        <div class="tooltip">
                            <strong>Chapter:</strong> {{ item.chapter }}<br>
                            <strong>Serie:</strong> {{ item.serie }}<br>
                            <strong>Site:</strong> {{ item.site }}
                        </div>
                    </div>
                {% else %}
                    <div class="card approved" id="card-{{ item.b64 }}" data-b64="{{ item.b64 }}" onclick="toggleReject('{{ item.b64 }}')">
                        <img src="/image/{{ item.b64 }}" loading="lazy" />
                        <div class="card-label">{{ item.display_name }}</div>
                        <div class="tooltip">
                            <strong>Chapter:</strong> {{ item.chapter }}<br>
                            <strong>Serie:</strong> {{ item.serie }}<br>
                            <strong>Site:</strong> {{ item.site }}
                        </div>
                    </div>
                {% endif %}
            {% endfor %}
        </div>

        <script>
            let selected = [];
            let rejected = [];

            function toggleSelect(b64) {
                const el = document.getElementById('card-' + b64);
                el.classList.toggle('selected');
                if (selected.includes(b64)) {
                    selected = selected.filter(id => id !== b64);
                } else {
                    selected.push(b64);
                }
            }

            function selectAll() {
                selected = [];
                document.querySelectorAll('.card').forEach(card => {
                    card.classList.add('selected');
                    selected.push(card.getAttribute('data-b64'));
                });
            }

            function deselectAll() {
                document.querySelectorAll('.card').forEach(card => {
                    card.classList.remove('selected');
                });
                selected = [];
            }

            function toggleReject(b64) {
                const el = document.getElementById('card-' + b64);
                el.classList.toggle('approved');
                el.classList.toggle('rejected');
                if (rejected.includes(b64)) {
                    rejected = rejected.filter(id => id !== b64);
                } else {
                    rejected.push(b64);
                }
            }

            function submitSelection(action) {
                if (selected.length === 0) {
                    alert("Please select at least one image.");
                    return;
                }
                
                if (action === 'delete') {
                    if (!confirm("Warning! Do you really want to permanently DELETE the selected images?")) return;
                    fetch('/edit_delete', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({selected: selected})
                    }).then(res => res.json()).then(data => {
                        if (data.status === 'ok') window.location.reload();
                    });
                } else if (action === 'merge') {
                    fetch('/edit_merge', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({selected: selected, main_b64: '{{ main_b64 }}'})
                    }).then(res => res.json()).then(data => {
                        if (data.status === 'ok') window.location.reload();
                        else alert("Error: " + data.message);
                    });
                }
            }

            function submitValidation() {
                fetch('/edit_finalize', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({rejected: rejected, main_b64: '{{ main_b64 }}'})
                }).then(res => res.json()).then(data => {
                    if (data.status === 'ok') {
                        document.body.innerHTML = "<div style='text-align:center; margin-top:100px;'><h1>Processing completed!</h1><p>You can safely close this tab.</p></div>";
                    }
                });
            }
        </script>
    </body>
    </html>
    """

    # --- FLASK ROUTES ---
    @app.route('/')
    def index():
        return render_template_string(MAIN_HTML_TEMPLATE, images=main_images_data, thumb_size=thumb_size)

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
            # Phase 1: Selection and actions (Delete / Merge)
            try:
                files = sorted([
                    f for f in leaf_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in supported_extensions and not f.name.startswith("fus-")
                ], key=lambda x: x.name)
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
                
            return render_template_string(
                EDITOR_HTML_TEMPLATE,
                phase=1,
                title=f"Folder Management: {site_dir.name} / {serie_dir.name} / {leaf_dir.name}",
                instructions="Select consecutive images to merge them, or permanently delete them from disk.",
                images=images_data,
                main_b64=main_b64,
                thumb_size=thumb_size
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
            return render_template_string(
                EDITOR_HTML_TEMPLATE,
                phase=2,
                title=f"Merge Validation: {site_dir.name} / {serie_dir.name} / {leaf_dir.name}",
                instructions="Verify the results. Green borders are kept; click to reject an assembly.",
                images=images_data,
                main_b64=main_b64,
                thumb_size=thumb_size
            )

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
        
        # Extraction and numeric sorting of files
        numbered = {}
        for p in selected_paths:
            try:
                num = get_number(p.name)
                numbered[num] = p
            except ValueError:
                pass
                
        sorted_nums = sorted(numbered.keys())
        i = 0
        while i < len(sorted_nums) - 1:
            curr = sorted_nums[i]
            nxt = sorted_nums[i + 1]
            
            if nxt == curr + 1:
                top_path = numbered[curr]
                bottom_path = numbered[nxt]
                
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
            else:
                i += 1
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