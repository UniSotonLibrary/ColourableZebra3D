import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _make_handler(docs_dir: Path, textures_dir: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(docs_dir), **kwargs)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/api/textures":
                self._send_textures_list()
                return
            super().do_GET()

        def _send_textures_list(self):
            files = sorted(p.name for p in textures_dir.glob("*") if p.is_file())
            # Mirror the shape of GitHub's contents API so viewer.js needs no branching.
            body = json.dumps([{"name": name} for name in files]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass  # keep the terminal quiet for routine asset requests

    return Handler


def start_local_server(docs_dir: Path) -> int:
    """Serves the docs/ folder on 127.0.0.1 (random free port) and returns that port."""
    textures_dir = docs_dir / "assets" / "textures"
    handler = _make_handler(docs_dir, textures_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return port
