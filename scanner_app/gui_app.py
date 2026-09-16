import os
import queue
import threading
import traceback
from pathlib import Path

# WebView2 must not store its profile inside a OneDrive-synced folder -
# cloud sync file locking there causes the embedded browser to crash on interaction.
_webview2_data_dir = Path(os.environ["LOCALAPPDATA"]) / "ZebraTextureViewer" / "WebView2"
_webview2_data_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(_webview2_data_dir))

import webview

from image_scanner import PROJECT_ROOT, _sanitize_name, capture_photo_from_webcam, process_zebra_textures
from local_server import start_local_server

# ------------------------------------------------------------
# Overlay UI injected into the viewer page: "+" button (disabled
# while a scan is running), the name-entry modal, and a bottom-right
# toast log for errors/success. No persistent "status bar" - progress
# is only visible via the button's disabled state and the toast.
# ------------------------------------------------------------
INJECT_JS = r"""
(function () {
  try {
  if (document.getElementById('zs-fab')) { return; }

  var style = document.createElement('style');
  style.innerHTML = `
    #zs-fab {
      position: fixed; top: 20px; right: 20px; z-index: 99999;
      width: 56px; height: 56px; border-radius: 50%; border: none;
      background: #2e7d32; color: #fff; font-size: 30px; line-height: 56px;
      text-align: center; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.4);
      font-family: sans-serif;
    }
    #zs-fab:disabled {
      background: #555; color: #999; cursor: not-allowed; box-shadow: none;
    }
    #zs-overlay {
      display: none; position: fixed; inset: 0; z-index: 99998;
      background: rgba(0,0,0,0.75); align-items: center; justify-content: center;
      font-family: sans-serif; color: #fff;
    }
    #zs-modal {
      background: #1e1e1e; padding: 28px; border-radius: 12px; width: 320px;
      text-align: center; box-shadow: 0 4px 16px rgba(0,0,0,0.5);
    }
    #zs-modal h2 { margin: 0 0 16px 0; font-size: 18px; }
    #zs-name-input {
      width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #444;
      background: #2a2a2a; color: #fff; font-size: 14px; box-sizing: border-box;
      margin-bottom: 16px;
    }
    .zs-btn-row { display: flex; gap: 10px; justify-content: center; }
    .zs-btn {
      flex: 1; padding: 10px; border-radius: 6px; border: none; cursor: pointer;
      font-size: 14px; font-weight: bold;
    }
    .zs-btn-primary { background: #2e7d32; color: #fff; }
    .zs-btn-secondary { background: #444; color: #fff; }
    #zs-toast-container {
      position: fixed; bottom: 20px; right: 20px; z-index: 100000;
      display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
      pointer-events: none; font-family: sans-serif;
    }
    .zs-toast {
      font-size: 14px; text-shadow: 0 1px 3px rgba(0,0,0,0.9);
      transition: opacity 0.5s ease; opacity: 1;
    }
    .zs-toast-error { color: #ff5252; }
    .zs-toast-success { color: #69f0ae; }
    .zs-toast-hide { opacity: 0; }
  `;
  document.head.appendChild(style);

  var fab = document.createElement('button');
  fab.id = 'zs-fab';
  fab.innerHTML = '+';
  fab.onclick = function () { pywebview.api.start_scan(); };
  document.body.appendChild(fab);

  var overlay = document.createElement('div');
  overlay.id = 'zs-overlay';
  overlay.innerHTML = `
    <div id="zs-modal">
      <h2>Name Your Zebra</h2>
      <input id="zs-name-input" type="text" placeholder="Enter a name" />
      <div class="zs-btn-row">
        <button class="zs-btn zs-btn-secondary" onclick="ZebraScanner.cancel()">Cancel</button>
        <button class="zs-btn zs-btn-primary" onclick="ZebraScanner.submitName()">Start</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  var toastContainer = document.createElement('div');
  toastContainer.id = 'zs-toast-container';
  document.body.appendChild(toastContainer);

  document.getElementById('zs-name-input').addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { ZebraScanner.submitName(); }
  });

  window.ZebraScanner = {
    showNamePrompt: function () {
      document.getElementById('zs-overlay').style.display = 'flex';
      var input = document.getElementById('zs-name-input');
      input.value = '';
      input.focus();
    },
    hideOverlay: function () {
      document.getElementById('zs-overlay').style.display = 'none';
    },
    setScanning: function (scanning) {
      document.getElementById('zs-fab').disabled = !!scanning;
    },
    showToast: function (type, message) {
      var el = document.createElement('div');
      el.className = 'zs-toast zs-toast-' + type;
      el.innerText = message;
      toastContainer.appendChild(el);
      var duration = type === 'error' ? 20000 : 4000;
      setTimeout(function () {
        el.classList.add('zs-toast-hide');
        setTimeout(function () { el.remove(); }, 500);
      }, duration);
    },
    refreshTextures: function () {
      if (window.zebraViewerRefresh) { window.zebraViewerRefresh(); }
    },
    submitName: function () {
      var name = document.getElementById('zs-name-input').value;
      pywebview.api.submit_zebra_name(name);
    },
    cancel: function () {
      pywebview.api.cancel_scan();
    }
  };
  } catch (err) {
    console.error('Zebra overlay injection failed:', err);
  }
})();
"""


class Api:
    """Bridges the injected page overlay to the Python scanning workflow."""

    def __init__(self):
        self._name_queue = queue.Queue()
        self.is_scanning = False

    def _emit(self, script: str):
        # Fetched lazily (never stored) - storing the Window on this API object
        # makes pywebview's method-discovery recurse into window.native and crash.
        window = webview.active_window()
        if window:
            try:
                window.evaluate_js(script)
            except Exception:
                pass

    def submit_zebra_name(self, name):
        self._name_queue.put(name)

    def cancel_scan(self):
        self._name_queue.put(None)

    def start_scan(self):
        if self.is_scanning:
            return False
        self.is_scanning = True
        self._emit("ZebraScanner.setScanning(true)")
        threading.Thread(target=self._run_scan, daemon=True).start()
        return True

    def _run_scan(self):
        try:
            self._emit("ZebraScanner.showNamePrompt()")
            raw_name = self._name_queue.get()
            if raw_name is None:
                self._emit("ZebraScanner.hideOverlay()")
                return

            zebra_name = _sanitize_name(raw_name)
            self._emit("ZebraScanner.hideOverlay()")

            captured_image_path = PROJECT_ROOT / "docs" / "assets" / "image" / "captured_coloring_sheet.jpg"
            if not capture_photo_from_webcam(captured_image_path):
                return

            process_zebra_textures(zebra_name, captured_image_path)

            self._emit(f"ZebraScanner.showToast('success', \"Zebra '{zebra_name}' uploaded!\")")
            self._emit("ZebraScanner.refreshTextures()")
        except Exception as e:
            message = str(e).replace("'", "\\'").replace("\n", " ").replace('"', "'")
            self._emit("ZebraScanner.hideOverlay()")
            self._emit(f"ZebraScanner.showToast('error', \"{message}\")")
        finally:
            self.is_scanning = False
            self._emit("ZebraScanner.setScanning(false)")


def on_loaded(window):
    try:
        window.evaluate_js(INJECT_JS)
    except Exception:
        traceback.print_exc()


def main():
    port = start_local_server(PROJECT_ROOT / "docs")
    local_url = f"http://127.0.0.1:{port}/local_index.html"

    api = Api()
    window = webview.create_window("Zebra Texture Viewer", local_url, js_api=api, width=1280, height=800)
    window.events.loaded += lambda: on_loaded(window)
    try:
        webview.start(gui="edgechromium")
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
