"""Verify the packaged addon, scene storage and schema protection in Blender."""

import argparse
import importlib
import json
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
        assert restored.to_snapshot() == document.to_snapshot()
        reloaded_sketch = restored.get_sketch(sketch_id)
        assert reloaded_sketch.entities[0].id == circle.id
        assert reloaded_sketch.plane.origin.z == 30
        assert n.new_sketch_solver().solve(reloaded_sketch).state == n.SketchSolveState.FULLY_CONSTRAINED
        assert restored.to_snapshot() == document.to_snapshot(), "Solving a copy changed the document"
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
        assert migrated.schema_version == 2 and migrated.sketches == []
        assert migrated.revision == 7 and migrated.id == legacy["document_id"]
        assert bpy.context.scene.blendercad_document_snapshot == legacy_serialized, "Load silently rewrote a legacy snapshot"
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
        print("Blender foundation: package import, sketch save/reopen, legacy migration, and schema protection passed")
    finally:
        addon.unregister()
