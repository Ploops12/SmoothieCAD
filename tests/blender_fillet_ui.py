"""Real-window CAD edge picking, fillet dialogs, radius undo and history repair."""

import argparse
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import traceback
import zipfile

import bpy
import numpy as np
from bpy_extras import view3d_utils
from mathutils import Euler, Vector

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
parser.add_argument("--output-dir", required=True, type=Path)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
options.output_dir.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory()
with zipfile.ZipFile(options.addon_zip) as archive:
    archive.extractall(temporary.name)
sys.path.insert(0, temporary.name)
addon = importlib.import_module("blendercad")
from blendercad import highlight, presentation as p

n = addon._native
bpy.context.preferences.view.show_splash = False
LOSS = 1 - math.pi / 4
BASE_VOLUME = 30 * 20 * 10
step, area, region = 0, None, None
body_id, fillet_id, original_target = None, None, None
baseline, radius_one, radius_two = None, None, None
cursor = (500, 450)
overlay_before = 0


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8), (actual, expected)


def host():
    scene = bpy.context.scene
    return next(item for item in p.hosts(scene, p.read_document(scene).id) if item[p.BODY] == body_id)


def volume(radius=None):
    scene = bpy.context.scene
    actual = p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body_id)
    close(actual, BASE_VOLUME if radius is None else BASE_VOLUME - LOSS * radius ** 2 * 10)
    return actual


def fingerprint():
    scene = bpy.context.scene
    owner = host()
    return (scene.blendercad_document_snapshot, id(p.runtime(scene).evaluator),
            p.current_packet(scene, owner).token, p.display_for(scene, owner).data.as_pointer())


def event(kind, **values):
    global cursor
    if "x" in values:
        cursor = values["x"], values["y"]
        bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])
    else:
        values["x"], values["y"] = cursor
    bpy.context.window.event_simulate(type=kind, value="PRESS", **values)
    values.pop("unicode", None)  # Blender permits Unicode only on PRESS events.
    bpy.context.window.event_simulate(type=kind, value="RELEASE", **values)


def focus_radius_field():
    # Factory-startup UI at the runner's fixed 1280x900/DPI: the first
    # property row is immediately below the popup's invocation point. A real
    # double click enters numeric text mode; Tab does not focus it on this UI.
    x, y = cursor[0] - 60, cursor[1] - 8
    event("LEFTMOUSE", x=x, y=y)
    event("LEFTMOUSE", x=x, y=y)


def click_point(point):
    location = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(point))
    assert location is not None, "Fillet edge is outside the viewport"
    event("LEFTMOUSE", x=int(region.x + location.x), y=int(region.y + location.y))


def view_body():
    view = area.spaces.active.region_3d
    # From +X/-Y/+Z: the (30,0) vertical corner and opposite front corner
    # are visible, and their midpoints are well away from vertex pick radii.
    view.view_rotation = Euler((1.05, 0, 0.6)).to_quaternion()
    view.view_location = Vector((0.015, 0.01, 0.005))
    view.view_distance, view.view_perspective = 0.09, "ORTHO"
    area.spaces.active.clip_start = 0.0001
    area.spaces.active.shading.color_type = "OBJECT"
    area.tag_redraw()


def invoke(operator, **values):
    with bpy.context.temp_override(area=area, region=region):
        assert operator("INVOKE_DEFAULT", **values) == {"RUNNING_MODAL"}


def execute(operator, **values):
    with bpy.context.temp_override(area=area, region=region):
        assert operator(**values) == {"FINISHED"}


def selected_target():
    scene = bpy.context.scene
    owner = host()
    assert bpy.context.active_object == owner and owner.type == "EMPTY"
    pick = p.runtime(scene).last_pick
    assert pick is not None and pick.kind == "EDGE", "Real edge click did not select a CAD edge"
    close(p.current_measure(scene, owner).length_mm, 10)
    return p.persistent_target(scene, owner, pick)


def capture_viewport():
    """Read the completed window GPU framebuffer, including the CAD sidebar.

    A viewport POST_PIXEL callback precedes final workbench compositing on this
    Blender build and can capture a black solid silhouette. Reading after the
    synchronous redraw captures the shaded result without a screen screenshot.
    """
    import gpu
    window = bpy.context.window
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, window.width, window.height, 4, 0, "FLOAT")
    values = np.array(pixels, dtype=np.float32).reshape((window.height, window.width, 4))
    assert np.ptp(values[:, :, :3]) > 0.1, "Fillet framebuffer was empty"
    location = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector((0.015, 0.01, 0.01)))
    assert location is not None
    x, y = int(region.x + location.x), int(region.y + location.y)
    assert values[y, x, :3].max() > 0.05, ("Shaded Solid captured black", tuple(p.display_for(bpy.context.scene, host()).color))
    image = bpy.data.images.new("Fillet history verification", width=window.width, height=window.height, alpha=True)
    try:
        image.pixels.foreach_set(values.reshape(-1))
        image.filepath_raw = str(options.output_dir / "fillet-history.png")
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def write_result(passed, **values):
    report = {"passed": passed, "status": "passed" if passed else "failed", **values}
    for filename in ("result.json", "summary.json"):
        (options.output_dir / filename).write_text(json.dumps(report, indent=2))



def tick():
    global step, area, region, body_id, fillet_id, original_target, baseline, radius_one, radius_two, overlay_before
    try:
        scene = bpy.context.scene
        if step == 0:
            addon.register()
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)
            bpy.ops.blendercad.new_document()
            area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
            region = next(item for item in area.regions if item.type == "WINDOW")
            execute(bpy.ops.blendercad.add_rectangle_solid, width_mm=30, height_mm=20, depth_mm=10)
            body_id = bpy.context.active_object[p.BODY]
            volume()
            area.spaces.active.show_region_ui = True
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            sidebar = next(item for item in area.regions if item.type == "UI")
            sidebar.active_panel_category = "BlenderCAD"
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            assert sidebar.width > 0 and sidebar.active_panel_category == "BlenderCAD", "CAD sidebar did not open"
            region = next(item for item in area.regions if item.type == "WINDOW")
            view_body()
            scene.blendercad_selection_mode = "EDGE"
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = None
            bpy.context.preferences.edit.use_global_undo = True
            bpy.ops.ed.undo_push(message="Fillet UI baseline")
        elif step == 1:
            overlay_before = highlight.draw_count
            click_point((0.03, 0, 0.005))
        elif step == 2:
            original_target = selected_target()
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            assert highlight.draw_count > overlay_before and not highlight.last_error, highlight.last_error
            baseline = fingerprint()
            invoke(bpy.ops.blendercad.fillet, radius_mm=1, parameter="")
        elif step == 3:
            event("ESC")
        elif step == 4:
            assert fingerprint() == baseline, "Cancelling fillet creation changed intent/cache/display"
            assert len(p.read_document(scene).features) == 1
            invoke(bpy.ops.blendercad.fillet, radius_mm=1, parameter="")
        elif step == 5:
            event("RET")
        elif step == 6:
            document = p.read_document(scene)
            assert len(document.features) == 2 and document.features[-1].type == "fillet"
            fillet_id = document.features[-1].id
            assert document.get_feature(fillet_id).radius == 1
            assert document.get_feature(fillet_id).edges[0].anchor.key == original_target.anchor.key
            volume(1)
            radius_one = scene.blendercad_document_snapshot
            baseline = fingerprint()
            invoke(bpy.ops.blendercad.set_fillet_radius, feature_id=fillet_id)
        elif step == 7:
            focus_radius_field()
        elif step == 8:
            event("A", ctrl=True)
            event("TWO", unicode="2")
        elif step == 9:
            event("ESC")  # Cancel numeric text entry, then its enclosing dialog.
        elif step == 10:
            event("ESC")
        elif step == 11:
            assert fingerprint() == baseline, "Cancelling radius edit changed intent/cache/display"
            volume(1)
            invoke(bpy.ops.blendercad.set_fillet_radius, feature_id=fillet_id)
        elif step == 12:
            focus_radius_field()
        elif step == 13:
            event("A", ctrl=True)
            event("TWO", unicode="2")
        elif step == 14:
            event("RET")  # Commit widget input; the next Enter accepts the dialog.
        elif step == 15:
            event("RET")
        elif step == 16:
            assert p.read_document(scene).get_feature(fillet_id).radius == 2, (
                "Radius dialog did not commit the typed value", p.read_document(scene).get_feature(fillet_id).radius)
            volume(2)
            radius_two = scene.blendercad_document_snapshot
            event("Z", ctrl=True)
        elif step == 17:
            assert scene.blendercad_document_snapshot == radius_one, "One Ctrl-Z did not undo the radius interaction"
            volume(1)
            event("Z", ctrl=True, shift=True)
        elif step == 18:
            assert scene.blendercad_document_snapshot == radius_two, "Ctrl-Shift-Z did not redo the radius interaction"
            volume(2)
            execute(bpy.ops.blendercad.rollback, feature_id=fillet_id)
        elif step == 19:
            document = p.read_document(scene)
            assert document.rollback_before == fillet_id and len(document.features) == 2
            assert p.runtime(scene).report.features[-1].state == n.EvaluationState.ROLLED_BACK
            volume()
            view_body()
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = None
        elif step == 20:
            click_point((0, 0, 0.005))
        elif step == 21:
            replacement = selected_target()
            assert replacement.anchor.key != original_target.anchor.key, "Repair did not select a different input edge"
            baseline = replacement.anchor.key
            execute(bpy.ops.blendercad.repair_fillet_edges, feature_id=fillet_id)
        elif step == 22:
            document = p.read_document(scene)
            assert document.rollback_before is None and document.get_feature(fillet_id).edges[0].anchor.key == baseline
            assert p.runtime(scene).report.features[-1].state == n.EvaluationState.UP_TO_DATE
            volume(2)
            baseline = fingerprint()
            try:
                with bpy.context.temp_override(area=area, region=region):
                    result = bpy.ops.blendercad.move_feature(feature_id=fillet_id, offset=-1)
            except RuntimeError:
                pass  # Blender may raise when the operator reports ERROR.
            else:
                assert result == {"CANCELLED"}, result
            assert fingerprint() == baseline, "Invalid history reorder changed the model/cache/display"
        elif step == 23:
            event("ESC")  # Dismiss a possible operator error report popup.
        elif step == 24:
            execute(bpy.ops.blendercad.suppress_feature, feature_id=fillet_id, suppressed=True)
        elif step == 25:
            assert p.runtime(scene).report.features[-1].state == n.EvaluationState.SUPPRESSED
            assert not p.display_for(scene, host())["blendercad_current"]
            try:
                p.current_packet(scene, host())
            except ValueError:
                pass
            else:
                raise AssertionError("Suppressed fillet exposed current topology")
            execute(bpy.ops.blendercad.suppress_feature, feature_id=fillet_id, suppressed=False)
        elif step == 26:
            volume(2)
            assert p.runtime(scene).report.features[-1].state == n.EvaluationState.UP_TO_DATE
            p.current_packet(scene, host())
            execute(bpy.ops.blendercad.rollback, feature_id=fillet_id)
        elif step == 27:
            volume()
            assert p.read_document(scene).rollback_before == fillet_id
            execute(bpy.ops.blendercad.rollback, feature_id="")
        elif step == 28:
            volume(2)
            assert p.read_document(scene).rollback_before is None
            view_body()
        elif step == 29:
            capture_viewport()
            assert not highlight.last_error, highlight.last_error
        elif step == 30:
            volume(2)
            write_result(True, summary="Real-window fillet edge picking, dialog cancellation/confirmation, radius undo/redo and history repair passed",
                screenshot="fillet-history.png", dimensions_mm=[30, 20, 10], radius_mm=2,
                volume_mm3=volume(2), edge_clicks=2, fillet_dialog=True, radius_dialog=True,
                one_step_radius_undo=True, rollback_repair_resume=True, rejected_reorder=True,
                suppression_recovery=True, sidebar_category="BlenderCAD", overlay_draw_count=highlight.draw_count,
                display_color=list(p.display_for(scene, host()).color), shading=area.spaces.active.shading.color_type)
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 0.4
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        write_result(False, step=step, error=error)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=4)
