import os
import subprocess
from pathlib import Path

try:
    from .config import BLENDER_EXE, BLEND_FILE
except ImportError:
    from config import BLENDER_EXE, BLEND_FILE

def run_blender_bake(source_jpg_path: Path, output_png_path: Path) -> bool:
    """Headlessly bakes texture from BlockyZebra to AnimatedZebra in Blender 5.0."""
    temp_worker = source_jpg_path.parent / "_temp_bake_worker.py"
    
    blender_internal_code = f"""
import os
import bpy

SOURCE_TEXTURE_PATH = r"{str(source_jpg_path)}"
OUTPUT_TEXTURE_PATH = r"{str(output_png_path)}"

SOURCE_NAME = "BlockyZebra"
TARGET_NAME = "AnimatedZebra"

scene = bpy.context.scene
scene.render.engine = 'CYCLES'

source_obj = bpy.data.objects.get(SOURCE_NAME)
target_obj = bpy.data.objects.get(TARGET_NAME)

if not source_obj or not target_obj:
    raise Exception("Missing objects in blend file.")

if bpy.ops.object.mode_set.poll():
    bpy.ops.object.mode_set(mode='OBJECT')

bpy.ops.object.select_all(action='DESELECT')
source_obj.select_set(True)
target_obj.select_set(True)
bpy.context.view_layer.objects.active = target_obj

# Attach Source Texture
source_mat = source_obj.active_material
if not source_mat or not source_mat.node_tree:
    raise Exception(f"{{SOURCE_NAME}} lacks node material.")

source_img = bpy.data.images.load(SOURCE_TEXTURE_PATH, check_existing=True)
source_nodes = source_mat.node_tree.nodes
source_tex_node = next((n for n in source_nodes if n.type == 'TEX_IMAGE'), None)
if not source_tex_node:
    source_tex_node = source_nodes.new(type='ShaderNodeTexImage')
source_tex_node.image = source_img

principled = next((n for n in source_nodes if n.type == 'BSDF_PRINCIPLED'), None)
if principled:
    source_mat.node_tree.links.new(source_tex_node.outputs['Color'], principled.inputs['Base Color'])

# Target Setup
target_mat = target_obj.active_material
if not target_mat or not target_mat.node_tree:
    target_mat = bpy.data.materials.new(name="AnimatedZebra_Baked_Mat")
    target_obj.data.materials.append(target_mat)

target_nodes = target_mat.node_tree.nodes
baked_image = bpy.data.images.new(name="animatedtexture", width=1024, height=1024, alpha=False)

target_tex_node = next((n for n in target_nodes if n.type == 'TEX_IMAGE'), None)
if not target_tex_node:
    target_tex_node = target_nodes.new(type='ShaderNodeTexImage')

target_tex_node.image = baked_image
target_nodes.active = target_tex_node

# Bake Config
bake_settings = scene.render.bake
bake_settings.use_selected_to_active = True
bake_settings.use_cage = False
bake_settings.cage_extrusion = 0.04
bake_settings.max_ray_distance = 0.08
bake_settings.use_clear = True
bake_settings.margin = 16

bake_settings.pass_filter.clear()
bake_settings.pass_filter.add('COLOR')

print("[Blender 5.0] Running Texture Bake...")
# In true background mode (-b) there is no window/view3d, so the bake operator's
# own context lookup reports "No valid selected objects" - override it explicitly.
bake_context = bpy.context.copy()
bake_context["selected_objects"] = [source_obj, target_obj]
bake_context["selected_editable_objects"] = [source_obj, target_obj]
bake_context["active_object"] = target_obj
bake_context["object"] = target_obj
with bpy.context.temp_override(**bake_context):
    bpy.ops.object.bake(type='DIFFUSE', save_mode='INTERNAL')

baked_image.filepath_raw = OUTPUT_TEXTURE_PATH
baked_image.file_format = 'PNG'
baked_image.save()
print(f"[Blender 5.0] Saved baked map: {{OUTPUT_TEXTURE_PATH}}")
"""
    try:
        with open(temp_worker, "w", encoding="utf-8") as f:
            f.write(blender_internal_code)

        cmd = [str(BLENDER_EXE), "-b", str(BLEND_FILE), "--python-exit-code", "1", "-P", str(temp_worker)]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in process.stdout:
            print(line, end="")
        process.wait()
        # Blender can exit 0 without --python-exit-code even after a script error, so also verify the file exists.
        return process.returncode == 0 and output_png_path.exists()
    finally:
        if temp_worker.exists():
            temp_worker.unlink()

if __name__ == "__main__":
    test_src = Path(r"C:\Users\jm1d22\OneDrive - University of Southampton\Documents\Work Documents\Projects\Hands On Humanities\Zebra3D\ColourableZebra3D\docs\assets\textures\Texture_0001.jpg")
    test_out = test_src.parent / "Texture_0001_HighPoly.png"
    run_blender_bake(test_src, test_out)