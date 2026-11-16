"""Public, atomic STEP export of current analytic Solid geometry."""

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
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8), (actual, expected)


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(root / "installed addon")
    sys.path.insert(0, str(root / "installed addon"))
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, operators, presentation as p, sketch_commands, sketch_editor
        n = addon._native
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        assert bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5) == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        feature = document.features[0].id
        body = document.get_feature(feature).body_id
        host = bpy.context.active_object
        host.location = (0.123, -0.456, 0.789)
        host.rotation_euler = (0.3, -0.2, 0.7)
        bpy.context.view_layer.update()
        destination = root / "part with spaces.stp"
        kernel = n.new_geometry_adapter()

        def owner():
            return next(item for item in p.hosts(scene, p.runtime(scene).document.id) if item[p.BODY] == body)

        def select_only(*objects):
            for obj in bpy.context.selected_objects:
                obj.select_set(False)
            for obj in objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = objects[0] if objects else None

        def fingerprint():
            state = p.runtime(scene)
            current_host = owner()
            display = p.display_for(scene, current_host)
            packet = state.packets.get(body)
            return (scene.blendercad_document_snapshot, id(state), id(state.evaluator),
                    state.document.revision, state.report.revision,
                    display.as_pointer(), display.data.as_pointer(), display.get(p.TOKEN),
                    tuple(tuple(vertex.co) for vertex in display.data.vertices),
                    packet.token if packet else None,
                    tuple(tuple(row) for row in current_host.matrix_world),
                    tuple(sorted(obj.as_pointer() for obj in bpy.context.selected_objects)),
                    bpy.context.active_object.as_pointer() if bpy.context.active_object else None,
                    tuple((item.bl_rna.identifier, item.as_pointer()) for item in bpy.context.window_manager.operators),
                    len(bpy.data.objects), len(bpy.data.meshes), scene.unit_settings.scale_length)

        def no_temporary_files():
            assert not list(root.glob(".blendercad-step-*")), "Export left a sibling temporary file"

        def readback(path, expected_volume=3000, depth=5):
            shape = kernel.read_step(str(path))
            try:
                assert kernel.is_valid(shape)
                close(kernel.volume_mm3(shape), expected_volume)
                vertices = [n.geometry_measure(kernel, shape, n.TopologyElement(n.TopologyKind.VERTEX, index)).center_mm
                            for index in range(n.geometry_topology(kernel, shape).counts[2])]
                for values, high in (([point.x for point in vertices], 30),
                                     ([point.y for point in vertices], 20),
                                     ([point.z for point in vertices], depth)):
                    close(min(values), 0)
                    close(max(values), high)
            finally:
                kernel.release(shape)
            compact = "".join(path.read_text().split())
            assert "SI_UNIT(.MILLI.,.METRE.)" in compact, "STEP did not declare millimetre length units"

        def rejected(operation, message=None, unchanged=True):
            before = fingerprint()
            saved = destination.read_bytes()
            try:
                result = operation()
            except (ValueError, RuntimeError, OSError, TypeError) as error:
                if message is not None:
                    assert message in str(error), str(error)
            else:
                assert result == {"CANCELLED"}, "Rejected export unexpectedly succeeded"
            if unchanged:
                assert fingerprint() == before, "Export changed document, runtime, display, selection or operator history"
            assert destination.read_bytes() == saved, "Rejected export replaced the existing destination"
            no_temporary_files()

        assert not ({"REGISTER", "UNDO"} & operators.BLENDERCAD_OT_export_step.bl_options)
        assert bpy.ops.blendercad.export_step.poll()
        destination.write_bytes(b"existing destination")
        before = fingerprint()
        assert bpy.ops.blendercad.export_step(filepath=str(destination)) == {"FINISHED"}
        assert fingerprint() == before
        no_temporary_files()
        readback(destination)
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 3000)

        # Scene display units can invalidate picking without changing the
        # current part-local analytic source. Export must not force recompute.
        scene.unit_settings.scale_length = 0.01
        before = fingerprint()
        assert bpy.ops.blendercad.export_step(filepath=str(destination)) == {"FINISHED"}
        assert fingerprint() == before
        readback(destination)
        scene.unit_settings.scale_length = 1

        # Another independent body's failure must not prevent valid current
        # geometry from being exported from the selected body.
        assert bpy.ops.blendercad.add_rectangle_solid(width_mm=4, height_mm=3, depth_mm=2) == {"FINISHED"}
        other = bpy.context.active_object
        other_feature = p.read_document(scene).features[-1].id
        commands.set_depth(bpy.context, other_feature, 0)
        select_only(owner())
        assert bpy.ops.blendercad.export_step.poll()
        before = fingerprint()
        assert bpy.ops.blendercad.export_step(filepath=str(destination)) == {"FINISHED"}
        assert fingerprint() == before
        readback(destination)

        destination.write_bytes(b"preserve this existing destination")
        select_only()
        assert not bpy.ops.blendercad.export_step.poll()
        rejected(lambda: commands.export_step(bpy.context, destination))
        select_only(owner(), other)
        rejected(lambda: commands.export_step(bpy.context, destination), "exactly one")
        bpy.ops.mesh.primitive_cube_add(size=0.01)
        ordinary = bpy.context.active_object
        select_only(owner(), ordinary)
        rejected(lambda: commands.export_step(bpy.context, destination), "CAD Solid")
        select_only(ordinary)
        rejected(lambda: commands.export_step(bpy.context, destination), "CAD Solid")
        bpy.data.objects.remove(ordinary, do_unlink=True)
        select_only(owner())
        host = owner()
        host.scale.x = 2
        bpy.context.view_layer.update()
        assert not bpy.ops.blendercad.export_step.poll()
        rejected(lambda: commands.export_step(bpy.context, destination), commands.capabilities.INCOMPATIBLE_MESSAGE)
        host.scale.x = 1
        bpy.context.view_layer.update()

        draft = sketch_commands.Session(p.read_document(scene), name="Unconfirmed export draft")
        draft.add_line((1, 2), (4, 2))
        sketch_editor.active = draft
        try:
            assert not bpy.ops.blendercad.export_step.poll()
            rejected(lambda: commands.export_step(bpy.context, destination), "Finish or cancel")
        finally:
            sketch_editor.active = None

        saved_snapshot = scene.blendercad_document_snapshot
        changed = p.read_document(scene)
        changed.set_feature_depth(feature, 6)
        scene.blendercad_document_snapshot = p.serialize(changed)
        assert not bpy.ops.blendercad.export_step.poll()
        rejected(lambda: commands.export_step(bpy.context, destination), "recompute")
        scene.blendercad_document_snapshot = saved_snapshot
        p.sync(scene)
        commands.set_depth(bpy.context, feature, 0)
        assert not bpy.ops.blendercad.export_step.poll()
        rejected(lambda: commands.export_step(bpy.context, destination), "current valid")
        commands.set_depth(bpy.context, feature, 5)
        rejected(lambda: commands.export_step(bpy.context, root / "missing directory" / "part.step"), "does not exist")
        rejected(lambda: commands.export_step(bpy.context, root), "not a directory")

        original_native = p._native
        p._native = None
        try:
            assert not bpy.ops.blendercad.export_step.poll()
            rejected(lambda: commands.export_step(bpy.context, destination), "Native module")
        finally:
            p._native = original_native

        # The failure seam wraps, rather than mocks, a real native evaluator.
        # Positive exports/readbacks above always use the actual OCCT writer.
        state = p.runtime(scene)
        native_evaluator = state.evaluator

        class WriterProxy:
            def __init__(self, hook):
                self.hook = hook

            def __getattr__(self, name):
                return getattr(native_evaluator, name)

            def write_step(self, document, body_id, filepath):
                return self.hook(document, body_id, filepath)

        def writer_failure(document, body_id, filepath):
            Path(filepath).write_bytes(b"partial native output")
            raise RuntimeError("Injected STEP writer failure")

        state.evaluator = WriterProxy(writer_failure)
        try:
            rejected(lambda: bpy.ops.blendercad.export_step(filepath=str(destination)), "Injected STEP writer failure")
        finally:
            state.evaluator = native_evaluator

        original_replace = commands.os.replace
        replacement_attempts = []

        def replacement_failure(source, target):
            replacement_attempts.append((Path(source), Path(target)))
            assert Path(source).parent == destination.parent == Path(target).parent
            assert Path(source).stat().st_size > 0
            raise OSError("Injected STEP publication failure")

        commands.os.replace = replacement_failure
        try:
            rejected(lambda: bpy.ops.blendercad.export_step(filepath=str(destination)), "Injected STEP publication failure")
        finally:
            commands.os.replace = original_replace
        assert len(replacement_attempts) == 1

        saved_snapshot = scene.blendercad_document_snapshot

        def changed_during_write(document, body_id, filepath):
            native_evaluator.write_step(document, body_id, filepath)
            document.set_feature_depth(feature, 6)
            scene.blendercad_document_snapshot = p.serialize(document)

        state.evaluator = WriterProxy(changed_during_write)
        try:
            rejected(lambda: commands.export_step(bpy.context, destination), "changed during", unchanged=False)
            assert scene.blendercad_document_snapshot != saved_snapshot
        finally:
            state.evaluator = native_evaluator
            scene.blendercad_document_snapshot = saved_snapshot
            p.restore_runtime(scene, state)

        # Export is not a scene undo step: the next undo must restore the prior
        # modelling edit, while the already-published external file remains.
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="STEP export model baseline")
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=6) == {"FINISHED"}
        bpy.ops.ed.undo_push(message="STEP export changed model")
        assert bpy.ops.blendercad.export_step(filepath=str(destination)) == {"FINISHED"}
        published = destination.read_bytes()
        readback(destination, 3600, 6)
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        assert p.read_document(scene).get_feature(feature).depth == 5
        assert destination.read_bytes() == published
        readback(destination, 3600, 6)
        print("STEP export: public operator, part-local mm B-rep, immutable scene/cache, stale/draft/selection guards, atomic failures and non-modelling undo passed")
    finally:
        addon.unregister()
