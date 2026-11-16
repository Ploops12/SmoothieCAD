"""Real-window planar face attachment, moving support and explicit repair."""

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
from blendercad import commands, feature_editor, presentation as p, sketch_editor as editor, sketch_view

n = addon._native
bpy.context.preferences.view.show_splash = False
area, region = None, None
cursor = (640, 450)
state, actions = {}, []
step = 0


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8), (actual, expected)


def document():
    return p.read_document(bpy.context.scene)


def owner():
    return next(host for host in p.hosts(bpy.context.scene, document().id) if host[p.BODY] == state["body"])


def support():
    return next(item.support for item in document().sketches if item.id == state["sketch"])


def support_intent():
    snapshot = document().to_snapshot()
    return next(item["support"] for item in snapshot["sketches"] if item["id"] == state["sketch"])


def execute(operator, **arguments):
    with bpy.context.temp_override(area=area, region=region):
        assert operator(**arguments) == {"FINISHED"}


def event(kind, **values):
    global cursor
    if "x" in values:
        cursor = int(values["x"]), int(values["y"])
        bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])
    else:
        values["x"], values["y"] = cursor
    bpy.context.window.event_simulate(type=kind, value="PRESS", **values)
    values.pop("unicode", None)
    bpy.context.window.event_simulate(type=kind, value="RELEASE", **values)


def text(value):
    digits = ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")
    punctuation = {"-": "MINUS", ".": "PERIOD", " ": "SPACE"}
    for character in value:
        kind = digits[int(character)] if character.isdigit() else punctuation.get(character, character.upper())
        event(kind, unicode=character)


def project(point):
    value = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(point))
    assert value is not None, "Requested CAD point is outside the view"
    return int(region.x + value.x), int(region.y + value.y)


def world_mm(point):
    return owner().matrix_world @ (Vector(point) * p.unit_scale(bpy.context.scene))


def click_uv(uv, shift=False):
    active = editor.active
    assert active is not None
    point = sketch_view.world_point(active.session.display_problem.plane, uv, active.placement, active.scale)
    x, y = project(point)
    event("LEFTMOUSE", x=x, y=y, shift=shift)


def view_body(sidebar=False, oblique=False):
    global region
    area.spaces.active.show_region_ui = sidebar
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    region = next(item for item in area.regions if item.type == "WINDOW")
    if sidebar:
        next(item for item in area.regions if item.type == "UI").active_panel_category = "BlenderCAD"
    view = area.spaces.active.region_3d
    view.view_rotation = Euler((0.8, 0, 0.5) if oblique else (0, 0, 0)).to_quaternion()
    view.view_location = world_mm((15, 10, document().length_value("thickness") / 2))
    view.view_distance, view.view_perspective = 0.09, "ORTHO"
    area.spaces.active.clip_start, area.spaces.active.clip_end = 0.0001, 1
    area.spaces.active.shading.color_type = "OBJECT"
    area.tag_redraw()


def snapshot_unchanged():
    assert bpy.context.scene.blendercad_document_snapshot == state["before_editor"]
    assert len(bpy.data.objects) == state["before_objects"]
    assert len(bpy.data.meshes) == state["before_meshes"]


def check_geometry(thickness):
    current = document()
    evaluator = p.runtime(bpy.context.scene).evaluator
    assert all(item.state == n.EvaluationState.UP_TO_DATE for item in p.runtime(bpy.context.scene).report.features)
    assert current.geometric_tolerance_mm == 0.001
    close(evaluator.volume_mm3(current, state["body"]), 30 * 20 * thickness - math.pi * 4 * 3)
    plane = evaluator.evaluated_sketch(current, state["sketch"]).plane
    close(plane.origin.z, thickness)
    normal = Vector((plane.x_axis.x, plane.x_axis.y, plane.x_axis.z)).cross(
        Vector((plane.y_axis.x, plane.y_axis.y, plane.y_axis.z)))
    assert normal.z < -0.999999
    hit = evaluator.ray_cast(current, state["body"], n.Vector3(15, 10, 30), n.Vector3(0, 0, -1), 40)
    assert hit is not None, "Blind pocket unexpectedly cut through the stock"
    close(hit.point_mm.z, thickness - 3)
    neighboring = evaluator.ray_cast(current, state["body"], n.Vector3(19, 10, 30), n.Vector3(0, 0, -1), 40)
    assert neighboring is not None
    close(neighboring.point_mm.z, thickness)
    assert [item.id for item in current.features] == state["features"]


def setup():
    global area, region
    addon.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    assert bpy.ops.blendercad.new_document() == {"FINISHED"}
    area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
    region = next(item for item in area.regions if item.type == "WINDOW")
    execute(bpy.ops.blendercad.add_rectangle_solid, width_mm=30, height_mm=20, depth_mm=10)
    state["body"] = bpy.context.active_object[p.BODY]
    state["base"] = document().features[0].id
    execute(bpy.ops.blendercad.parameter, name="thickness", value=10, unit="mm", create=True)
    execute(bpy.ops.blendercad.set_feature_depth, feature_id=state["base"], parameter="thickness")
    bpy.context.scene.blendercad_selection_mode = "FACE"
    bpy.context.preferences.edit.use_global_undo = True
    view_body()


def actual_face_click(thickness):
    view_body()
    # Away from the later pocket center, but on the same analytic top face.
    x, y = project(world_mm((8, 6, thickness)))
    event("LEFTMOUSE", x=x, y=y)


def face_clicked():
    current = document()
    pick = p.runtime(bpy.context.scene).last_pick
    assert pick is not None and pick.kind == "FACE", "Actual viewport click did not select a CAD face"
    plane = p.runtime(bpy.context.scene).evaluator.planar_face(current, state["body"], pick.index)
    assert plane is not None
    close(plane.origin.z, current.length_value("thickness"))


def invoke_face_sketch():
    face_clicked()
    state["before_editor"] = bpy.context.scene.blendercad_document_snapshot
    state["before_objects"], state["before_meshes"] = len(bpy.data.objects), len(bpy.data.meshes)
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.edit_sketch("INVOKE_DEFAULT", plane="FACE", reverse_support=True,
            name="Face attached blind pocket") == {"RUNNING_MODAL"}
    active = editor.active
    assert active is not None and not active.session.error, None if active is None else active.session.error
    state["editor_resources"] = vars(active)
    definition = next(item for item in active.session.document.sketches if item.id == active.session.sketch_id)
    assert definition.support is not None and definition.support.feature_id == state["base"]
    assert definition.support.reversed
    plane = active.session.display_problem.plane
    close(plane.origin.z, document().length_value("thickness"))
    origin = Vector((plane.origin.x, plane.origin.y, plane.origin.z))
    x_axis = Vector((plane.x_axis.x, plane.x_axis.y, plane.x_axis.z))
    y_axis = Vector((plane.y_axis.x, plane.y_axis.y, plane.y_axis.z))
    relative = Vector((15, 10, document().length_value("thickness"))) - origin
    state["center_uv"] = relative.dot(x_axis), relative.dot(y_axis)
    view = area.spaces.active.region_3d
    view.view_location = world_mm((12, 8, document().length_value("thickness")))
    view.view_distance = 0.09
    area.tag_redraw()


def cancelled_new_sketch():
    assert editor.active is None
    assert state["editor_resources"]["session"].evaluator is None
    snapshot_unchanged()
    assert len(document().sketches) == 1, "Cancelled face attachment left a saved sketch or dependency"


def check_sketch_draw():
    active = editor.active
    assert active is not None
    before = sketch_view.draw_count
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    assert sketch_view.draw_count > before and not sketch_view.last_error, sketch_view.last_error
    snapshot_unchanged()


def dimension(kind):
    active = editor.active
    assert active is not None and active.selected
    if kind == "D":
        assert len(active.selected) == 1 and active.selected[0].element == n.SketchElement.GEOMETRY
    else:
        assert len(active.selected) == 2 and not active.selected[0].entity
        assert active.selected[1].element == n.SketchElement.CIRCLE_CENTER
    event(kind)


def confirm_sketch():
    active = editor.active
    assert active is not None and not active.session.error
    assert active.session.report.state == n.SketchSolveState.FULLY_CONSTRAINED
    assert active.session.report.remaining_degrees_of_freedom == 0
    assert len(active.session.document.get_sketch(active.session.sketch_id).entities) == 1
    state["sketch"] = active.session.sketch_id
    snapshot_unchanged()
    event("RET")


def preview_cut():
    assert editor.active is None
    assert next(item.support for item in document().sketches if item.id == state["sketch"]) is not None
    state["cut_baseline"] = bpy.context.scene.blendercad_document_snapshot
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.preview_feature("INVOKE_DEFAULT", operation="CUT",
            sketch_id=state["sketch"], value_mm=3) == {"RUNNING_MODAL"}
    active = feature_editor.active
    assert active is not None and active.session.valid, None if active is None else active.session.error
    close(active.anchor.z, 0.01)
    assert active.direction.z < -0.99999
    state["cut_draft"] = active.session.document.to_snapshot()


def draw_cut_preview():
    capture("attached-cut-preview.png")
    active = feature_editor.active
    assert active is not None and active.draw_count > 0 and not active.draw_error, active.draw_error
    assert active.overlay is not None and active.overlay.draw_count > 0
    assert bpy.context.scene.blendercad_document_snapshot == state["cut_baseline"]


def move_end_on_handle():
    active = feature_editor.active
    assert active is not None and active.drag is not None
    baseline, start, projection, value, mouse_mode = active.drag
    assert not mouse_mode and projection.x > 0
    bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING",
        x=int(start.x + region.x + 20), y=int(start.y + region.y))


def verify_end_on_handle():
    active = feature_editor.active
    assert active is not None and active.session.valid and active.session.value_mm > 3
    assert bpy.context.scene.blendercad_document_snapshot == state["cut_baseline"]


def reset_end_on_handle():
    active = feature_editor.active
    assert active is not None and active.drag is None
    assert active.session.document.to_snapshot() == state["cut_draft"]


def cut_sketch():
    assert feature_editor.active is None
    state["features"] = [item.id for item in document().features]
    assert len(state["features"]) == 2
    state["cut"] = state["features"][-1]
    state["intent"] = support_intent()
    check_geometry(10)
    state["before_parameter"] = bpy.context.scene.blendercad_document_snapshot
    bpy.ops.ed.undo_push(message="Attached pocket parameter edit baseline")


def position_parameter_cursor():
    # Process the mouse move in its own event-loop iteration before invoking
    # Blender's popup; queued events do not change the current invoke position.
    global cursor
    cursor = (640, 500)
    bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])


def invoke_parameter():
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.parameter("INVOKE_DEFAULT", name="thickness") == {"RUNNING_MODAL"}


def focus_parameter():
    capture("parameter-dialog.png")
    x, y = cursor[0] - 60, cursor[1] - 112
    event("LEFTMOUSE", x=x, y=y)
    event("LEFTMOUSE", x=x, y=y)


def type_parameter():
    capture("parameter-focused.png")
    event("A", ctrl=True)
    text("14")


def edited_parameter():
    close(document().length_value("thickness"), 14)
    check_geometry(14)
    assert support_intent() == state["intent"], "Upstream edit rewrote persistent support intent"
    state["after_parameter"] = bpy.context.scene.blendercad_document_snapshot
    event("Z", ctrl=True)


def undone_parameter():
    assert bpy.context.scene.blendercad_document_snapshot == state["before_parameter"], "One Ctrl-Z did not restore complete attached pocket"
    check_geometry(10)
    event("Z", ctrl=True, shift=True)


def redone_parameter():
    assert bpy.context.scene.blendercad_document_snapshot == state["after_parameter"], "One redo did not restore complete attached pocket"
    check_geometry(14)


def reopen_sketch():
    state["before_editor"] = bpy.context.scene.blendercad_document_snapshot
    state["before_objects"], state["before_meshes"] = len(bpy.data.objects), len(bpy.data.meshes)
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.edit_sketch("INVOKE_DEFAULT", sketch_id=state["sketch"]) == {"RUNNING_MODAL"}
    assert editor.active is not None
    state["editor_resources"] = vars(editor.active)
    close(editor.active.session.display_problem.plane.origin.z, 14)
    assert editor.active.session.report.state == n.SketchSolveState.FULLY_CONSTRAINED


def reopened_cancelled():
    assert editor.active is None
    assert state["editor_resources"]["session"].evaluator is None
    snapshot_unchanged()
    check_geometry(14)


def broken_support_fixture():
    # The only native-definition mutation in this test is an explicit failure
    # fixture after actual face/circle authoring and verified upstream editing.
    scene = bpy.context.scene
    current = document()
    state["valid_generation"] = p.current_packet(scene, owner()).arrays["geometry_generation"]
    replacement = support()
    replacement.face = n.PersistentReference(state["body"], n.TopologyKind.FACE,
        n.ReferenceAnchor(state["base"], "missing:deliberate-support-fixture"))
    current.set_sketch_support(state["sketch"], replacement)
    commands.commit(scene, current)
    report = p.runtime(scene).report
    assert [item.state for item in report.features] == [n.EvaluationState.UP_TO_DATE, n.EvaluationState.FAILED]
    assert report.features[-1].diagnostic and "support" in report.features[-1].diagnostic.lower()
    assert p.runtime(scene).packets[state["body"]].arrays["geometry_generation"] == state["valid_generation"]
    assert not p.display_for(scene, owner())["blendercad_current"]
    sentinel = options.output_dir / "blocked.step"
    sentinel.write_bytes(b"Preserve the preceding external file on failed support.\n")
    saved = sentinel.read_bytes()
    assert not bpy.ops.blendercad.export_step.poll()
    try:
        commands.export_step(bpy.context, sentinel)
    except (ValueError, RuntimeError):
        pass
    else:
        raise AssertionError("Broken support exported a stale Solid as current")
    assert sentinel.read_bytes() == saved


def expose_inputs():
    execute(bpy.ops.blendercad.sketch_support_inputs, sketch_id=state["sketch"])
    assert document().rollback_before == state["cut"]
    report = p.runtime(bpy.context.scene).report
    assert report.features[0].state == n.EvaluationState.UP_TO_DATE
    assert report.features[1].state == n.EvaluationState.ROLLED_BACK


def repair_support():
    face_clicked()
    execute(bpy.ops.blendercad.repair_sketch_support, sketch_id=state["sketch"])
    assert document().rollback_before is None
    check_geometry(14)
    assert support_intent() == state["intent"], "Explicit repair changed more than the selected support target"


def save_part():
    state["saved_snapshot"] = bpy.context.scene.blendercad_document_snapshot
    view_body(sidebar=True, oblique=True)
    assert bpy.ops.wm.save_as_mainfile(filepath=str(options.output_dir / "supported-pocket.blend")) == {"FINISHED"}


def reopen_part():
    assert bpy.ops.wm.open_mainfile(filepath=str(options.output_dir / "supported-pocket.blend")) == {"FINISHED"}


def verify_reopened_part():
    global area, region
    assert bpy.context.scene.blendercad_document_snapshot == state["saved_snapshot"]
    area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
    region = next(item for item in area.regions if item.type == "WINDOW")
    host = owner()
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    host.select_set(True)
    bpy.context.view_layer.objects.active = host
    check_geometry(14)
    view_body(sidebar=True, oblique=True)
    step_path = options.output_dir / "supported-pocket.step"
    execute(bpy.ops.blendercad.export_step, filepath=str(step_path))
    kernel = n.new_geometry_adapter()
    shape = kernel.read_step(str(step_path))
    try:
        assert kernel.is_valid(shape)
        assert kernel.max_tolerance_mm(shape) <= 0.001
        close(kernel.volume_mm3(shape), 8400 - math.pi * 4 * 3)
    finally:
        kernel.release(shape)


def capture(filename="supported-pocket.png"):
    import gpu
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    window = bpy.context.window
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, window.width, window.height, 4, 0, "FLOAT")
    values = np.asarray(pixels, dtype=np.float32).reshape(-1)
    assert np.ptp(values.reshape((-1, 4))[:, :3]) > 0.1
    image = bpy.data.images.new("Supported pocket framebuffer", width=window.width, height=window.height, alpha=True)
    try:
        image.pixels.foreach_set(values)
        image.filepath_raw = str(options.output_dir / filename)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def scroll_support_controls():
    sidebar = next(item for item in area.regions if item.type == "UI")
    x, y = sidebar.x + sidebar.width // 2, sidebar.y + sidebar.height // 2
    for _ in range(10):
        event("WHEELDOWNMOUSE", x=x, y=y)


def completed():
    assert editor.active is None and not sketch_view.last_error, sketch_view.last_error
    report = {"passed": True,
        "summary": "Attached sketch UI: real face/circle authoring, dimensions, moving support, one-step parameter undo/redo, explicit repair, save/reopen and STEP passed",
        "actual_face_clicks": True, "modal_circle_and_dimensions": True,
        "cancelled_attachment_has_no_definition": True, "fully_constrained": True,
        "upstream_support_motion": True, "blind_pocket_floor_mm": 11,
        "one_step_parameter_undo_redo": True, "stale_export_blocked": True,
        "explicit_selected_face_repair": True, "save_reopen": True, "step_round_trip": True,
        "attached_solid_preview": True, "end_on_handle_adjustment": True,
        "support_controls_screenshot": "support-controls.png",
        "screenshot": "supported-pocket.png", "body_id": state["body"],
        "sketch_id": state["sketch"], "feature_ids": state["features"]}
    (options.output_dir / "result.json").write_text(json.dumps(report, indent=2))
    bpy.ops.wm.quit_blender()


def add(label, action):
    actions.append((label, action))


def key(kind, **arguments):
    add("key " + kind, lambda: event(kind, **arguments))


add("setup parameter-driven stock", setup)
add("actual top face selection", lambda: actual_face_click(10))
add("begin cancellable face sketch", invoke_face_sketch)
key("ESC")
add("verify cancelled attachment leaves no definitions", cancelled_new_sketch)
add("select top face again", lambda: actual_face_click(10))
add("begin attached pocket sketch", invoke_face_sketch)
key("C")
add("actual circle center", lambda: click_uv(state["center_uv"]))
add("actual circle radius point", lambda: click_uv((state["center_uv"][0] + 2, state["center_uv"][1])))
key("S")
add("select whole circle", lambda: click_uv((state["center_uv"][0] + 2, state["center_uv"][1])))
add("radius dimension", lambda: dimension("D"))
add("type exact radius", lambda: text("2"))
key("RET")
for kind, coordinate in (("A", 0), ("B", 1)):
    add("select sketch origin", lambda: click_uv((0, 0)))
    add("select circle center", lambda: click_uv(state["center_uv"], shift=True))
    add("signed center dimension " + kind, lambda kind=kind: dimension(kind))
    add("type exact center coordinate", lambda coordinate=coordinate: text(f"{state['center_uv'][coordinate]:.12g}"))
    key("RET")
add("verify actual sketch GPU draw", check_sketch_draw)
add("confirm fully constrained supported circle", confirm_sketch)
add("begin attached blind-cut preview", preview_cut)
add("draw attached solid preview", draw_cut_preview)
key("G")
add("move end-on depth handle", move_end_on_handle)
add("verify end-on scalar adjustment", verify_end_on_handle)
key("ESC")
add("verify exact cut draft restoration", reset_end_on_handle)
key("RET")
add("create blind cut from supported sketch", cut_sketch)
add("position actual parameter dialog cursor", position_parameter_cursor)
add("invoke actual upstream thickness dialog", invoke_parameter)
add("focus actual thickness widget", focus_parameter)
add("type actual thickness value", type_parameter)
add("capture entered thickness", lambda: capture("parameter-entered.png"))
key("RET")
key("RET")
add("verify support moved and request undo", edited_parameter)
add("verify one undo and request redo", undone_parameter)
add("verify one redo", redone_parameter)
add("reopen attached sketch at moved support", reopen_sketch)
add("draw reopened attached sketch", check_sketch_draw)
key("ESC")
add("verify reopened editor cancellation", reopened_cancelled)
add("explicit broken-reference fixture", broken_support_fixture)
add("expose upstream support input", expose_inputs)
add("actual replacement face selection", lambda: actual_face_click(14))
add("repair selected support and resume history", repair_support)
add("save supported part", save_part)
add("reopen supported part", reopen_part)
add("verify reopened support and STEP", verify_reopened_part)
add("capture completed viewport", capture)
add("scroll actual support controls into view", scroll_support_controls)
add("capture support controls", lambda: capture("support-controls.png"))
add("complete", completed)


def tick():
    global step
    label, action = actions[step]
    try:
        if action is not reopen_part and (bpy.context.window is None or bpy.context.screen is None):
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                action()
        else:
            action()
        if step == len(actions) - 1:
            return None
        step += 1
        return 0.3
    except Exception:
        error = traceback.format_exc()
        print(f"Sketch support step {step} ({label}):\n{error}", flush=True)
        active = editor.active
        report = {"passed": False, "step": step, "action": label, "error": error,
            "draft": None if active is None else {"tool": active.tool, "message": active.message,
                "error": active.session.error, "input": str(active.input_action), "text": active.input_text}}
        (options.output_dir / "result.json").write_text(json.dumps(report, indent=2))
        try:
            capture()
        except Exception:
            pass
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=4, persistent=True)
