import threading
import base64
import logging
import re
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_file
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


def start_web_ui(images_list, port, thumb_size, supported_extensions):
    """Starts the Flask server for manual sorting, merging, and image splitting."""
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

    # --- HELPERS ---
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
                <p style="margin:5px 0 0 0; color:#aaa;">Select images to discard, or click ✏️ to open the leaf folder in an independent merge/split/delete tool.</p>
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
            
            .split-icon { position: absolute; top: 6px; right: 6px; background: #8b5cf6; padding: 5px; border-radius: 5px; cursor: pointer; z-index: 10; color: #fff; font-size: 16px; border: 1px solid #444; line-height: 1; transition: 0.2s; }
            .split-icon:hover { background: #7c3aed; transform: scale(1.1); box-shadow: 0 0 8px #8b5cf6; }

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
                        <div class="split-icon" onclick="openSplit(event, '{{ item.b64 }}')" title="Split this image">✂️</div>
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
            
            function openSplit(event, b64) {
                event.stopPropagation();
                window.open('/split/' + b64, '_blank');
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

    SPLIT_HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Split Image Studio</title>
        <style>
            body { font-family: 'Segoe UI', Arial, sans-serif; background: #121212; color: #fff; margin: 0; padding: 0; display: flex; flex-direction: column; height: 100vh; }
            .toolbar { background: #1a1a1a; padding: 15px; border-bottom: 2px solid #333; display: flex; justify-content: space-between; align-items: center; z-index: 1000; box-shadow: 0 2px 10px rgba(0,0,0,0.5); }
            .controls { display: flex; gap: 12px; align-items: center; }
            
            .btn { color: white; padding: 10px 15px; border: none; border-radius: 5px; cursor: pointer; font-weight: bold; transition: 0.2s; font-size: 13px;}
            .btn.primary { background: #8b5cf6; }
            .btn.danger { background: #ef4444; }
            .btn.secondary { background: #4b5563; }
            .btn:hover { opacity: 0.9; }
            .btn.active { box-shadow: 0 0 0 3px #10b981; }

            .workspace { flex-grow: 1; overflow: auto; position: relative; display: flex; justify-content: center; align-items: flex-start; padding: 50px; background: #0a0a0a; }
            
            .image-container { position: relative; box-shadow: 0 0 20px rgba(0,0,0,0.8); cursor: crosshair; transform-origin: top center; transition: width 0.2s; width: 100%; max-width: 800px; }
            .image-container img { width: 100%; display: block; user-select: none; pointer-events: none; }
            
            .marker-h { position: absolute; left: 0; width: 100%; height: 2px; background: #ef4444; z-index: 10; box-shadow: 0 0 5px #000; pointer-events: none; }
            .marker-v { position: absolute; top: 0; height: 100%; width: 2px; background: #3b82f6; z-index: 10; box-shadow: 0 0 5px #000; pointer-events: none; }
        </style>
    </head>
    <body>
        <div class="toolbar">
            <div>
                <h3 style="margin:0;">✂️ Image Split Studio</h3>
                <small style="color:#aaa;">Click anywhere on the image to place your cutting markers.</small>
            </div>
            <div class="controls">
                <button class="btn secondary" onclick="changeZoom(-20)">Zoom -</button>
                <span id="zoom-level" style="min-width: 45px; text-align: center;">100%</span>
                <button class="btn secondary" onclick="changeZoom(20)">Zoom +</button>
                <div style="width: 2px; height: 30px; background: #444; margin: 0 5px;"></div>
                <button id="btn-mode-h" class="btn active" style="background:#ef4444;" onclick="setMode('h')">Horizontal Cut</button>
                <button id="btn-mode-v" class="btn" style="background:#3b82f6;" onclick="setMode('v')">Vertical Cut</button>
                <button class="btn secondary" onclick="clearMarkers()">Clear Markers</button>
                <button class="btn primary" onclick="submitSplit()">Execute Split</button>
            </div>
        </div>
        
        <div class="workspace">
            <div class="image-container" id="img-container" onclick="addMarker(event)">
                <img src="/image/{{ b64 }}" id="target-image" />
                <div id="markers-layer"></div>
            </div>
        </div>

        <script>
            let currentZoom = 100; // Reference percentage
            let mode = 'h'; // 'h' or 'v'
            let cutsH = []; // Store percentages for responsive cutting
            let cutsV = [];

            function changeZoom(delta) {
                currentZoom = Math.max(20, Math.min(500, currentZoom + delta));
                document.getElementById('zoom-level').innerText = currentZoom + '%';
                
                const container = document.getElementById('img-container');
                container.style.maxWidth = 'none'; // Uncap size for heavy zoom-ins
                container.style.width = (800 * (currentZoom / 100)) + 'px';
            }

            function setMode(newMode) {
                mode = newMode;
                document.getElementById('btn-mode-h').classList.remove('active');
                document.getElementById('btn-mode-v').classList.remove('active');
                document.getElementById('btn-mode-' + newMode).classList.add('active');
            }

            function addMarker(e) {
                const rect = e.currentTarget.getBoundingClientRect();
                const layer = document.getElementById('markers-layer');
                
                if (mode === 'h') {
                    const yPos = e.clientY - rect.top;
                    const percentY = (yPos / rect.height) * 100;
                    cutsH.push(percentY);
                    
                    const marker = document.createElement('div');
                    marker.className = 'marker-h';
                    marker.style.top = percentY + '%';
                    layer.appendChild(marker);
                } else {
                    const xPos = e.clientX - rect.left;
                    const percentX = (xPos / rect.width) * 100;
                    cutsV.push(percentX);
                    
                    const marker = document.createElement('div');
                    marker.className = 'marker-v';
                    marker.style.left = percentX + '%';
                    layer.appendChild(marker);
                }
            }

            function clearMarkers() {
                cutsH = [];
                cutsV = [];
                document.getElementById('markers-layer').innerHTML = '';
            }

            function submitSplit() {
                if (cutsH.length === 0 && cutsV.length === 0) {
                    alert("Please place at least one marker.");
                    return;
                }
                
                if (!confirm("This will divide the image and sequentially renumber the entire folder. Proceed?")) return;

                fetch('/api_do_split', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        b64: '{{ b64 }}',
                        cuts_h: cutsH,
                        cuts_v: cutsV
                    })
                }).then(res => res.json()).then(data => {
                    if (data.status === 'ok') {
                        alert("Split and sequence renumbering successful! Close this tab and refresh the parent window.");
                        window.close();
                    } else {
                        alert("Error: " + data.message);
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
            # Phase 1: Selection and actions (Delete / Merge / Split)
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
                
            return render_template_string(
                EDITOR_HTML_TEMPLATE,
                phase=1,
                title=f"Folder Management: {site_dir.name} / {serie_dir.name} / {leaf_dir.name}",
                instructions="Select consecutive images to merge them, click ✂️ to split, or delete them.",
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

    @app.route('/split/<b64>')
    def split_page(b64):
        if b64 not in path_map:
            return "Image not found", 404
        return render_template_string(SPLIT_HTML_TEMPLATE, b64=b64)

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
        
        # Tri naturel des fichiers sélectionnés, indépendamment des trous dans la numérotation
        selected_paths.sort(key=get_natural_key)
        
        i = 0
        # On traite les fichiers 2 par 2
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