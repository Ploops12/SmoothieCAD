"""Packaged sketch-dimension bindings, editing, duplication and reconstruction."""

import argparse
import importlib
import math
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-7), (actual, expected)


with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, presentation as p
        n = addon._native
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        assert bpy.ops.blendercad.parameter(name="width", value=20, create=True) == {"FINISHED"}
        assert bpy.ops.blendercad.parameter(name="height", create=True, use_expression=True,
                                           expression="width / 2") == {"FINISHED"}
        assert bpy.ops.blendercad.parameter(name="angle", value=30, create=True, unit="deg") == {"FINISHED"}
        assert bpy.ops.blendercad.add_rectangle_solid(width_mm=999, height_mm=888, depth_mm=5,
                                                     width_parameter="width", height_parameter="height") == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        host = bpy.context.active_object
        body = host[p.BODY]
        feature = next(item for item in document.features if item.body_id == body)
        sketch_id = feature.sketch_id
        problem = document.get_sketch(sketch_id)
        width_constraint = next(item.id for item in problem.constraints if item.parameter == "width")
        height_constraint = next(item.id for item in problem.constraints if item.parameter == "height")
        assert all(item.value == 0 for item in problem.constraints if item.parameter is not None)
        close(problem.entities[0].geometry.end.x, 20)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 1000)
        reference_id = document.add_reference("Profile cap", n.PersistentReference(
            body, n.TopologyKind.FACE, n.ReferenceAnchor(feature.id, "cap:start")))
        commands.commit(scene, document)
        assert bpy.ops.blendercad.parameter(name="width", value=30) == {"FINISHED"}
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 2250)
        close(p.runtime(scene).evaluator.measure_reference(document, document.get_reference(reference_id).target).area_mm2, 450)
        assert document.get_sketch(sketch_id).entities[0].geometry.end.x == 20, "Recompute rewrote saved sketch guesses"

        # Numeric and named edits use the same command and switch definition
        # explicitly; a literal does not secretly retain the old width driver.
        assert bpy.ops.blendercad.set_sketch_dimension(sketch_id=sketch_id, constraint_id=width_constraint,
                                                      value_mm=24) == {"FINISHED"}
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 1800)
        assert next(item for item in document.get_sketch(sketch_id).constraints if item.id == width_constraint).parameter is None
        assert bpy.ops.blendercad.set_sketch_dimension(sketch_id=sketch_id, constraint_id=width_constraint,
                                                      parameter="width") == {"FINISHED"}
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 2250)
        baseline = scene.blendercad_document_snapshot
        mesh = p.display_for(scene, host).data
        evaluator = p.runtime(scene).evaluator
        token = p.current_packet(scene, host).token
        geometric = next(item.id for item in document.get_sketch(sketch_id).constraints if item.kind == n.SketchConstraintKind.HORIZONTAL)
        for constraint_id, value in ((width_constraint, "missing"), (width_constraint, "angle"),
                                     (width_constraint, -2), (geometric, "width")):
            try:
                commands.set_sketch_dimension(bpy.context, sketch_id, constraint_id, value)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid sketch binding was published")
            assert scene.blendercad_document_snapshot == baseline
            assert p.runtime(scene).evaluator is evaluator
            assert p.current_packet(scene, host).token == token
            assert p.display_for(scene, host).data == mesh
        try:
            result = bpy.ops.blendercad.set_sketch_dimension(sketch_id=sketch_id, constraint_id=width_constraint,
                                                           parameter="angle")
        except RuntimeError:
            pass  # Blender may raise for an operator ERROR report in background.
        else:
            assert result == {"CANCELLED"}
        assert scene.blendercad_document_snapshot == baseline
        assert p.current_packet(scene, host).token == token

        # Duplication preserves document-level drivers but remaps every saved
        # profile/entity/constraint identity; it is not a competing definition.
        original_ids = {item.id for item in document.get_sketch(sketch_id).constraints}
        assert bpy.ops.blendercad.duplicate_solid() == {"FINISHED"}
        clone = bpy.context.active_object
        clone_body = clone[p.BODY]
        document = p.read_document(scene)
        clone_feature = next(item for item in document.features if item.body_id == clone_body)
        clone_problem = document.get_sketch(clone_feature.sketch_id)
        assert clone_feature.sketch_id != sketch_id
        assert original_ids.isdisjoint(item.id for item in clone_problem.constraints)
        assert {item.parameter for item in clone_problem.constraints if item.parameter} == {"width", "height"}
        assert bpy.ops.blendercad.parameter(name="width", value=40) == {"FINISHED"}
        document = p.read_document(scene)
        for current_body in (body, clone_body):
            close(p.runtime(scene).evaluator.volume_mm3(document, current_body), 4000)
        assert bpy.ops.blendercad.parameter(name="width", value=0) == {"FINISHED"}
        document = p.read_document(scene)
        assert all(item.state == n.EvaluationState.FAILED for item in p.runtime(scene).report.features)
        for current_host in p.hosts(scene, document.id):
            assert not p.display_for(scene, current_host)["blendercad_current"]
            try:
                p.current_packet(scene, current_host)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid bound dimension exposed current picking data")
        assert bpy.ops.blendercad.parameter(name="width", value=35) == {"FINISHED"}
        document = p.read_document(scene)
        for current_body in (body, clone_body):
            close(p.runtime(scene).evaluator.volume_mm3(document, current_body), 35 * 17.5 * 5)

        filepath = str(Path(directory) / "sketch-parameters.blend")
        baseline = scene.blendercad_document_snapshot
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert scene.blendercad_document_snapshot == baseline
        assert next(item for item in document.get_sketch(sketch_id).constraints if item.id == width_constraint).parameter == "width"
        assert next(item for item in document.get_sketch(sketch_id).constraints if item.id == height_constraint).parameter == "height"
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 35 * 17.5 * 5)
        assert p.runtime(scene).evaluator.resolve_reference(document, document.get_reference(reference_id).target).state == n.ReferenceState.RESOLVED
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="Bound sketch dimension baseline")
        assert bpy.ops.blendercad.set_sketch_dimension(sketch_id=sketch_id, constraint_id=width_constraint,
                                                      value_mm=28) == {"FINISHED"}
        bpy.ops.ed.undo_push(message="Literal sketch dimension edit")
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 28 * 17.5 * 5)
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert next(item for item in document.get_sketch(sketch_id).constraints if item.id == width_constraint).parameter == "width"
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 35 * 17.5 * 5)
        assert bpy.ops.ed.redo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert next(item for item in document.get_sketch(sketch_id).constraints if item.id == width_constraint).parameter is None
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 28 * 17.5 * 5)
        print("Blender sketch parameters: named rectangle dimensions, numeric/bound edits, atomic failures, duplication, stable references, stale/recovery, save/reopen and undo/redo passed")
    finally:
        addon.unregister()
