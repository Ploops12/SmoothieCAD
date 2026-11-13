"""Verify the packaged addon, scene storage and schema protection in Blender."""

import argparse
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])

with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        assert bpy.app.version[:3] == (5, 2, 3), bpy.app.version
        assert addon._native is not None, "Packaged native extension did not load"
        assert bpy.ops.blendercad.new_document.poll()
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        original = bpy.context.scene.blendercad_document_snapshot
        assert not bpy.ops.blendercad.new_document.poll(), "Existing document could be overwritten"
        document = addon._native.Document.from_snapshot(json.loads(original))
        document.add_parameter("width", 24, "mm")
        n = addon._native
        sketch = n.SketchProblem()
        sketch.plane.origin = n.Vector3(10, 20, 30)
        sketch.plane.x_axis = n.Vector3(0, 1, 0)
        sketch.plane.y_axis = n.Vector3(0, 0, 1)
        circle = n.SketchEntity(n.Circle2(n.Point2(1, 2), 2))
        center = n.SketchReference(circle.id, n.SketchElement.CIRCLE_CENTER)
        origin = n.SketchReference()
        whole = n.SketchReference(circle.id, n.SketchElement.GEOMETRY)
        sketch.entities = [circle]
        sketch.constraints = [n.SketchConstraint(n.SketchConstraintKind.DISTANCE_X, [origin, center], 2),
                              n.SketchConstraint(n.SketchConstraintKind.DISTANCE_Y, [origin, center], -4),
                              n.SketchConstraint(n.SketchConstraintKind.RADIUS, [whole], 3)]
        report = n.new_sketch_solver().solve(sketch)
        assert report.state == n.SketchSolveState.FULLY_CONSTRAINED, report.diagnostics
        sketch_id = document.add_sketch("Mounting hole", sketch)
        document.add_parameter("thickness", 4, "mm")
        extrusion = document.add_extrusion("Cylinder", sketch_id, "thickness")
        body_id = document.get_feature(extrusion).body_id
        reference_id = document.add_reference("Cylinder start face", n.PersistentReference(
            body_id, n.TopologyKind.FACE, n.ReferenceAnchor(extrusion, "cap:start")))
        evaluator = n.FeatureEvaluator()
        assert evaluator.evaluate(document).bodies[0].state == n.EvaluationState.UP_TO_DATE
        reference_resolution = evaluator.resolve_reference(document, document.get_reference(reference_id).target)
        assert reference_resolution.state == n.ReferenceState.RESOLVED
        assert len(reference_resolution.candidates) == 1
        assert reference_resolution.candidates[0].kind == n.TopologyKind.FACE
        assert math.isclose(evaluator.volume_mm3(document, body_id), math.pi * 9 * 4, rel_tol=1e-10)
        bpy.context.scene.blendercad_document_snapshot = json.dumps(document.to_snapshot())
        stored = bpy.context.scene.blendercad_document_snapshot
        filepath = str(Path(directory) / "foundation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        assert bpy.context.scene.blendercad_document_snapshot == stored
        restored = addon._native.Document.from_snapshot(json.loads(stored))
        assert restored.id == document.id
        assert restored.parameters[0].value == 24
        assert restored.sketches[0].id == sketch_id
        assert restored.features[0].id == extrusion
        assert restored.features[0].body_id == body_id
        assert restored.references[0].id == reference_id
        assert restored.references[0].target.anchor.feature_id == extrusion
        assert restored.references[0].target.anchor.key == "cap:start"
        assert restored.to_snapshot() == document.to_snapshot()
        reloaded_sketch = restored.get_sketch(sketch_id)
        assert reloaded_sketch.entities[0].id == circle.id
        assert reloaded_sketch.plane.origin.z == 30
        assert n.new_sketch_solver().solve(reloaded_sketch).state == n.SketchSolveState.FULLY_CONSTRAINED
        assert restored.to_snapshot() == document.to_snapshot(), "Solving a copy changed the document"
        rebuilt = n.FeatureEvaluator()
        restored_report = rebuilt.evaluate(restored)
        assert restored_report.bodies[0].state == n.EvaluationState.UP_TO_DATE
        assert restored_report.references[0].reference_id == reference_id
        assert restored_report.references[0].resolution.state == n.ReferenceState.RESOLVED
        restored_resolution = rebuilt.resolve_reference(restored, restored.get_reference(reference_id).target)
        assert restored_resolution.state == n.ReferenceState.RESOLVED
        assert len(restored_resolution.candidates) == 1
        assert restored_resolution.candidates[0].kind == n.TopologyKind.FACE
        assert math.isclose(rebuilt.volume_mm3(restored, body_id), math.pi * 9 * 4, rel_tol=1e-10)
        addon.unregister()
        addon.register()
        assert bpy.context.scene.blendercad_document_snapshot == stored
        legacy = {"schema_version": 1, "document_id": n.new_document_id(), "revision": 7,
                  "geometric_tolerance_mm": 0.001, "parameters": []}
        legacy_serialized = json.dumps(legacy)
        bpy.context.scene.blendercad_document_snapshot = legacy_serialized
        legacy_filepath = str(Path(directory) / "legacy.blend")
        bpy.ops.wm.save_as_mainfile(filepath=legacy_filepath)
        bpy.ops.wm.open_mainfile(filepath=legacy_filepath)
        migrated = n.Document.from_snapshot(json.loads(bpy.context.scene.blendercad_document_snapshot))
        assert migrated.schema_version == 5 and migrated.sketches == [] and migrated.features == [] and migrated.references == []
        assert migrated.revision == 7 and migrated.id == legacy["document_id"]
        assert bpy.context.scene.blendercad_document_snapshot == legacy_serialized, "Load silently rewrote a legacy snapshot"
        legacy_two = document.to_snapshot()
        legacy_two["schema_version"] = 2
        del legacy_two["features"]
        del legacy_two["rollback_before"]
        del legacy_two["references"]
        legacy_two_serialized = json.dumps(legacy_two)
        bpy.context.scene.blendercad_document_snapshot = legacy_two_serialized
        legacy_two_filepath = str(Path(directory) / "legacy-two.blend")
        bpy.ops.wm.save_as_mainfile(filepath=legacy_two_filepath)
        bpy.ops.wm.open_mainfile(filepath=legacy_two_filepath)
        migrated_two = n.Document.from_snapshot(json.loads(bpy.context.scene.blendercad_document_snapshot))
        assert migrated_two.schema_version == 5 and migrated_two.features == [] and migrated_two.references == []
        assert migrated_two.sketches[0].id == sketch_id
        assert bpy.context.scene.blendercad_document_snapshot == legacy_two_serialized
        legacy_three = document.to_snapshot()
        legacy_three["schema_version"] = 3
        del legacy_three["references"]
        legacy_three_serialized = json.dumps(legacy_three)
        bpy.context.scene.blendercad_document_snapshot = legacy_three_serialized
        legacy_three_filepath = str(Path(directory) / "legacy-three.blend")
        bpy.ops.wm.save_as_mainfile(filepath=legacy_three_filepath)
        bpy.ops.wm.open_mainfile(filepath=legacy_three_filepath)
        migrated_three = n.Document.from_snapshot(json.loads(bpy.context.scene.blendercad_document_snapshot))
        assert migrated_three.schema_version == 5 and migrated_three.references == []
        assert migrated_three.features[0].id == extrusion and migrated_three.rollback_before == document.rollback_before
        assert bpy.context.scene.blendercad_document_snapshot == legacy_three_serialized
        future = document.to_snapshot()
        future["schema_version"] += 1
        serialized = json.dumps(future)
        bpy.context.scene.blendercad_document_snapshot = serialized
        assert not bpy.ops.blendercad.new_document.poll()
        try:
            addon._native.Document.from_snapshot(json.loads(serialized))
        except ValueError:
            pass
        else:
            raise AssertionError("Future document schema was accepted")
        assert bpy.context.scene.blendercad_document_snapshot == serialized
        future_reference = document.to_snapshot()
        future_reference["references"][0]["version"] += 1
        serialized = json.dumps(future_reference)
        bpy.context.scene.blendercad_document_snapshot = serialized
        assert not bpy.ops.blendercad.new_document.poll()
        try:
            n.Document.from_snapshot(json.loads(serialized))
        except ValueError:
            pass
        else:
            raise AssertionError("Future reference version was accepted")
        assert bpy.context.scene.blendercad_document_snapshot == serialized
        future_feature = document.to_snapshot()
        future_feature["features"][0]["version"] += 1
        serialized = json.dumps(future_feature)
        bpy.context.scene.blendercad_document_snapshot = serialized
        assert not bpy.ops.blendercad.new_document.poll()
        try:
            n.Document.from_snapshot(json.loads(serialized))
        except ValueError:
            pass
        else:
            raise AssertionError("Future feature version was accepted")
        assert bpy.context.scene.blendercad_document_snapshot == serialized
        future_sketch = document.to_snapshot()
        future_sketch["sketches"][0]["version"] += 1
        serialized = json.dumps(future_sketch)
        bpy.context.scene.blendercad_document_snapshot = serialized
        assert not bpy.ops.blendercad.new_document.poll()
        try:
            n.Document.from_snapshot(json.loads(serialized))
        except ValueError:
            pass
        else:
            raise AssertionError("Future sketch version was accepted")
        assert bpy.context.scene.blendercad_document_snapshot == serialized
        print("Blender foundation: package import, saved feature/reference identities, native rebuild, schemas 1/2/3 migration, and future-version protection passed")
    finally:
        addon.unregister()
