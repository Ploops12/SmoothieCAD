"""Create a dimensioned L bracket entirely through Blender's modelling UI."""

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
parser.add_argument("--scale", type=float, default=1)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
S = options.scale
assert math.isfinite(S) and S > 0
options.output_dir.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory()
with zipfile.ZipFile(options.addon_zip) as archive:
    archive.extractall(temporary.name)
sys.path.insert(0, temporary.name)
addon = importlib.import_module("blendercad")
from blendercad import presentation as p, sketch_editor as editor, sketch_view

n = addon._native
bpy.context.preferences.view.show_splash = False
area, region = None, None
cursor = (500, 450)
state = {}
actions = []
step = 0
LOSS = 1 - math.pi / 4


def expected(span=50, holes=2, fillet=True):
    document = p.read_document(bpy.context.scene)
    values = {parameter.name: parameter.value for parameter in document.parameters}
    # Blender FloatProperty stores single-precision UI inputs. Test the kernel
    # against those actual design definitions, not unrepresentable decimal
    # literals; independently keep the nominal inputs within the fixed budget.
    for name, nominal in (("span", span), ("height", 40), ("thickness", 5),
                          ("width", 30), ("hole_radius", 3), ("edge_radius", 1)):
        assert abs(values[name] - nominal * S) <= document.geometric_tolerance_mm
    result = (values["span"] * values["thickness"] + values["thickness"] * (values["height"] - values["thickness"])) * values["width"]
    result -= holes * math.pi * values["hole_radius"] ** 2 * values["thickness"]
    return result - (LOSS * values["edge_radius"] ** 2 * values["width"] if fillet else 0)


def close(actual, target):
    assert math.isclose(actual, target, rel_tol=1e-8, abs_tol=1e-10 * max(1, S ** 3)), (actual, target, S)


def body():
    scene = bpy.context.scene
    return next(item for item in p.hosts(scene, p.read_document(scene).id) if item[p.BODY] == state["body"])


def volume(span=50, holes=2, fillet=True):
    scene = bpy.context.scene
    document = p.read_document(scene)
    assert all(item.state == n.EvaluationState.UP_TO_DATE for item in p.runtime(scene).report.features)
    actual = p.runtime(scene).evaluator.volume_mm3(document, state["body"])
    close(actual, expected(span, holes, fillet))
    return actual


def execute(operator, **arguments):
    with bpy.context.temp_override(area=area, region=region):
        assert operator(**arguments) == {"FINISHED"}


def event(kind, **values):
    global cursor
    if "x" in values:
        cursor = values["x"], values["y"]
        bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])
    else:
        values["x"], values["y"] = cursor
    bpy.context.window.event_simulate(type=kind, value="PRESS", **values)
    values.pop("unicode", None)
    bpy.context.window.event_simulate(type=kind, value="RELEASE", **values)


def type_text(text):
    digit_names = ("ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")
    punctuation = {"=": "EQUAL", "_": "MINUS", "-": "MINUS", ".": "PERIOD", " ": "SPACE"}
    for character in text:
        kind = digit_names[int(character)] if character.isdigit() else punctuation.get(character, character.upper())
        event(kind, unicode=character, shift=character == "_")


def project(point):
    value = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, point)
    assert value is not None, "Target outside the active view"
    return int(region.x + value.x), int(region.y + value.y)


def click(uv, shift=False):
    active = editor.active
    assert active is not None
    point = sketch_view.world_point(active.session.display_problem.plane,
        (uv[0] * S, uv[1] * S), active.placement, active.scale)
    x, y = project(point)
    event("LEFTMOUSE", x=x, y=y, shift=shift)


def invoke_sketch(name, plane, center, distance):
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.edit_sketch("INVOKE_DEFAULT", name=name, plane=plane) == {"RUNNING_MODAL"}
    active = editor.active
    assert active is not None
    view = area.spaces.active.region_3d
    # Camera-only scale setup. Empty sketches have no bounds to frame yet.
    view.view_location = sketch_view.world_point(active.session.display_problem.plane,
        (center[0] * S, center[1] * S), active.placement, active.scale)
    view.view_distance = distance * S * active.scale
    area.tag_redraw()


def confirm_sketch(key, entities):
    active = editor.active
    assert active is not None
    assert len(active.session.document.get_sketch(active.session.sketch_id).entities) == entities
    assert active.session.report.state == n.SketchSolveState.FULLY_CONSTRAINED, (
        key, active.session.report.state, active.session.error, active.message)
    assert not active.session.error
    state[key] = active.session.sketch_id
    event("RET")


def dimension_key(kind):
    active = editor.active
    assert active is not None and active.selected, (kind, active.message)
    if kind == "D":
        assert len(active.selected) == 1 and active.selected[0].element == n.SketchElement.GEOMETRY
    else:
        assert len(active.selected) == 2 and not active.selected[0].entity
        assert active.selected[1].element == n.SketchElement.CIRCLE_CENTER
    event(kind)


def add(label, action):
    actions.append((label, action))


def key(kind, **arguments):
    add(f"key {kind}", lambda: event(kind, **arguments))


def click_action(uv, shift=False):
    add(f"click {uv}", lambda: click(uv, shift))


def bind_dimension(uv, parameter):
    click_action(uv)
    add("dimension selection", lambda: dimension_key("D"))
    add("type =" + parameter, lambda: type_text("=" + parameter))
    key("RET")


def center_dimension(center, shortcut, parameter):
    click_action((0, 0))
    click_action(center, shift=True)
    add("center dimension " + shortcut, lambda: dimension_key(shortcut))
    add("type =" + parameter, lambda: type_text("=" + parameter))
    key("RET")


def setup():
    global area, region
    addon.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.blendercad.new_document()
    area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
    region = next(item for item in area.regions if item.type == "WINDOW")
    area.spaces.active.show_region_ui = False
    parameters = {"span": 50, "height": 40, "thickness": 5, "width": 30,
                  "hole_radius": 3, "edge_radius": 1}
    for name, value in parameters.items():
        execute(bpy.ops.blendercad.parameter, name=name, value=value * S, unit="mm", create=True)
    for name, expression in {"rise": "height - thickness", "foot_x": "span * 0.6",
                             "arm_y": "height * 0.5", "mid_width": "width * 0.5",
                             "through_depth": "thickness * 3"}.items():
        execute(bpy.ops.blendercad.parameter, name=name, expression=expression,
                unit="mm", create=True, use_expression=True)
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Before bracket construction")


def extrude_profile():
    assert editor.active is None
    execute(bpy.ops.blendercad.extrude_sketch, sketch_id=state["profile"], parameter="width")
    state["body"] = bpy.context.active_object[p.BODY]
    volume(holes=0, fillet=False)


def begin_foot():
    # Deliberate cutter overtravel avoids relying on a float cursor position
    # coinciding exactly with a double-precision CAD face at every scale.
    bpy.context.scene.cursor.location = (0, 10 * S * p.unit_scale(bpy.context.scene), 0)
    invoke_sketch("Foot hole", "XZ", (18, 8), 65)


def cut_foot():
    assert editor.active is None
    execute(bpy.ops.blendercad.extrude_sketch, sketch_id=state["foot"], parameter="through_depth", cut=True)
    volume(holes=1, fillet=False)


def begin_arm():
    bpy.context.scene.cursor.location = (-5 * S * p.unit_scale(bpy.context.scene), 0, 0)
    invoke_sketch("Arm hole", "YZ", (15, 8), 65)


def cut_arm():
    assert editor.active is None
    execute(bpy.ops.blendercad.extrude_sketch, sketch_id=state["arm"], parameter="through_depth", cut=True)
    volume(fillet=False)


def view_body(sidebar=False):
    global region
    area.spaces.active.show_region_ui = sidebar
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    if sidebar:
        sidebar_region = next(item for item in area.regions if item.type == "UI")
        sidebar_region.active_panel_category = "BlenderCAD"
    region = next(item for item in area.regions if item.type == "WINDOW")
    view = area.spaces.active.region_3d
    view.view_rotation = Euler((0.7, 0, 0.65)).to_quaternion()
    scale = p.unit_scale(bpy.context.scene)
    view.view_location = Vector((25 * S * scale, 20 * S * scale, 15 * S * scale))
    view.view_distance, view.view_perspective = 120 * S * scale, "ORTHO"
    # Keep depth-buffer precision proportional to the framed part. A default
    # kilometre-scale far plane causes visible z fighting for sub-mm models.
    area.spaces.active.clip_start = max(1e-6, view.view_distance * 0.01)
    area.spaces.active.clip_end = max(0.01, view.view_distance * 10)
    area.spaces.active.shading.color_type = "OBJECT"
    area.tag_redraw()


def pick_fillet_edge():
    point = Vector((50 * S, 0, 15 * S)) * p.unit_scale(bpy.context.scene)
    x, y = project(point)
    event("LEFTMOUSE", x=x, y=y)


def invoke_fillet():
    scene = bpy.context.scene
    owner = body()
    assert bpy.context.active_object == owner
    pick = p.runtime(scene).last_pick
    assert pick is not None and pick.kind == "EDGE", "Actual bracket edge click did not resolve a CAD edge"
    close(p.current_measure(scene, owner).length_mm, p.read_document(scene).length_value("width"))
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.fillet("INVOKE_DEFAULT", parameter="edge_radius") == {"RUNNING_MODAL"}


def fillet_created():
    document = p.read_document(bpy.context.scene)
    assert [item.type for item in document.features] == ["extrusion", "subtractive_extrusion", "subtractive_extrusion", "fillet"]
    assert len(document.sketches) == 3
    state["feature_ids"] = [item.id for item in document.features]
    state["initial_snapshot"] = bpy.context.scene.blendercad_document_snapshot
    volume()


def invoke_old_parameter_edit():
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.parameter("INVOKE_DEFAULT", name="span") == {"RUNNING_MODAL"}


def focus_parameter_value():
    # The controlled factory-startup dialog has Name/Create/Unit/Expression
    # rows before its numeric Value. Actual mouse focus/text events are needed
    # for Blender to bracket this interaction as one undoable user command.
    capture("parameter-dialog.png")
    x, y = cursor[0] - 60, cursor[1] - 112
    event("LEFTMOUSE", x=x, y=y)
    event("LEFTMOUSE", x=x, y=y)


def type_old_parameter_value():
    event("A", ctrl=True)
    type_text(f"{55 * S:g}")


def old_parameter_edit():
    actual = next(item.value for item in p.read_document(bpy.context.scene).parameters if item.name == "span")
    assert actual == float(np.float32(55 * S)), (actual, 55 * S)
    volume(span=55)
    state["edited_snapshot"] = bpy.context.scene.blendercad_document_snapshot


def undo_checked():
    assert bpy.context.scene.blendercad_document_snapshot == state["initial_snapshot"], "One Ctrl-Z did not undo the complete parameter edit"
    volume()
    event("Z", ctrl=True, shift=True)


def redo_checked():
    assert bpy.context.scene.blendercad_document_snapshot == state["edited_snapshot"], "One redo did not restore downstream bracket intent"
    volume(span=55)
    execute(bpy.ops.blendercad.parameter, name="span", value=50 * S)
    volume()
    assert [item.id for item in p.read_document(bpy.context.scene).features] == state["feature_ids"]


def save_bracket():
    assert editor.active is None
    document = p.read_document(bpy.context.scene)
    assert document.geometric_tolerance_mm == 0.001
    for key in ("profile", "foot", "arm"):
        problem = document.evaluated_sketch(state[key])
        assert n.new_sketch_solver(document.geometric_tolerance_mm).solve(problem).state == n.SketchSolveState.FULLY_CONSTRAINED
    state["saved_snapshot"] = bpy.context.scene.blendercad_document_snapshot
    assert bpy.ops.wm.save_as_mainfile(filepath=str(options.output_dir / "bracket.blend")) == {"FINISHED"}


def reopen_bracket():
    # Loading replaces Blender's windows/screens. Do not use the old timer's
    # context or RNA UI pointers until the next event-loop iteration.
    assert bpy.ops.wm.open_mainfile(filepath=str(options.output_dir / "bracket.blend")) == {"FINISHED"}


def verify_reopened_bracket():
    global area, region
    assert bpy.context.scene.blendercad_document_snapshot == state["saved_snapshot"]
    assert [item.id for item in p.read_document(bpy.context.scene).features] == state["feature_ids"]
    area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
    region = next(item for item in area.regions if item.type == "WINDOW")
    owner = body()
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    owner.select_set(True)
    bpy.context.view_layer.objects.active = owner
    volume()
    view_body(sidebar=True)


def exchange_bracket():
    filepath = options.output_dir / "bracket.step"
    execute(bpy.ops.blendercad.export_step, filepath=str(filepath))
    assert filepath.is_file() and filepath.stat().st_size > 0
    kernel = n.new_geometry_adapter(0.001)
    shape = kernel.read_step(str(filepath))
    try:
        assert kernel.is_valid(shape)
        assert kernel.max_tolerance_mm(shape) <= 0.001
        close(kernel.volume_mm3(shape), expected())
    finally:
        kernel.release(shape)
    scene = bpy.context.scene
    document = p.read_document(scene)
    evaluator = p.runtime(scene).evaluator
    values = {parameter.name: parameter.value for parameter in document.parameters}
    for origin, direction in (
            ((values["foot_x"], values["thickness"] + 10 * S, values["mid_width"]), (0, -1, 0)),
            ((-10 * S, values["arm_y"], values["mid_width"]), (1, 0, 0))):
        assert evaluator.ray_cast(document, state["body"], n.Vector3(*origin), n.Vector3(*direction), 200 * S) is None, "Mounting hole is not through the arm"
    count = p.current_packet(scene, body()).arrays["face_count"]
    radii = [evaluator.measure_element(document, state["body"], n.TopologyElement(n.TopologyKind.FACE, index)).radius_mm for index in range(count)]
    assert sum(radius is not None and math.isclose(radius, 3 * S, rel_tol=1e-7) for radius in radii) >= 2, radii
    assert any(radius is not None and math.isclose(radius, S, rel_tol=1e-7) for radius in radii), radii


def capture(filename="bracket.png"):
    import gpu
    window = bpy.context.window
    with bpy.context.temp_override(area=area, region=region):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, window.width, window.height, 4, 0, "FLOAT")
    values = np.array(pixels, dtype=np.float32).reshape(-1)
    assert np.ptp(values.reshape((-1, 4))[:, :3]) > 0.1
    image = bpy.data.images.new("Bracket acceptance framebuffer", width=window.width, height=window.height, alpha=True)
    try:
        image.pixels.foreach_set(values)
        image.filepath_raw = str(options.output_dir / filename)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def completed():
    assert editor.active is None and not sketch_view.last_error, sketch_view.last_error
    result = {"passed": True, "scale": S, "body_id": state["body"], "volume_mm3": volume(),
        "summary": f"Bracket UI scale {S:g}: modal L profile, constrained holes, cuts/fillet, parameter undo/redo, save/reopen and STEP passed",
        "modal_geometry": True, "fully_constrained_sketches": 3, "named_dimensions": True,
        "old_parameter_undo_redo": True, "step_round_trip": True, "save_reopen": True,
        "screenshot": "bracket.png", "blend": "bracket.blend", "step": "bracket.step"}
    (options.output_dir / "result.json").write_text(json.dumps(result, indent=2))
    bpy.ops.wm.quit_blender()


add("setup named parameters", setup)
add("new L profile", lambda: invoke_sketch("Bracket L profile", "XY", (25, 20), 100))
for uv in ((0, 0), (50, 0), (50, 5), (5, 5), (5, 40), (0, 40), (0, 0)):
    click_action(uv, shift=True)
key("RIGHTMOUSE")
for uv, parameter in (((25, 0), "span"), ((50, 2.5), "thickness"), ((5, 22.5), "rise"), ((2.5, 40), "thickness")):
    bind_dimension(uv, parameter)
add("confirm fully constrained L", lambda: confirm_sketch("profile", 6))
add("extrude width", extrude_profile)
for name, begin, center, key_name, cut in (("foot", begin_foot, (30, 15), "foot_x", cut_foot),
                                         ("arm", begin_arm, (20, 15), "arm_y", cut_arm)):
    add("new " + name + " hole", begin)
    key("C")
    click_action(center)
    click_action((center[0] + 3, center[1]))
    key("S")
    bind_dimension((center[0] + 3, center[1]), "hole_radius")
    center_dimension(center, "A", key_name)
    center_dimension(center, "B", "mid_width")
    add("confirm fully constrained " + name + " circle", lambda key_name=name: confirm_sketch(key_name, 1))
    add("cut " + name + " through wall", cut)
add("frame bracket for edge pick", lambda: (view_body(), setattr(bpy.context.scene, "blendercad_selection_mode", "EDGE")))
add("actual CAD corner-edge click", pick_fillet_edge)
add("invoke named-radius fillet", invoke_fillet)
key("RET")
add("verify complete bracket", fillet_created)
add("invoke old span parameter dialog", invoke_old_parameter_edit)
add("focus actual parameter Value widget", focus_parameter_value)
add("type edited span", type_old_parameter_value)
key("RET")
key("RET")
add("verify old span edit", old_parameter_edit)
key("Z", ctrl=True)
add("verify undo and request redo", undo_checked)
add("verify redo and restore reference span", redo_checked)
add("save bracket", save_bracket)
add("reopen bracket", reopen_bracket)
add("verify reconstructed bracket in live window", verify_reopened_bracket)
add("public STEP export and analytic round trip", exchange_bracket)
add("capture completed GPU framebuffer", capture)
add("complete", completed)


def tick():
    global step
    label, action = actions[step]
    try:
        if action is not reopen_bracket and (bpy.context.window is None or bpy.context.screen is None):
            # Application timers are not permanently associated with a window;
            # after .blend loading select Blender's replacement live window.
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
        print(f"Bracket step {step} ({label}) scale {S}:\n{error}", flush=True)
        active = editor.active
        draft = None if active is None else {"tool": active.tool, "message": active.message,
            "error": active.session.error, "report": None if active.session.report is None else active.session.report.state.name,
            "input": repr(active.input_action), "text": active.input_text}
        (options.output_dir / "result.json").write_text(json.dumps({"passed": False,
            "scale": S, "step": step, "action": label, "error": error, "draft": draft}, indent=2))
        try:
            capture("failure.png")
        except Exception:
            pass
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=4, persistent=True)
