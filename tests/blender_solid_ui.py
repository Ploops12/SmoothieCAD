"""Physical Blender event-path smoke test, launched by CMake in Xvfb."""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import traceback
import zipfile

import bpy
from bpy_extras import view3d_utils
from mathutils import Quaternion, Vector

bpy.context.preferences.view.show_splash = False

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
parser.add_argument("--output-dir", required=True, type=Path)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
temporary = tempfile.TemporaryDirectory()
with zipfile.ZipFile(options.addon_zip) as archive:
    archive.extractall(temporary.name)
sys.path.insert(0, temporary.name)
addon = importlib.import_module("blendercad")
from blendercad import capabilities, presentation as p

capability_calls = []
original_require = capabilities.require_capability


def traced_require(objects, operation):
    capability_calls.append(operation)
    return original_require(objects, operation)


capabilities.require_capability = traced_require

step = 0
baseline = None
host_id = None
area = None
region = None
cursor = (500, 450)


def event(kind, **values):
    global cursor
    if "x" in values:
        cursor = values["x"], values["y"]
        bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])
    else:
        values["x"], values["y"] = cursor
    bpy.context.window.event_simulate(type=kind, value="PRESS", **values)
    bpy.context.window.event_simulate(type=kind, value="RELEASE", **values)


def tick():
    global step, baseline, host_id, area, region
    try:
        scene = bpy.context.scene
        if step == 0:
            addon.register()
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)
            bpy.ops.blendercad.new_document()
            area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
            region = next(region for region in area.regions if region.type == "WINDOW")
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5)
            host_id = bpy.context.active_object[p.BODY]
            view = area.spaces.active.region_3d
            assert view.view_distance < 0.2, "Numeric creation did not frame the millimetre-scale body"
            view.view_rotation = Quaternion((1, 0, 0, 0))
            view.view_location = Vector((0.015, 0.01, 0))
            view.view_distance = 0.08
            view.view_perspective = "ORTHO"
            area.spaces.active.shading.color_type = "OBJECT"
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = None
            bpy.ops.ed.undo_push(message="Initial CAD Solid")
        elif step == 1:
            location = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector((0.015, 0.01, 0.005)))
            event("LEFTMOUSE", x=int(region.x + location.x), y=int(region.y + location.y))
        elif step == 2:
            assert bpy.context.active_object is not None, "Click did not activate the Solid host"
            assert bpy.context.active_object.get(p.BODY) == host_id and bpy.context.active_object.type == "EMPTY"
            baseline = scene.blendercad_document_snapshot
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.blendercad.add_rectangle_solid("INVOKE_DEFAULT")
        elif step == 3:
            event("ESC")
        elif step == 4:
            assert scene.blendercad_document_snapshot == baseline, "Cancelling a numeric creation dialog changed the model"
            event("D", shift=True)
        elif step == 5:
            document = p.read_document(scene)
            assert len(document.features) == 2, "Shift-D did not dispatch the CAD definition duplicate"
            assert len({host[p.BODY] for host in p.hosts(scene, document.id)}) == 2
            assert bpy.context.active_object[p.BODY] != host_id
            baseline = scene.blendercad_document_snapshot
            event("X")
        elif step == 6:
            event("ESC")
        elif step == 7:
            assert scene.blendercad_document_snapshot == baseline, "Cancelling deletion changed the model"
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.blendercad.convert_to_regular_mesh("INVOKE_DEFAULT")
        elif step == 8:
            event("RET")
        elif step == 9:
            assert bpy.context.active_object.type == "MESH" and p.host_for(bpy.context.active_object) is None
            assert len(p.read_document(scene).features) == 1
            event("Z", ctrl=True)
        elif step == 10:
            assert scene.blendercad_document_snapshot == baseline, "UI conversion undo did not restore design intent"
            assert bpy.context.active_object.type == "EMPTY" and p.host_for(bpy.context.active_object) is not None
            document = p.read_document(scene)
            assert len(p.hosts(scene, document.id)) == 2
            p.current_packet(scene, bpy.context.active_object)
            event("S")
        elif step == 11:
            assert "scale" in capability_calls, "S did not reach the capability guard"
            assert tuple(bpy.context.active_object.scale) == (1, 1, 1)
            assert scene.blendercad_document_snapshot == baseline
            event("ESC")  # Dismiss Blender's error report popup.
        elif step == 12:
            event("TAB")
        elif step == 13:
            assert "edit_mode" in capability_calls, "Tab did not reach the capability guard"
            assert bpy.context.mode == "OBJECT" and scene.blendercad_document_snapshot == baseline
            event("ESC")
        elif step == 14:
            (options.output_dir / "result.json").write_text(json.dumps({"passed": True, "capability_calls": capability_calls}))
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 0.5
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        (options.output_dir / "result.json").write_text(json.dumps({"passed": False, "step": step, "error": error}))
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=4)
