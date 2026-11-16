"""Real-window ghost previews, scalar handles and transactional feature edits."""

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
from blendercad import commands, feature_editor as editor, feature_view, presentation as p, sketch_commands

n = addon._native
bpy.context.preferences.view.show_splash = False
LOSS = 1 - math.pi / 4
area, region = None, None
cursor = (500, 450)
state, actions = {}, []
step = 0


def close(actual, expected, *, rel=1e-9):
    assert math.isclose(actual, expected, rel_tol=rel, abs_tol=1e-8), (actual, expected)


def owner():
    scene = bpy.context.scene
    document = p.read_document(scene)
    return next(obj for obj in p.hosts(scene, document.id) if obj[p.BODY] == state["body"])


def current_volume(expected):
    scene = bpy.context.scene
    document = p.read_document(scene)
    actual = p.runtime(scene).evaluator.volume_mm3(document, state["body"])
    close(actual, expected)
    return actual


def fingerprint():
    scene = bpy.context.scene
    runtime = p.runtime(scene)
    return (scene.blendercad_document_snapshot, id(runtime), id(runtime.evaluator),
            tuple((host[p.BODY], p.current_packet(scene, host).token,
                   p.display_for(scene, host).as_pointer(), p.display_for(scene, host).data.as_pointer(),
                   tuple(tuple(vertex.co) for vertex in p.display_for(scene, host).data.vertices),
                   tuple(tuple(row) for row in host.matrix_world))
                  for host in p.hosts(scene, p.read_document(scene).id)),
            tuple(sorted(obj.as_pointer() for obj in bpy.context.selected_objects)),
            bpy.context.active_object.as_pointer() if bpy.context.active_object else None,
            len(bpy.data.objects), len(bpy.data.meshes))


def execute(operator, **values):
    with bpy.context.temp_override(area=area, region=region):
        assert operator(**values) == {"FINISHED"}


def invoke(**values):
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.preview_feature("INVOKE_DEFAULT", **values) == {"RUNNING_MODAL"}
    assert editor.active is not None
    # Blender removes a cancelled operator's RNA immediately. Retain its plain
    # Python resource journal, not an invalidated operator wrapper, for cleanup
    # assertions on the following event-loop iteration.
    state["tool_resources"] = vars(editor.active)


def mouse(value, x, y, kind="LEFTMOUSE"):
    global cursor
    cursor = int(x), int(y)
    bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])
    if kind != "MOUSEMOVE":
        bpy.context.window.event_simulate(type=kind, value=value, x=cursor[0], y=cursor[1])


def event(kind, **values):
    values.setdefault("x", cursor[0])
    values.setdefault("y", cursor[1])
    bpy.context.window.event_simulate(type=kind, value="PRESS", **values)
    values.pop("unicode", None)
    bpy.context.window.event_simulate(type=kind, value="RELEASE", **values)


def text(value):
    digits = ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")
    punctuation = {"=": "EQUAL", "_": "MINUS", "-": "MINUS", ".": "PERIOD", " ": "SPACE"}
    for character in value:
        kind = digits[int(character)] if character.isdigit() else punctuation.get(character, character.upper())
        event(kind, unicode=character, shift=character == "_")


def project(point):
    screen = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, Vector(point))
    assert screen is not None
    return Vector((region.x + screen.x, region.y + screen.y))


def view_body():
    global region
    region = next(item for item in area.regions if item.type == "WINDOW")
    area.spaces.active.show_region_ui = False
    view = area.spaces.active.region_3d
    view.view_rotation = Euler((1.05, 0, 0.6)).to_quaternion()
    view.view_location = Vector((0.015, 0.01, 0.006))
    view.view_distance, view.view_perspective = 0.1, "ORTHO"
    area.spaces.active.clip_start, area.spaces.active.clip_end = 0.0001, 1
    area.spaces.active.shading.color_type = "OBJECT"
    area.tag_redraw()


def active_valid(expected=None):
    active = editor.active
    assert active is not None and active.session.valid, None if active is None else active.session.error
    assert not active.draw_error, active.draw_error
    if expected is not None:
        close(active.session.evaluator.volume_mm3(active.session.document, active.session.body_id), expected)
    return active


def draw_checked():
    active = editor.active
    assert active is not None
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    assert active.draw_count > 0 and not active.draw_error, active.draw_error
    assert active.overlay is not None and active.overlay.draw_count > 0
    assert active.overlay._batches is not None, "Actual draw did not lazily create ghost GPU batches"
    state["gpu_draws"] = state.get("gpu_draws", 0) + active.draw_count


def capture(name):
    import gpu
    draw_checked()
    window = bpy.context.window
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, window.width, window.height, 4, 0, "FLOAT")
    values = np.asarray(pixels, dtype=np.float32).reshape(-1)
    assert np.ptp(values.reshape((-1, 4))[:, :3]) > 0.1
    image = bpy.data.images.new("Feature preview framebuffer", width=window.width, height=window.height, alpha=True)
    try:
        image.pixels.foreach_set(values)
        image.filepath_raw, image.file_format = str(options.output_dir / name), "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def cleanup_checked():
    assert editor.active is None, "Invalidated or cancelled tool remains active"
    resources = state["tool_resources"]
    assert not resources["handlers"] and resources["timer"] is None
    assert resources["overlay"] is None and resources["overlay_arrays"] is None
    assert resources["session"] is None and resources["initial"] is None


def unchanged():
    assert fingerprint() == state["baseline"], "Preview/cancellation changed published intent, cache, mesh, picks or selection"


def setup():
    global area
    addon.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    assert bpy.ops.blendercad.new_document() == {"FINISHED"}
    area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
    view_body()
    execute(bpy.ops.blendercad.add_rectangle_solid, width_mm=30, height_mm=20, depth_mm=10)
    state["body"] = bpy.context.active_object[p.BODY]
    state["base"] = p.read_document(bpy.context.scene).features[0].id
    state["profile"] = p.read_document(bpy.context.scene).features[0].sketch_id
    execute(bpy.ops.blendercad.parameter, name="depth", value=10, unit="mm", create=True)
    execute(bpy.ops.blendercad.parameter, name="round_radius", value=1, unit="mm", create=True)
    execute(bpy.ops.blendercad.parameter, name="double_depth", expression="depth * 2", unit="mm", create=True, use_expression=True)
    execute(bpy.ops.blendercad.set_feature_depth, feature_id=state["base"], parameter="depth")
    view_body()
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Feature preview baseline")
    state["baseline"] = fingerprint()
    state["original_snapshot"] = bpy.context.scene.blendercad_document_snapshot
    current_volume(6000)


def begin_new_extrusion():
    # Initial reconstruction is deliberately deferred until addon registration
    # returns. Establish the source after that startup callback has completed.
    state["baseline"] = fingerprint()
    invoke(operation="EXTRUDE", sketch_id=state["profile"], value_mm=15)
    active_valid(30 * 20 * 15)


def competition_rejected():
    assert not bpy.ops.blendercad.parameter.poll()
    assert not bpy.ops.blendercad.edit_sketch.poll()
    assert not bpy.ops.blendercad.export_step.poll()
    try:
        commands.set_depth(bpy.context, state["base"], 17)
    except ValueError as error:
        assert "Finish or cancel" in str(error), str(error)
    else:
        raise AssertionError("A competing CAD command published during the preview")
    unchanged()
    capture("extrusion-preview.png")


def begin_depth():
    cleanup_checked()
    unchanged()
    invoke(operation="DEPTH", feature_id=state["base"])
    active = active_valid(6000)
    assert active.session.input == "depth"
    state["drag_baseline"] = active.session.document.to_snapshot()
    state["baseline"] = fingerprint()


def begin_confirmed_extrusion():
    cleanup_checked()
    unchanged()
    invoke(operation="EXTRUDE", sketch_id=state["profile"], value_mm=15)
    active = active_valid(9000)
    state["created_body"], state["created_feature"] = active.session.body_id, active.session.feature_id
    state["created_placement"] = active.placement.copy()


def extrusion_confirmed():
    cleanup_checked()
    document = p.read_document(bpy.context.scene)
    assert len(document.features) == 2 and document.features[-1].id == state["created_feature"]
    host = bpy.context.active_object
    assert host[p.BODY] == state["created_body"] and host.matrix_world == state["created_placement"]
    close(p.runtime(bpy.context.scene).evaluator.volume_mm3(document, state["created_body"]), 9000)
    current_volume(6000)
    event("Z", ctrl=True)


def extrusion_undone():
    assert bpy.context.scene.blendercad_document_snapshot == state["original_snapshot"], "One undo did not remove the created extrusion definition"
    assert len(p.hosts(bpy.context.scene, p.read_document(bpy.context.scene).id)) == 1
    current_volume(6000)
    state["baseline"] = fingerprint()


def press_handle():
    active = active_valid()
    point = project(active.anchor + active.direction * active.session.value_mm * active.scale)
    mouse("PRESS", *point)


def move_handle(delta):
    active = editor.active
    assert active is not None and active.drag is not None, "Actual endpoint/G event did not start scalar drag"
    _baseline, start, projection, _value, _mouse_mode = active.drag
    point = start + projection * delta + Vector((region.x, region.y))
    mouse("NOTHING", *point, kind="MOUSEMOVE")


def verify_handle_motion():
    active = active_valid()
    assert active.drag is not None and active.drag[4], "Endpoint was not press-dragged"
    assert active.session.value_mm > 11, active.session.value_mm
    assert active.session.input == "depth"
    close(active.session.document.length_value("depth"), active.session.value_mm)
    unchanged()
    current_volume(6000)


def verify_drag_cancel():
    active = active_valid(6000)
    assert active.drag is None
    assert active.session.document.to_snapshot() == state["drag_baseline"], "Esc did not restore exact drag baseline"
    unchanged()
    mouse("RELEASE", *cursor)


def verify_key_drag():
    active = active_valid()
    assert active.drag is not None and not active.drag[4]
    assert active.session.value_mm < 9.5
    unchanged()
    event("LEFTMOUSE")


def verify_depth_field():
    active = active_valid(30 * 20 * 12)
    assert active.input_text is None and active.drag is None
    assert active.session.input == "depth"
    close(active.session.document.length_value("depth"), 12)
    unchanged()
    state["depth_draft"] = active.session.document.to_snapshot()
    capture("depth-handle-preview.png")


def depth_confirmed():
    cleanup_checked()
    document = p.read_document(bpy.context.scene)
    assert document.to_snapshot() == state["depth_draft"]
    assert document.get_feature(state["base"]).depth == "depth"
    current_volume(7200)
    state["depth_snapshot"] = bpy.context.scene.blendercad_document_snapshot
    event("Z", ctrl=True)


def verify_depth_undo():
    assert bpy.context.scene.blendercad_document_snapshot == state["original_snapshot"], "One Ctrl-Z did not undo complete modal confirmation"
    current_volume(6000)
    event("Z", ctrl=True, shift=True)


def prepare_cut():
    assert bpy.context.scene.blendercad_document_snapshot == state["depth_snapshot"], "One redo did not restore modal confirmation"
    current_volume(7200)
    plane = n.SketchPlane()
    plane.origin = n.Vector3(0, 0, -1)
    draft = sketch_commands.Session(p.read_document(bpy.context.scene), name="Preview through hole", plane=plane)
    circle = draft.add_circle((15, 10), 2)
    center = n.SketchReference(circle, n.SketchElement.CIRCLE_CENTER)
    draft.add_constraint(n.SketchConstraintKind.RADIUS, [n.SketchReference(circle, n.SketchElement.GEOMETRY)], 2)
    draft.add_constraint(n.SketchConstraintKind.DISTANCE_X, [n.SketchReference(), center], 15)
    draft.add_constraint(n.SketchConstraintKind.DISTANCE_Y, [n.SketchReference(), center], 10)
    assert draft.report.state == n.SketchSolveState.FULLY_CONSTRAINED
    commands.commit(bpy.context.scene, draft.document)
    state["cut_sketch"] = draft.sketch_id
    state["baseline"] = fingerprint()
    invoke(operation="CUT", sketch_id=state["cut_sketch"], value_mm=14)
    active_valid(7200 - math.pi * 4 * 12)
    state["good_arrays"] = editor.active.session.arrays


def invalid_checked():
    active = editor.active
    assert active is not None and not active.session.valid and active.session.error
    assert active.session.arrays is state["good_arrays"], "Invalid candidate lost its preceding valid result"
    unchanged()
    draw_checked()


def invalid_confirmation_blocked():
    invalid_checked()
    assert editor.active.message, "Invalid confirmation did not explain the failure"
    capture("invalid-" + editor.active.operation.lower() + "-preview.png")


def cut_confirmed():
    cleanup_checked()
    document = p.read_document(bpy.context.scene)
    assert document.features[-1].type == "subtractive_extrusion"
    state["cut"] = document.features[-1].id
    current_volume(7200 - math.pi * 4 * 12)
    assert p.runtime(bpy.context.scene).evaluator.ray_cast(document, state["body"],
        n.Vector3(15, 10, -10), n.Vector3(0, 0, 1), 40) is None
    view_body()
    bpy.context.scene.blendercad_selection_mode = "EDGE"
    point = project(Vector((30, 0, 6)) * p.unit_scale(bpy.context.scene))
    mouse("PRESS", *point)
    mouse("RELEASE", *point)


def begin_fillet():
    scene = bpy.context.scene
    pick = p.runtime(scene).last_pick
    assert pick is not None and pick.kind == "EDGE", "Real corner click did not resolve a CAD edge"
    close(p.current_measure(scene, owner()).length_mm, 12)
    state["fillet_target"] = p.persistent_target(scene, owner(), pick)
    state["baseline"] = fingerprint()
    invoke(operation="FILLET", parameter="round_radius")
    active_valid(7200 - math.pi * 4 * 12 - LOSS * 12)
    state["good_arrays"] = editor.active.session.arrays


def fillet_recovered():
    active = active_valid(7200 - math.pi * 4 * 12 - LOSS * 4 * 12)
    assert active.session.input == "round_radius"
    unchanged()
    capture("fillet-preview.png")


def fillet_confirmed():
    cleanup_checked()
    document = p.read_document(bpy.context.scene)
    feature = document.features[-1]
    assert feature.type == "fillet" and feature.input_id == state["cut"]
    assert feature.edges[0].anchor.key == state["fillet_target"].anchor.key
    assert feature.radius == "round_radius"
    state["fillet"] = feature.id
    current_volume(7200 - math.pi * 4 * 12 - LOSS * 4 * 12)
    state["baseline"] = fingerprint()
    invoke(operation="DEPTH", feature_id=state["base"])


def expression_bound():
    active = active_valid()
    assert active.session.input == "double_depth"
    state["expression_draft"] = active.session.document.to_snapshot()


def expression_protected():
    active = editor.active
    assert active is not None and "expression" in active.message, active.message
    assert active.session.document.to_snapshot() == state["expression_draft"]
    assert active.session.valid
    unchanged()


def unlinked_checked():
    active = active_valid()
    assert not isinstance(active.session.input, str), "U did not explicitly unlink the expression driver"
    unchanged()


def reset_checked():
    active = active_valid()
    assert active.session.input == "depth"
    assert active.session.document.to_snapshot() == p.read_document(bpy.context.scene).to_snapshot()
    unchanged()


def begin_invalidation(kind):
    cleanup_checked()
    unchanged()
    invoke(operation="DEPTH", feature_id=state["base"])
    state["invalidation"] = kind
    if kind == "source":
        state["restore_document"] = p.read_document(bpy.context.scene)
        changed = p.read_document(bpy.context.scene)
        changed.set_parameter_value("depth", 13)
        bpy.context.scene.blendercad_document_snapshot = p.serialize(changed)
        state["external_snapshot"] = bpy.context.scene.blendercad_document_snapshot
    elif kind == "placement":
        state["matrix"] = owner().matrix_world.copy()
        owner().location.x += 0.003
        bpy.context.view_layer.update()
        state["external_matrix"] = owner().matrix_world.copy()
    elif kind == "viewport":
        area.type = "TEXT_EDITOR"


def invalidated_checked():
    cleanup_checked()
    kind = state["invalidation"]
    if kind == "source":
        assert bpy.context.scene.blendercad_document_snapshot == state["external_snapshot"], "Cancellation overwrote an external document edit"
        commands.commit(bpy.context.scene, state["restore_document"])
    elif kind == "placement":
        assert owner().matrix_world == state["external_matrix"], "Cancellation overwrote external placement"
        owner().matrix_world = state["matrix"]
        bpy.context.view_layer.update()
    elif kind == "viewport":
        area.type = "VIEW_3D"
        view_body()
    state["baseline"] = fingerprint()


def save_with_draft():
    invoke(operation="DEPTH", feature_id=state["base"])


def save_committed_only():
    active_valid()
    assert editor.active.session.value_mm == 14
    state["saved_snapshot"] = bpy.context.scene.blendercad_document_snapshot
    assert bpy.ops.wm.save_as_mainfile(filepath=str(options.output_dir / "preview.blend")) == {"FINISHED"}
    assert bpy.context.scene.blendercad_document_snapshot == state["saved_snapshot"]


def reopen():
    assert bpy.ops.wm.open_mainfile(filepath=str(options.output_dir / "preview.blend")) == {"FINISHED"}


def reopened():
    global area
    cleanup_checked()
    area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
    view_body()
    assert bpy.context.scene.blendercad_document_snapshot == state["saved_snapshot"]
    document = p.read_document(bpy.context.scene)
    assert document.length_value("depth") == 12 and len(document.features) == 3
    current_volume(7200 - math.pi * 4 * 12 - LOSS * 4 * 12)


def completed():
    assert editor.active is None
    report = {"passed": True,
        "summary": "Feature UI: GPU ghosts, real handle/G drags, cancel/reset, parameter input, one-step undo/redo, cut/fillet failure recovery and lifecycle guards passed",
        "real_handle_drag": True, "keyboard_scalar_drag": True,
        "cancel_exact_baseline": True, "parameter_binding_preserved": True,
        "expression_driver_protected": True, "explicit_unlink": True,
        "one_step_confirmation_undo_redo": True, "invalid_confirmation_blocked": True,
        "precise_through_hole": True, "real_fillet_edge_click": True,
        "source_placement_viewport_guards": True, "save_reopen_committed_only": True,
        "gpu_draws": state["gpu_draws"],
        "screenshots": ["extrusion-preview.png", "depth-handle-preview.png", "invalid-cut-preview.png", "invalid-fillet-preview.png", "fillet-preview.png"]}
    (options.output_dir / "result.json").write_text(json.dumps(report, indent=2))
    bpy.ops.wm.quit_blender()


def add(label, action):
    actions.append((label, action))


def key(kind, **values):
    add("key " + kind, lambda: event(kind, **values))


def enter_value(value):
    key("D")
    add("type " + value, lambda: text(value))
    key("RET")


add("setup committed parametric stock", setup)
add("new extrusion ghost", begin_new_extrusion)
add("GPU ghost and competing command guard", competition_rejected)
key("ESC")
add("confirmable independent extrusion preview", begin_confirmed_extrusion)
key("RET")
add("verify created body and request undo", extrusion_confirmed)
add("verify one undo removes created body", extrusion_undone)
add("cancel extrusion and begin depth", begin_depth)
add("press actual scalar endpoint", press_handle)
add("move scalar endpoint", lambda: move_handle(2))
add("verify actual parameter-preserving handle drag", verify_handle_motion)
key("ESC")
add("verify exact handle drag cancellation", verify_drag_cancel)
key("G")
add("move keyboard scalar drag", lambda: move_handle(-1.5))
add("verify keyboard scalar drag", verify_key_drag)
enter_value("12")
add("verify numeric input and capture ghost", verify_depth_field)
key("RET")
add("verify confirmation and request undo", depth_confirmed)
add("verify one undo and request redo", verify_depth_undo)
add("verify redo and begin through-cut preview", prepare_cut)
enter_value("0")
add("verify invalid cut keeps preceding valid ghost", invalid_checked)
key("RET")
add("verify invalid cut cannot confirm", invalid_confirmation_blocked)
key("R")
key("RET")
add("verify cut and click real CAD edge", cut_confirmed)
add("begin persistent fillet preview", begin_fillet)
enter_value("100")
add("verify failed fillet keeps preceding valid ghost", invalid_checked)
key("RET")
add("verify failed fillet cannot confirm", invalid_confirmation_blocked)
enter_value("2")
add("verify recovered parameter-driven fillet", fillet_recovered)
key("RET")
add("verify fillet confirmation and begin upstream edit", fillet_confirmed)
enter_value("=double_depth")
add("verify expression binding", expression_bound)
key("G")
add("attempt expression-driven handle move", lambda: move_handle(1))
add("verify expression driver is protected", expression_protected)
key("ESC")
key("U")
add("verify explicit unlink", unlinked_checked)
key("R")
add("verify reset restores initial draft", reset_checked)
key("ESC")
for kind in ("source", "placement", "viewport"):
    add("external " + kind + " invalidation", lambda kind=kind: begin_invalidation(kind))
    add("verify " + kind + " cleanup", invalidated_checked)
add("begin uncommitted save/load draft", save_with_draft)
enter_value("14")
add("save only committed intent while preview is active", save_committed_only)
add("load saved file with active preview", reopen)
add("verify load cancelled draft and rebuilt committed geometry", reopened)
add("complete", completed)


def tick():
    global step
    label, action = actions[step]
    try:
        if action is not reopen and (bpy.context.window is None or bpy.context.screen is None):
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
        print(f"Feature preview step {step} ({label}):\n{error}", flush=True)
        active = editor.active
        report = {"passed": False, "step": step, "action": label, "error": error,
                  "preview": None if active is None else {
                      "valid": active.session.valid, "error": active.session.error,
                      "message": active.message, "draw_error": active.draw_error,
                      "input": str(active.session.input), "value_mm": active.session.value_mm,
                      "typing": active.input_text, "dragging": active.drag is not None}}
        (options.output_dir / "result.json").write_text(json.dumps(report, indent=2))
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=4, persistent=True)
