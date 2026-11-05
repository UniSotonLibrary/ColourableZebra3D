import base64
import re
from pathlib import Path

import cv2
import numpy as np
import requests

from blender_bake import run_blender_bake
from config import TOKEN_FILE

# ============================================================
# CONFIGURATION
# ============================================================
REPO_OWNER = "UniSotonLibrary"
REPO_NAME = "ColourableZebra3D"
BRANCH = "main"
PREFERRED_USB_CAMERA_INDEX = 1
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def _sanitize_name(name: str) -> str:
    clean_name = re.sub(r"[^a-zA-Z0-9 _\-]", "", (name or "").strip())
    clean_name = re.sub(r"\s+", " ", clean_name)
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


def _available_camera_indices(max_devices: int = 5) -> list[int]:
    available_indices = []
    for camera_index in range(max_devices):
        camera = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if camera.isOpened():
            available_indices.append(camera_index)
        camera.release()
    return available_indices


def capture_photo_from_webcam(save_path: Path) -> bool:
    """Opens a live camera preview with an on-screen shutter button (like a phone camera)."""
    available_cameras = _available_camera_indices()
    if not available_cameras:
        raise RuntimeError("Could not open a webcam.")

    selected_camera = (
        PREFERRED_USB_CAMERA_INDEX
        if PREFERRED_USB_CAMERA_INDEX in available_cameras
        else available_cameras[0]
    )
    cap = cv2.VideoCapture(selected_camera, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

    if not cap.isOpened():
        raise RuntimeError("Could not open the selected webcam.")

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
            cv2.putText(preview_frame, "Tap to capture | C: change camera | ESC: cancel", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.circle(preview_frame, button_center, button_radius + 8, (255, 255, 255), 2)
            cv2.circle(preview_frame, button_center, button_radius, (255, 255, 255), -1)
            cv2.imshow(window_name, preview_frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                state["cancelled"] = True
                break
            if key in (ord("c"), ord("C")) and len(available_cameras) > 1:
                current_position = available_cameras.index(selected_camera)
                selected_camera = available_cameras[(current_position + 1) % len(available_cameras)]
                cap.release()
                cap = cv2.VideoCapture(selected_camera, cv2.CAP_DSHOW)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                if not cap.isOpened():
                    raise RuntimeError(f"Could not switch to webcam {selected_camera}.")
                continue
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
    print(f"[INFO] Uploading '{remote_filename}' to GitHub...")
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

    print(f"[INFO] Uploaded '{remote_filename}' successfully.")


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

    print("[INFO] Starting Blender bake...")
    if not run_blender_bake(output_lowpoly_jpg, output_highpoly_png):
        raise RuntimeError("The high-poly texture bake failed.")
    print("[INFO] Blender bake finished.")

    if on_status:
        on_status("Uploading textures...")

    upload_texture_to_github(output_lowpoly_jpg, f"{zebra_name}.jpg")
    upload_texture_to_github(output_highpoly_png, f"{zebra_name}_HighPoly.png")


if __name__ == "__main__":
    from gui_app import main

    main()


