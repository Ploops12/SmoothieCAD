"""Real-window sketch drawing, dimension entry, preview cancellation and undo."""

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
temporary = tempfile.TemporaryDirectory()
with zipfile.ZipFile(options.addon_zip) as archive:
    archive.extractall(temporary.name)
sys.path.insert(0, temporary.name)
addon = importlib.import_module("blendercad")
from blendercad import presentation as p, sketch_editor as editor, sketch_view

bpy.context.preferences.view.show_splash = False
step, area, region, sketch_id, body_id = 0, None, None, None, None
baseline, token, mesh = None, None, None
cursor = (500, 450)
drag_start = None
placed_sketch = None
source_placement = None


def capture_viewport():
    """Read the draw framebuffer; Xvfb's screen screenshot can be all black."""
    import gpu
    captured = {}

    def read_pixels():
        if bpy.context.area == area:
            width, height = region.width, region.height
            pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, "FLOAT")
            captured.update(size=(width, height), pixels=np.array(pixels, dtype=np.float32).reshape(-1))

    handle = bpy.types.SpaceView3D.draw_handler_add(read_pixels, (), "WINDOW", "POST_PIXEL")
    try:
        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
    finally:
        bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
    assert captured and np.ptp(captured["pixels"].reshape((-1, 4))[:, :3]) > 0.1, "Sketch draw framebuffer was empty"
    width, height = captured["size"]
    image = bpy.data.images.new("Sketch viewport verification", width=width, height=height, alpha=True)
    try:
        image.pixels.foreach_set(captured["pixels"])
        image.filepath_raw = str(options.output_dir / "sketch-editor.png")
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def event(kind, **values):
    global cursor
    if "x" in values:
        cursor = values["x"], values["y"]
        bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])
    else:
        values["x"], values["y"] = cursor
    bpy.context.window.event_simulate(type=kind, value="PRESS", **values)
    bpy.context.window.event_simulate(type=kind, value="RELEASE", **values)


def location(uv):
    active = editor.active
    point = sketch_view.world_point(active.session.display_problem.plane, uv, active.placement, active.scale)
    value = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d, point)
    assert value is not None
    return int(region.x + value.x), int(region.y + value.y)


def click(uv):
    x, y = location(uv)
    event("LEFTMOUSE", x=x, y=y)


def invoke(sketch="", plane="XY"):
    with bpy.context.temp_override(area=area, region=region):
        assert bpy.ops.blendercad.edit_sketch("INVOKE_DEFAULT", sketch_id=sketch, plane=plane) == {"RUNNING_MODAL"}


def volume():
    scene = bpy.context.scene
    return p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body_id)


def rejected_entry():
    """Actual modal setup errors must release ownership without scene edits."""
    scene = bpy.context.scene
    previous = scene.blendercad_document_snapshot
    snapshot = json.loads(previous)
    snapshot["revision"] = 2 ** 64 - 1
    exhausted = json.dumps(snapshot)
    scene.blendercad_document_snapshot = exhausted
    before = (len(bpy.data.objects), len(bpy.data.meshes))
    try:
        try:
            with bpy.context.temp_override(area=area, region=region):
                result = bpy.ops.blendercad.edit_sketch("INVOKE_DEFAULT")
        except RuntimeError as error:
            assert "revision" in str(error).lower(), str(error)
        else:
            assert result == {"CANCELLED"}
        assert editor.active is None
        assert scene.blendercad_document_snapshot == exhausted
        assert (len(bpy.data.objects), len(bpy.data.meshes)) == before
    finally:
        scene.blendercad_document_snapshot = previous
        p.sync(scene)


def tick():
    global step, area, region, sketch_id, body_id, baseline, token, mesh, drag_start, placed_sketch, source_placement
    try:
        scene = bpy.context.scene
        if step == 0:
            addon.register()
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)
            bpy.ops.blendercad.new_document()
            area = next(item for item in bpy.context.screen.areas if item.type == "VIEW_3D")
            region = next(item for item in area.regions if item.type == "WINDOW")
            baseline = scene.blendercad_document_snapshot
            bpy.ops.ed.undo_push(message="Before interactive sketch")
            invoke()
        elif step == 1:
            assert editor.active is not None
            event("R")
        elif step == 2:
            click((0, 0))
        elif step == 3:
            click((30, 20))
        elif step == 4:
            assert len(editor.active.session.document.get_sketch(editor.active.session.sketch_id).entities) == 4
            assert scene.blendercad_document_snapshot == baseline and len(p.read_document(scene).sketches) == 0
            # Scene/native changes are forbidden behind the active modal owner.
            from blendercad import commands
            try:
                commands.set_parameter(bpy.context, "interloper", 1, "mm")
            except ValueError as error:
                assert "Finish or cancel" in str(error)
            else:
                raise AssertionError("Concurrent CAD command bypassed the sketch owner")
            event("S")
        elif step == 5:
            click((15, 0))
        elif step == 6:
            assert editor.active.selected[-1].element == addon._native.SketchElement.GEOMETRY
            event("D")
        elif step == 7:
            event("THREE")
            event("ZERO")
        elif step == 8:
            event("RET")
        elif step == 9:
            click((30, 10))
        elif step == 10:
            event("D")
        elif step == 11:
            event("TWO")
            event("ZERO")
        elif step == 12:
            event("RET")
        elif step == 13:
            assert editor.active.session.report.state == addon._native.SketchSolveState.FULLY_CONSTRAINED, editor.active.session.error
            assert scene.blendercad_document_snapshot == baseline
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            assert sketch_view.draw_count > 0 and not sketch_view.last_error, sketch_view.last_error
            capture_viewport()
            event("RET")
        elif step == 14:
            assert editor.active is None
            document = p.read_document(scene)
            sketch_id = document.sketches[0].id
            assert len(document.sketches) == 1 and len(document.features) == 0
            event("Z", ctrl=True)
        elif step == 15:
            assert len(p.read_document(scene).sketches) == 0, "One undo did not cancel the entire confirmed interaction"
            event("Z", ctrl=True, shift=True)
        elif step == 16:
            assert len(p.read_document(scene).sketches) == 1
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.blendercad.extrude_sketch(sketch_id=sketch_id, depth_mm=5) == {"FINISHED"}
            document = p.read_document(scene)
            body_id = document.features[0].body_id
            assert math.isclose(volume(), 3000, rel_tol=1e-6), volume()
            baseline = scene.blendercad_document_snapshot
            host = bpy.context.active_object
            token, mesh = p.current_packet(scene, host).token, p.display_for(scene, host).data.as_pointer()
            invoke(sketch_id)
        elif step == 17:
            # Dimension label is offset from the bottom edge by the overlay.
            x, y = location((15, 0))
            event("LEFTMOUSE", x=x + 18, y=y + 17)
        elif step == 18:
            assert editor.active.constraint, "Dimension label was not selectable"
            drag_start = cursor
            event("G")
        elif step == 19:
            bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=drag_start[0] + 40, y=drag_start[1])
        elif step == 20:
            assert editor.active.drag is not None and not editor.active.session.error
            draft = editor.active.session.document.get_sketch(sketch_id)
            assert any(item.value > 30.1 for item in draft.constraints if item.kind == addon._native.SketchConstraintKind.LENGTH)
            assert scene.blendercad_document_snapshot == baseline
            host = bpy.context.active_object
            assert p.current_packet(scene, host).token == token and p.display_for(scene, host).data.as_pointer() == mesh
            event("ESC")
        elif step == 21:
            assert editor.active.drag is None
            assert editor.active.session.document.to_snapshot() == p.read_document(scene).to_snapshot()
            event("ESC")
        elif step == 22:
            assert editor.active is None and scene.blendercad_document_snapshot == baseline
            assert math.isclose(volume(), 3000, rel_tol=1e-6)
            # An arbitrary cursor plane can draw circles and lines without
            # publishing their pending definitions or temporary point guides.
            scene.cursor.rotation_euler = Euler((0.2, 0.3, 0.4))
            host = bpy.context.active_object
            host.location, host.rotation_euler = (0.2, -0.1, 0.05), Euler((0.15, -0.25, 0.35))
            bpy.context.view_layer.update()
            invoke(plane="CURSOR")
        elif step == 23:
            event("C")
        elif step == 24:
            click((5, 5))
        elif step == 25:
            click((7, 5))
        elif step == 26:
            assert len(editor.active.session.display_problem.entities) == 1
            event("S")
        elif step == 27:
            click((5, 5))
        elif step == 28:
            event("G")
        elif step == 29:
            x, y = location((8, 7))
            bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
        elif step == 30:
            circle = editor.active.session.display_problem.entities[0].geometry
            assert circle.center.x > 7 and circle.center.y > 6, (circle.center.x, circle.center.y)
            assert len(editor.active.session.document.get_sketch(editor.active.session.sketch_id).constraints) == 0
            event("ESC")
        elif step == 31:
            event("L")
        elif step == 32:
            click((0, 0))
        elif step == 33:
            click((10, 0))
        elif step == 34:
            assert len(editor.active.session.display_problem.entities) == 2
            event("RIGHTMOUSE")
        elif step == 35:
            click((5, 0))
        elif step == 36:
            event("H")
        elif step == 37:
            selected = next(item for item in editor.active.session.document.get_sketch(editor.active.session.sketch_id).constraints
                            if item.id == editor.active.constraint)
            assert selected.kind == addon._native.SketchConstraintKind.HORIZONTAL
            event("TAB")
        elif step == 38:
            selected = next(item for item in editor.active.session.document.get_sketch(editor.active.session.sketch_id).constraints
                            if item.id == editor.active.constraint)
            assert selected.kind == addon._native.SketchConstraintKind.COINCIDENT
            event("TAB", shift=True)
        elif step == 39:
            event("X")
        elif step == 40:
            raw = editor.active.session.document.get_sketch(editor.active.session.sketch_id)
            assert len(raw.entities) == 2 and len(raw.constraints) == 1, "Constraint deletion removed geometry or retained the selected constraint"
            event("ESC")
        elif step == 41:
            assert editor.active is None and scene.blendercad_document_snapshot == baseline
            assert math.isclose(volume(), 3000, rel_tol=1e-6)
            assert not sketch_view.last_error, sketch_view.last_error
            source_placement = bpy.context.active_object.matrix_world.copy()
            scene.cursor.location = source_placement @ Vector((0.01, 0.01, 0))
            invoke()
        elif step == 42:
            assert editor.active.placement == source_placement
            event("R")
        elif step == 43:
            click((0, 0))
        elif step == 44:
            click((20, 10))
        elif step == 45:
            placed_sketch = editor.active.session.sketch_id
            event("RET")
        elif step == 46:
            assert editor.active is None
            invoke(placed_sketch)
        elif step == 47:
            assert editor.active.placement == source_placement, "Unused sketch changed frame on reopening"
            event("ESC")
        elif step == 48:
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.blendercad.extrude_sketch(sketch_id=placed_sketch, depth_mm=2) == {"FINISHED"}
            host = bpy.context.active_object
            # Assigning matrix_world decomposes/rebuilds Blender float rotation
            # fields. Compare that presentation round trip at float precision;
            # native geometry and the world-corner budget below remain guarded.
            assert np.allclose(host.matrix_world, source_placement, rtol=0, atol=1e-7), "Extruding a sketch changed its displayed placement"
            problem = p.read_document(scene).evaluated_sketch(placed_sketch)
            addon._native.new_sketch_solver().solve(problem)
            vertices = [host.matrix_world @ vertex.co for vertex in p.display_for(scene, host).data.vertices]
            for entity in problem.entities:
                uv = entity.geometry.start.x, entity.geometry.start.y
                expected = sketch_view.world_point(problem.plane, uv, source_placement, p.unit_scale(scene))
                assert min((point - expected).length for point in vertices) <= 1e-6, "Solid did not appear where the profile was drawn"
            baseline = scene.blendercad_document_snapshot
            invoke()
        elif step == 49:
            area.type = "TEXT_EDITOR"
            bpy.context.window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=cursor[0], y=cursor[1])
        elif step == 50:
            assert editor.active is None and scene.blendercad_document_snapshot == baseline, "Replaced viewport retained its modal owner or changed intent"
            area.type = "VIEW_3D"
            region = next(item for item in area.regions if item.type == "WINDOW")
            rejected_entry()
            with bpy.context.temp_override(area=area, region=region):
                assert bpy.ops.blendercad.add_rectangle_solid(width_mm=0.02, height_mm=0.01, depth_mm=0.005) == {"FINISHED"}
            document = p.read_document(scene)
            feature = next(item for item in document.features if item.body_id == bpy.context.active_object[p.BODY])
            baseline = scene.blendercad_document_snapshot
            invoke(feature.sketch_id)
        elif step == 51:
            assert area.spaces.active.region_3d.view_distance < 0.001, "Small sketch inherited an uneditable fixed millimetre-scale view floor"
            click((0.01, 0))
        elif step == 52:
            assert editor.active.selected and editor.active.selected[0].element == addon._native.SketchElement.GEOMETRY
            event("ESC")
        elif step == 53:
            assert editor.active is None and scene.blendercad_document_snapshot == baseline
            (options.output_dir / "result.json").write_text(json.dumps({"passed": True,
                "summary": "Interactive sketch drawing, constraints, GPU preview, cancel, extrusion and one-step undo passed",
                "sketch_draw_count": sketch_view.draw_count, "tools": ["LINE", "CIRCLE", "RECTANGLE"],
                "dimension_input": True, "dimension_drag_cancel": True, "point_drag": True,
                "one_step_undo": True, "revision_guard": True, "placed_sketch": True,
                "viewport_replacement_cancel": True, "small_sketch_picking": True}))
            bpy.ops.wm.quit_blender()
            return None
        step += 1
        return 0.35
    except Exception:
        error = traceback.format_exc()
        print(error, flush=True)
        (options.output_dir / "result.json").write_text(json.dumps({"passed": False, "step": step, "error": error}))
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=4)
