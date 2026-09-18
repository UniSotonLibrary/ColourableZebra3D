"""Standalone tool: re-bakes every existing low-poly .jpg in docs/assets/textures
into a fresh _HighPoly.png (now that the headless Blender bake is fixed) and
uploads the results to GitHub. Run independently of the GUI:

    python manual_override.py
"""
from pathlib import Path

from blender_bake import run_blender_bake
from image_scanner import PROJECT_ROOT, upload_texture_to_github

TEXTURES_DIR = PROJECT_ROOT / "docs" / "assets" / "textures"


def regenerate_all_highpoly_textures():
    jpg_files = sorted(TEXTURES_DIR.glob("*.jpg"))
    if not jpg_files:
        print(f"[INFO] No .jpg files found in {TEXTURES_DIR}")
        return

    succeeded, failed = [], []

    for jpg_path in jpg_files:
        zebra_name = jpg_path.stem
        highpoly_path = TEXTURES_DIR / f"{zebra_name}_HighPoly.png"

        print(f"\n[INFO] Re-baking '{zebra_name}'...")
        try:
            if not run_blender_bake(jpg_path, highpoly_path):
                raise RuntimeError("Blender bake failed.")

            upload_texture_to_github(highpoly_path, f"{zebra_name}_HighPoly.png")
            succeeded.append(zebra_name)
        except Exception as e:
            print(f"[ERROR] '{zebra_name}' failed: {e}")
            failed.append(zebra_name)

    print("\n" + "=" * 60)
    print(f"[SUMMARY] {len(succeeded)} succeeded, {len(failed)} failed out of {len(jpg_files)}")
    if failed:
        print(f"[SUMMARY] Failed: {', '.join(failed)}")


if __name__ == "__main__":
    regenerate_all_highpoly_textures()
