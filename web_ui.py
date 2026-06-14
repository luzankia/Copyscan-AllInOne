import threading
import base64
import logging
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, send_file
from werkzeug.serving import make_server
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

def start_web_ui(images_list, port, thumb_size):
    """Starts the Flask server for manual sorting (Step 2)."""
    app = Flask(__name__)
    completion_event = threading.Event()
    
    HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Manual Image Sorting</title>
        <style>
            body { font-family: Arial, sans-serif; background: #121212; color: #fff; margin: 0; padding: 20px; }
            .header { display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; background: #121212; padding: 10px 0; z-index: 1000; border-bottom: 2px solid #333; }
            .gallery { display: flex; flex-wrap: wrap; gap: 15px; justify-content: center; margin-top: 20px; }
            .card { cursor: pointer; border: 3px solid transparent; border-radius: 8px; overflow: hidden; transition: 0.2s; }
            .card.selected { border-color: #ff4444; opacity: 0.6; }
            img { width: {{ thumb_size }}; height: auto; display: block; object-fit: cover; }
            .btn { background: #4CAF50; color: white; padding: 15px 25px; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; font-weight: bold; }
            .btn:hover { background: #45a049; }
        </style>
    </head>
    <body>
        <div class="header">
            <h2>Select images to DELETE</h2>
            <button class="btn" onclick="validate()">Validate & Close</button>
        </div>
        <div class="gallery">
            {% for b64_path in images %}
            <div class="card" onclick="toggleSelect(this, '{{ b64_path }}')">
                <img src="/image/{{ b64_path }}" loading="lazy" />
            </div>
            {% endfor %}
        </div>
        <script>
            let selected = [];
            function toggleSelect(el, path) {
                el.classList.toggle('selected');
                if (selected.includes(path)) {
                    selected = selected.filter(p => p !== path);
                } else {
                    selected.push(path);
                }
            }
            function validate() {
                fetch('/validate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({to_delete: selected})
                }).then(res => res.json()).then(data => {
                    document.body.innerHTML = "<h2 style='text-align:center; margin-top:50px;'>Processing complete. You can close this tab.</h2>";
                });
            }
        </script>
    </body>
    </html>
    """

    # Prepare safe base64 paths
    b64_images = []
    path_map = {}
    for img_path in images_list:
        b64 = base64.urlsafe_b64encode(str(img_path).encode('utf-8')).decode('utf-8')
        b64_images.append(b64)
        path_map[b64] = img_path

    @app.route('/')
    def index():
        return render_template_string(HTML_TEMPLATE, images=b64_images, thumb_size=thumb_size)

    @app.route('/image/<b64_path>')
    def serve_image(b64_path):
        real_path = path_map.get(b64_path)
        if real_path and real_path.exists():
            return send_file(str(real_path))
        return "Not found", 404

    @app.route('/validate', methods=['POST'])
    def validate():
        data = request.json
        to_delete_b64 = data.get('to_delete', [])
        
        errors = []
        for b64 in to_delete_b64:
            file_to_del = path_map.get(b64)
            if file_to_del and file_to_del.exists():
                try:
                    file_to_del.unlink()
                    logging.info(f"Web UI Deleted: {file_to_del}")
                except Exception as e:
                    errors.append(str(file_to_del))
                    logging.error(f"Web UI Deletion error {file_to_del}: {e}")
        
        if errors:
            console.print(f"[yellow]Web UI Warnings: Failed to delete {len(errors)} files.[/yellow]")
        
        completion_event.set()
        return jsonify({"status": "ok"})

    server_thread = ServerThread(app, port)
    server_thread.start()
    return server_thread, completion_event