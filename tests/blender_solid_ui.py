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
from mathutils import Euler, Quaternion, Vector

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
from blendercad import capabilities, highlight, presentation as p

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
overlay_before = 0
visibility_body = None
visibility_token = None


def click_point(point):
    location = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(point))
    assert location is not None
    event("LEFTMOUSE", x=int(region.x + location.x), y=int(region.y + location.y))


def verify_overlay():
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    assert not highlight.last_error, highlight.last_error
    assert highlight.draw_count > overlay_before, "Selected CAD topology did not reach the GPU overlay handler"


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
    global step, baseline, host_id, area, region, overlay_before, visibility_body, visibility_token
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
            overlay_before = highlight.draw_count
            click_point((0.015, 0.01, 0.005))
        elif step == 2:
            assert bpy.context.active_object is not None, "Click did not activate the Solid host"
            assert bpy.context.active_object.get(p.BODY) == host_id and bpy.context.active_object.type == "EMPTY"
            assert p.runtime(scene).last_pick is not None, "Body click did not retain the CAD face pick"
            assert abs(p.current_measure(scene, bpy.context.active_object).area_mm2 - 600) < 1e-6
            verify_overlay()
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.blendercad.save_reference(name="UI face reference") == {"FINISHED"}
            assert len(p.read_document(scene).references) == 1
            assert p.runtime(scene).report.references[0].resolution.state == addon._native.ReferenceState.RESOLVED
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
            scene.blendercad_selection_mode = "EDGE"
            overlay_before = highlight.draw_count
            click_point((0.015, 0, 0.005))
        elif step == 15:
            assert p.runtime(scene).last_pick.kind == "EDGE", "Edge-mode click did not select a CAD edge"
            assert abs(p.current_measure(scene, bpy.context.active_object).length_mm - 30) < 1e-6
            verify_overlay()
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.blendercad.save_reference(name="UI edge reference") == {"FINISHED"}
            assert p.read_document(scene).references[-1].target.kind == addon._native.TopologyKind.EDGE
            scene.blendercad_selection_mode = "VERTEX"
            overlay_before = highlight.draw_count
            click_point((0, 0, 0.005))
        elif step == 16:
            assert p.runtime(scene).last_pick.kind == "VERTEX", "Vertex-mode click did not select a CAD vertex"
            point = p.current_measure(scene, bpy.context.active_object).center_mm
            assert abs(point.x) < 1e-6 and abs(point.y) < 1e-6 and abs(point.z - 5) < 1e-6
            verify_overlay()
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.blendercad.save_reference(name="UI vertex reference") == {"FINISHED"}
            assert p.read_document(scene).references[-1].target.kind == addon._native.TopologyKind.VERTEX
            view = area.spaces.active.region_3d
            view.view_perspective = "PERSP"
            view.view_rotation = Euler((0.5, 0.3, 0)).to_quaternion()
            scene.blendercad_selection_mode = "EDGE"
        elif step == 17:
            overlay_before = highlight.draw_count
            click_point((0.015, 0, 0.005))
        elif step == 18:
            pick = p.runtime(scene).last_pick
            assert pick is not None and pick.kind == "EDGE"
            assert abs(pick.point_mm[1]) < 1e-5 and abs(pick.point_mm[2] - 5) < 1e-5
            assert abs(p.current_measure(scene, bpy.context.active_object).length_mm - 30) < 1e-6
            verify_overlay()
            host = bpy.context.active_object
            visibility_body = host[p.BODY]
            visibility_token = p.current_packet(scene, host).token
            baseline = scene.blendercad_document_snapshot
            event("H")
        elif step == 19:
            host = next(host for host in p.hosts(scene, p.read_document(scene).id) if host[p.BODY] == visibility_body)
            assert host.hide_get() and p.display_for(scene, host).hide_get(), "H did not hide the complete CAD Solid"
            assert p.current_packet(scene, host).token == visibility_token
            assert scene.blendercad_document_snapshot == baseline
            event("H", alt=True)
        elif step == 20:
            host = next(host for host in p.hosts(scene, p.read_document(scene).id) if host[p.BODY] == visibility_body)
            assert not host.hide_get() and not p.display_for(scene, host).hide_get(), "Alt-H did not restore the complete CAD Solid"
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            host.select_set(True)
            bpy.context.view_layer.objects.active = host
            event("NUMPAD_SLASH")
        elif step == 21:
            from blendercad import selection
            host = bpy.context.active_object
            space = area.spaces.active
            assert space.local_view is not None and host.local_view_get(space)
            assert p.display_for(scene, host).local_view_get(space), "Local view omitted the CAD display"
            with bpy.context.temp_override(area=area, region=region):
                visible = [owner[p.BODY] for owner, _packet, _inverse in selection._bodies(scene)]
                assert visible == [visibility_body], "CAD picking included objects outside local view"
            assert p.current_packet(scene, host).token == visibility_token
            assert scene.blendercad_document_snapshot == baseline
            event("NUMPAD_SLASH")
        elif step == 22:
            assert area.spaces.active.local_view is None
            assert scene.blendercad_document_snapshot == baseline
            (options.output_dir / "result.json").write_text(json.dumps({"passed": True, "capability_calls": capability_calls,
                "cad_selection_kinds": ["FACE", "EDGE", "VERTEX"], "overlay_draw_count": highlight.draw_count,
                "visibility_events": ["H", "ALT_H", "LOCAL_VIEW"]}))
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
