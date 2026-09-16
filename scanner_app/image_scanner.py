import base64
import os
import queue
import re
import threading
import traceback
from pathlib import Path

# WebView2 must not store its profile inside a OneDrive-synced folder -
# cloud sync file locking there causes the embedded browser to crash on interaction.
_webview2_data_dir = Path(os.environ["LOCALAPPDATA"]) / "ZebraTextureViewer" / "WebView2"
_webview2_data_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(_webview2_data_dir))

import cv2
import numpy as np
import requests
import webview

from blender_bake import run_blender_bake
from config import TOKEN_FILE

# ============================================================
# CONFIGURATION
# ============================================================
REPO_OWNER = "UniSotonLibrary"
REPO_NAME = "ColourableZebra3D"
BRANCH = "main"
SITE_URL = "https://unisotonlibrary.github.io/ColourableZebra3D/"
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

# ------------------------------------------------------------
# Overlay UI injected into the website: "+" button, name modal
# and a bottom-right toast log for status/errors.
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
    #zs-status-text { font-size: 16px; }
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
      <div id="zs-stage-name">
        <h2>Name Your Zebra</h2>
        <input id="zs-name-input" type="text" placeholder="Enter a name" />
        <div class="zs-btn-row">
          <button class="zs-btn zs-btn-secondary" onclick="ZebraScanner.cancel()">Cancel</button>
          <button class="zs-btn zs-btn-primary" onclick="ZebraScanner.submitName()">Start</button>
        </div>
      </div>
      <div id="zs-stage-status" style="display:none;">
        <p id="zs-status-text">Working...</p>
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
      document.getElementById('zs-stage-name').style.display = 'block';
      document.getElementById('zs-stage-status').style.display = 'none';
      document.getElementById('zs-overlay').style.display = 'flex';
      var input = document.getElementById('zs-name-input');
      input.value = '';
      input.focus();
    },
    showStatus: function (text) {
      document.getElementById('zs-stage-name').style.display = 'none';
      document.getElementById('zs-stage-status').style.display = 'block';
      document.getElementById('zs-status-text').innerText = text;
      document.getElementById('zs-overlay').style.display = 'flex';
    },
    hideOverlay: function () {
      document.getElementById('zs-overlay').style.display = 'none';
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


def _sanitize_name(name: str) -> str:
    clean_name = re.sub(r"[^a-zA-Z0-9_\-]", "", (name or "").strip())
    return clean_name if clean_name else "UnnamedZebra"


def load_github_token() -> str:
    if not TOKEN_FILE.exists():
        raise RuntimeError("GitHub token file not found. Cannot upload texture.")
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except Exception as e:
        raise RuntimeError(f"Failed to read GitHub token: {e}") from e
    if not token:
        raise RuntimeError("GitHub token file is empty. Cannot upload texture.")
    return token


def capture_photo_from_webcam(save_path: Path) -> bool:
    """Opens a live camera preview with an on-screen shutter button (like a phone camera)."""
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

    if not cap.isOpened():
        raise RuntimeError("Could not open webcam.")

    window_name = "Capture Coloring Sheet"
    state = {"captured": False, "cancelled": False}

    def on_mouse(event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        cx, cy, radius = param
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
            state["captured"] = True

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    # Force the preview above other windows (e.g. the webview) so it isn't hidden behind them.
    cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                raise RuntimeError("Failed to read from webcam.")

            h, w = frame.shape[:2]
            button_center = (w // 2, h - 80)
            button_radius = 40
            cv2.setMouseCallback(window_name, on_mouse, (button_center[0], button_center[1], button_radius))

            preview_frame = frame.copy()
            cv2.putText(preview_frame, "Tap the button to capture | ESC to cancel", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.circle(preview_frame, button_center, button_radius + 8, (255, 255, 255), 2)
            cv2.circle(preview_frame, button_center, button_radius, (255, 255, 255), -1)
            cv2.imshow(window_name, preview_frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                state["cancelled"] = True
                break
            if state["captured"]:
                save_path.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(save_path), frame)
                break
            # Window closed via its own [X] button - stop waiting instead of hanging forever.
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                state["cancelled"] = True
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return state["captured"]


def upload_texture_to_github(local_file_path: Path, remote_filename: str):
    token = load_github_token()

    remote_path = f"docs/assets/textures/{remote_filename}"
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{remote_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    with open(local_file_path, "rb") as f:
        encoded_content = base64.b64encode(f.read()).decode("utf-8")

    try:
        get_res = requests.get(url, headers=headers, timeout=10)
    except requests.exceptions.RequestException as e:
        raise RuntimeError("No internet connection, or GitHub could not be reached.") from e

    sha = get_res.json().get("sha") if get_res.status_code == 200 else None

    payload = {
        "message": f"Auto-upload texture {remote_filename}",
        "content": encoded_content,
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        response = requests.put(url, headers=headers, json=payload, timeout=30)
    except requests.exceptions.RequestException as e:
        raise RuntimeError("No internet connection, or GitHub could not be reached.") from e

    if response.status_code not in (200, 201):
        try:
            detail = response.json().get("message", "")
        except ValueError:
            detail = ""
        raise RuntimeError(f"Upload of '{remote_filename}' failed ({response.status_code}): {detail}")


def process_zebra_textures(zebra_name: str, captured_image_path: Path, on_status=None):
    """Warps the captured sheet, bakes the high-poly texture and uploads both. Raises on failure."""
    assets_textures_dir = PROJECT_ROOT / "docs" / "assets" / "textures"
    assets_textures_dir.mkdir(parents=True, exist_ok=True)

    output_lowpoly_jpg = assets_textures_dir / f"{zebra_name}.jpg"
    output_highpoly_png = assets_textures_dir / f"{zebra_name}_HighPoly.png"

    if on_status:
        on_status("Detecting tracking markers...")

    img = cv2.imread(str(captured_image_path))
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_ARUCO_ORIGINAL)
    aruco_params = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, aruco_params)

    corners, ids, _ = detector.detectMarkers(img)
    if ids is None:
        raise RuntimeError("No tracking markers were detected in the photo.")

    ids = ids.flatten()
    target_centers = {1: [50.0, 50.0], 2: [1997.0, 50.0], 3: [50.0, 1997.0], 4: [1997.0, 1997.0]}
    src_pts, dst_pts = [], []

    for marker_id in [1, 2, 3, 4]:
        if marker_id not in ids:
            raise RuntimeError("Not all tracking markers were found. Please try again.")
        idx = np.where(ids == marker_id)[0][0]
        mc = corners[idx][0]
        src_pts.append([float(np.mean(mc[:, 0])), float(np.mean(mc[:, 1]))])
        dst_pts.append(target_centers[marker_id])

    matrix, _ = cv2.findHomography(np.float32(src_pts), np.float32(dst_pts))
    texture_2k = cv2.warpPerspective(img, matrix, (2048, 2048), flags=cv2.INTER_LANCZOS4)
    cv2.imwrite(str(output_lowpoly_jpg), texture_2k, [cv2.IMWRITE_JPEG_QUALITY, 95])

    if on_status:
        on_status("Baking high-detail texture...")

    if not run_blender_bake(output_lowpoly_jpg, output_highpoly_png):
        raise RuntimeError("The high-poly texture bake failed.")

    if on_status:
        on_status("Uploading textures...")

    upload_texture_to_github(output_lowpoly_jpg, f"{zebra_name}.jpg")
    upload_texture_to_github(output_highpoly_png, f"{zebra_name}_HighPoly.png")


class Api:
    """Bridges the injected website overlay to the Python scanning workflow."""

    def __init__(self):
        self._name_queue = queue.Queue()

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
        threading.Thread(target=self._run_scan, daemon=True).start()
        return True

    def _run_scan(self):
        self._emit("ZebraScanner.showNamePrompt()")
        raw_name = self._name_queue.get()
        if raw_name is None:
            self._emit("ZebraScanner.hideOverlay()")
            return

        zebra_name = _sanitize_name(raw_name)

        try:
            self._emit("ZebraScanner.showStatus('Opening camera... check the camera window.')")
            captured_image_path = PROJECT_ROOT / "docs" / "assets" / "image" / "captured_coloring_sheet.jpg"
            if not capture_photo_from_webcam(captured_image_path):
                self._emit("ZebraScanner.hideOverlay()")
                return

            def on_status(text):
                escaped = text.replace("'", "\\'")
                self._emit(f"ZebraScanner.showStatus('{escaped}')")

            process_zebra_textures(zebra_name, captured_image_path, on_status)

            self._emit("ZebraScanner.hideOverlay()")
            self._emit(f"ZebraScanner.showToast('success', \"Zebra '{zebra_name}' uploaded!\")")
        except Exception as e:
            message = str(e).replace("'", "\\'").replace("\n", " ").replace('"', "'")
            self._emit("ZebraScanner.hideOverlay()")
            self._emit(f"ZebraScanner.showToast('error', \"{message}\")")


def on_loaded(window):
    try:
        window.evaluate_js(INJECT_JS)
    except Exception:
        traceback.print_exc()


def main():
    api = Api()
    window = webview.create_window("Zebra Texture Viewer", SITE_URL, js_api=api, width=1280, height=800)
    window.events.loaded += lambda: on_loaded(window)
    try:
        webview.start(gui="edgechromium")
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
