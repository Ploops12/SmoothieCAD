"""Packaged face-attached sketches through shared Blender CAD commands."""

import argparse
import copy
import importlib
import math
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import bpy
from mathutils import Vector

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def support_of(document, sketch):
    return next(item.support for item in document.sketches if item.id == sketch)


def rows(matrix):
    return tuple(tuple(row) for row in matrix)


def vector(value):
    return Vector((value.x, value.y, value.z))


def fingerprint():
    scene = bpy.context.scene
    state = p.runtime(scene)
    return (scene.blendercad_document_snapshot, id(state), id(state.evaluator),
            tuple((host[p.BODY], p.current_packet(scene, host).token,
                   p.display_for(scene, host).data.as_pointer(), rows(host.matrix_world))
                  for host in p.hosts(scene, p.read_document(scene).id)),
            tuple(sorted(obj.as_pointer() for obj in bpy.context.selected_objects)),
            bpy.context.active_object.as_pointer() if bpy.context.active_object else None,
            len(bpy.data.objects), len(bpy.data.meshes))


class BlenderSketchSupportChecks(unittest.TestCase):
    def setUp(self):
        scene = bpy.data.scenes.new("Attached sketch regression")
        bpy.context.window.scene = scene
        self.assertEqual(bpy.ops.blendercad.new_document(), {"FINISHED"})
        commands.set_parameter(bpy.context, "thickness", 10, "mm")
        commands.set_parameter(bpy.context, "hole_radius", 2, "mm")
        commands.set_parameter(bpy.context, "pocket_depth", 3, "mm")
        self.base = commands.add_rectangle(bpy.context, 30, 20, 10)
        commands.set_depth(bpy.context, self.base, "thickness")
        document = p.read_document(scene)
        self.body = document.get_feature(self.base).body_id
        host = self.host()
        host.location, host.rotation_euler = (0.15, -0.12, 0.08), (0.3, -0.2, 0.4)
        bpy.context.view_layer.update()
        self.top = n.PersistentReference(self.body, n.TopologyKind.FACE, n.ReferenceAnchor(self.base, "cap:end"))
        self.select_target(self.top)

    def close(self, actual, expected):
        self.assertTrue(math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-8), (actual, expected))

    def host(self, body=None):
        scene = bpy.context.scene
        return next(host for host in p.hosts(scene, p.read_document(scene).id) if host[p.BODY] == (body or self.body))

    def select_host(self, body=None):
        host = self.host(body)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        return host

    def select_target(self, target):
        scene = bpy.context.scene
        document = p.read_document(scene)
        resolution = p.runtime(scene).evaluator.resolve_reference(document, target)
        self.assertEqual(resolution.state, n.ReferenceState.RESOLVED)
        self.assertEqual(len(resolution.candidates), 1)
        element = resolution.candidates[0]
        host = self.select_host(target.body_id)
        packet = p.current_packet(scene, host)
        pick = p.resolve(scene, host, element.kind.name, element.index, packet.token)
        p.apply_pick(bpy.context, host, pick)
        return pick

    def volume(self, expected, body=None):
        scene = bpy.context.scene
        actual = p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body or self.body)
        self.close(actual, expected)
        return actual

    def draft(self, reversed=True):
        scene = bpy.context.scene
        support = commands.selected_face_support(bpy.context, reversed=reversed)
        self.assertEqual(support.feature_id, self.base)
        self.assertEqual(support.face.body_id, self.body)
        frame = p.runtime(scene).evaluator.support_plane(p.read_document(scene), support)
        world_center = Vector((15, 10, 10))
        offset = world_center - vector(frame.origin)
        center = offset.dot(vector(frame.x_axis)), offset.dot(vector(frame.y_axis))
        draft = Session(p.read_document(scene), name="Attached pocket", support=support,
                        evaluator=p.runtime(scene).evaluator)
        entity = draft.add_circle(center, 2)
        self.radius_constraint = draft.add_constraint(n.SketchConstraintKind.RADIUS,
            [n.SketchReference(entity, n.SketchElement.GEOMETRY)], parameter="hole_radius")
        point = n.SketchReference(entity, n.SketchElement.CIRCLE_CENTER)
        draft.add_constraint(n.SketchConstraintKind.DISTANCE_X, [n.SketchReference(), point], center[0])
        draft.add_constraint(n.SketchConstraintKind.DISTANCE_Y, [n.SketchReference(), point], center[1])
        self.assertEqual(draft.report.state, n.SketchSolveState.FULLY_CONSTRAINED, draft.error)
        return draft

    def pocket(self):
        draft = self.draft()
        commands.commit(bpy.context.scene, draft.document)
        self.sketch = draft.sketch_id
        self.cut = commands.extrude_sketch(bpy.context, self.sketch, "pocket_depth", input_feature_id=self.base)
        self.volume(6000 - math.pi * 4 * 3)
        return self.sketch

    def test_detached_solver_uses_current_support_and_shared_driver_without_scene_changes(self):
        scene = bpy.context.scene
        source = p.read_document(scene)
        before = fingerprint()
        draft = self.draft()
        self.assertEqual(fingerprint(), before)
        self.assertTrue(p.runtime(scene).evaluator.matches_document(source))
        self.close(draft.display_problem.plane.origin.z, 10)
        preview = draft.fork()
        preview.drag_dimension(self.radius_constraint, 3)
        self.close(preview.document.length_value("hole_radius"), 3)
        self.close(draft.document.length_value("hole_radius"), 2)
        self.assertEqual(fingerprint(), before)
        host, placement = commands.sketch_placement(bpy.context, draft.document, draft.sketch_id)
        self.assertEqual(host, self.host())
        self.assertEqual(rows(placement), rows(host.matrix_world))
        commands.commit(scene, draft.document)
        sketch = draft.sketch_id
        cut = commands.extrude_sketch(bpy.context, sketch, "pocket_depth", input_feature_id=self.base)
        self.volume(6000 - math.pi * 4 * 3)
        identities = [item.id for item in p.read_document(scene).features]
        commands.set_parameter(bpy.context, "thickness", 16)
        document = p.read_document(scene)
        self.assertEqual([item.id for item in document.features], identities)
        self.assertEqual(document.get_feature(cut).sketch_id, sketch)
        self.close(p.runtime(scene).evaluator.evaluated_sketch(document, sketch).plane.origin.z, 16)
        self.volume(9600 - math.pi * 4 * 3)
        self.assertEqual(document.get_sketch(sketch).entities[0].geometry.radius, 2)

    def test_support_offsets_detach_and_undo_preserve_explicit_frame(self):
        self.pocket()
        scene = bpy.context.scene
        commands.edit_sketch_support(bpy.context, self.sketch, 1, 0, 0, True)
        document = p.read_document(scene)
        self.assertEqual(support_of(document, self.sketch).offset_u_mm, 1)
        self.volume(6000 - math.pi * 4 * 3)
        plane = p.runtime(scene).evaluator.evaluated_sketch(document, self.sketch).plane
        baseline = scene.blendercad_document_snapshot
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="Attached support before detach")
        commands.detach_sketch_support(bpy.context, self.sketch)
        bpy.ops.ed.undo_push(message="Explicitly detached support")
        document = p.read_document(scene)
        self.assertIsNone(support_of(document, self.sketch))
        self.close(document.evaluated_sketch(self.sketch).plane.origin.z, plane.origin.z)
        detached = scene.blendercad_document_snapshot
        self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
        scene = bpy.context.scene
        self.assertEqual(scene.blendercad_document_snapshot, baseline)
        self.assertIsNotNone(support_of(p.read_document(scene), self.sketch))
        self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
        scene = bpy.context.scene
        self.assertEqual(scene.blendercad_document_snapshot, detached)
        commands.set_parameter(bpy.context, "thickness", 16)
        document = p.read_document(scene)
        self.close(p.runtime(scene).evaluator.evaluated_sketch(document, self.sketch).plane.origin.z, 10)
        self.volume(9600 - math.pi * 4 * 3)

    def test_invalid_2d_profile_can_detach_its_valid_support_without_repairing_intent(self):
        self.pocket()
        scene = bpy.context.scene
        good_generation = p.current_packet(scene, self.host()).arrays["geometry_generation"]
        commands.set_parameter(bpy.context, "hole_radius", 0)
        document = p.read_document(scene)
        self.assertEqual(p.runtime(scene).report.features[-1].state, n.EvaluationState.FAILED)
        support = support_of(document, self.sketch)
        plane = p.runtime(scene).evaluator.support_plane(document, support)
        self.close(plane.origin.z, 10)
        with self.assertRaises((ValueError, RuntimeError)):
            p.runtime(scene).evaluator.evaluated_sketch(document, self.sketch)
        failed_definition = next(item for item in document.to_snapshot()["sketches"] if item["id"] == self.sketch)
        commands.detach_sketch_support(bpy.context, self.sketch)
        document = p.read_document(scene)
        self.assertIsNone(support_of(document, self.sketch))
        detached = next(item for item in document.to_snapshot()["sketches"] if item["id"] == self.sketch)
        for field in ("entities", "constraints"):
            self.assertEqual(detached[field], failed_definition[field], "Detach changed failed 2D design intent")
        frozen = document.get_sketch(self.sketch).plane
        for actual, expected in ((frozen.origin, plane.origin), (frozen.x_axis, plane.x_axis), (frozen.y_axis, plane.y_axis)):
            self.assertEqual((actual.x, actual.y, actual.z), (expected.x, expected.y, expected.z))
        self.assertEqual(document.length_value("hole_radius"), 0)
        self.assertEqual(p.runtime(scene).report.features[-1].state, n.EvaluationState.FAILED,
                         "Detaching a valid support silently repaired or hid an invalid profile")
        stale = p.runtime(scene).evaluator.tessellate(document, self.body)
        self.assertFalse(stale["current"])
        self.assertEqual(stale["geometry_generation"], good_generation)
        commands.set_parameter(bpy.context, "hole_radius", 2)
        document = p.read_document(scene)
        self.assertEqual(p.runtime(scene).report.features[-1].state, n.EvaluationState.UP_TO_DATE)
        self.assertIsNone(support_of(document, self.sketch))
        self.close(p.runtime(scene).evaluator.evaluated_sketch(document, self.sketch).plane.origin.z, 10)
        self.volume(6000 - math.pi * 4 * 3)

    def test_failure_keeps_display_and_explicit_support_repair_resumes_history(self):
        self.pocket()
        scene = bpy.context.scene
        document = p.read_document(scene)
        generation = p.current_packet(scene, self.host()).arrays["geometry_generation"]
        broken = support_of(document, self.sketch)
        original_direction = broken.x_direction
        broken.face = n.PersistentReference(self.body, n.TopologyKind.FACE,
            n.ReferenceAnchor(self.base, "missing:repairable:face"))
        document.set_sketch_support(self.sketch, broken)
        commands.commit(scene, document)
        self.assertEqual(p.runtime(scene).report.features[-1].state, n.EvaluationState.FAILED)
        self.assertFalse(p.display_for(scene, self.host())["blendercad_current"])
        self.assertEqual(p.runtime(scene).evaluator.tessellate(p.read_document(scene), self.body)["geometry_generation"], generation)
        snapshot = scene.blendercad_document_snapshot
        with self.assertRaises((ValueError, RuntimeError)):
            commands.export_step(bpy.context, Path(directory) / "failed-support.step")
        self.assertEqual(scene.blendercad_document_snapshot, snapshot)
        evaluator = p.runtime(scene).evaluator
        with self.assertRaises((ValueError, RuntimeError)):
            commands.detach_sketch_support(bpy.context, self.sketch)
        self.assertEqual(scene.blendercad_document_snapshot, snapshot,
                         "Unresolved support was detached using a stale or fabricated frame")
        self.assertIs(p.runtime(scene).evaluator, evaluator)
        commands.rollback_sketch_support(bpy.context, self.sketch)
        self.select_target(self.top)
        commands.repair_sketch_support(bpy.context, self.sketch)
        document = p.read_document(scene)
        self.assertIsNone(document.rollback_before)
        self.assertEqual(document.features[-1].id, self.cut)
        self.assertEqual(support_of(document, self.sketch).face.anchor.key, self.top.anchor.key)
        self.volume(6000 - math.pi * 4 * 3)

        # Repair is not permission to invent a different tangent when the saved
        # direction is parallel to the explicitly selected replacement plane.
        commands.edit_sketch_support(bpy.context, self.sketch, 0, 0, 0, True, n.Vector3(0, 0, 1))
        self.assertEqual(p.runtime(scene).report.features[-1].state, n.EvaluationState.FAILED)
        commands.rollback_sketch_support(bpy.context, self.sketch)
        self.select_target(self.top)
        before = fingerprint()
        with self.assertRaises((ValueError, RuntimeError)):
            commands.repair_sketch_support(bpy.context, self.sketch)
        self.assertEqual(fingerprint(), before)
        commands.edit_sketch_support(bpy.context, self.sketch, 0, 0, 0, True, original_direction)
        commands.rollback(bpy.context)
        self.volume(6000 - math.pi * 4 * 3)

    def test_independent_duplication_remaps_support_and_reconstructs_after_save(self):
        self.pocket()
        scene = bpy.context.scene
        source = p.read_document(scene)
        source_sketch = self.sketch
        source_ids = {item.id for item in source.features}
        original_host = self.host()
        self.select_host()
        self.assertEqual(bpy.ops.blendercad.duplicate_solid(), {"FINISHED"})
        clone_host = bpy.context.active_object
        clone_body = clone_host[p.BODY]
        document = p.read_document(scene)
        cloned = [item for item in document.features if item.body_id == clone_body]
        self.assertEqual([item.type for item in cloned], ["extrusion", "subtractive_extrusion"])
        cloned_sketch = cloned[-1].sketch_id
        support = support_of(document, cloned_sketch)
        self.assertEqual(support.feature_id, cloned[0].id)
        self.assertEqual(support.face.body_id, clone_body)
        self.assertEqual(support.face.anchor.feature_id, cloned[0].id)
        self.assertNotIn(support.feature_id, source_ids)
        self.assertNotEqual(cloned_sketch, source_sketch)
        self.assertTrue({item.id for item in source.get_sketch(source_sketch).entities}.isdisjoint(
                        item.id for item in document.get_sketch(cloned_sketch).entities))
        clone_host.location += Vector((0.2, 0.1, -0.1))
        clone_host.rotation_euler = (-0.1, 0.4, 0.2)
        bpy.context.view_layer.update()
        host, placement = commands.sketch_placement(bpy.context, document, source_sketch)
        self.assertEqual(host, original_host)
        self.assertEqual(rows(placement), rows(original_host.matrix_world))
        self.select_host()
        host, placement = commands.sketch_placement(bpy.context, document, cloned_sketch)
        self.assertEqual(host, clone_host)
        self.assertEqual(rows(placement), rows(clone_host.matrix_world))
        commands.set_parameter(bpy.context, "thickness", 12)
        for body in (self.body, clone_body):
            self.volume(7200 - math.pi * 4 * 3, body)
        filepath = str(Path(directory) / "attached-sketches.blend")
        snapshot = scene.blendercad_document_snapshot
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=filepath), {"FINISHED"})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=filepath), {"FINISHED"})
        scene = bpy.context.scene
        self.assertEqual(scene.blendercad_document_snapshot, snapshot)
        document = p.read_document(scene)
        self.assertEqual(support_of(document, cloned_sketch).feature_id, cloned[0].id)
        self.assertTrue(all(item.state == n.EvaluationState.UP_TO_DATE for item in p.runtime(scene).report.features))
        self.select_host(clone_body)
        step = Path(directory) / "attached-clone.step"
        self.assertEqual(bpy.ops.blendercad.export_step(filepath=str(step)), {"FINISHED"})
        kernel = n.new_geometry_adapter()
        shape = kernel.read_step(str(step))
        try:
            self.assertTrue(kernel.is_valid(shape))
            self.assertLessEqual(kernel.max_tolerance_mm(shape), 0.001)
            self.close(kernel.volume_mm3(shape), 7200 - math.pi * 4 * 3)
        finally:
            kernel.release(shape)

    def test_selected_face_capture_uses_current_context_and_rejects_curves_and_stale_picks(self):
        self.pocket()
        scene = bpy.context.scene
        self.select_target(self.top)
        support = commands.selected_face_support(bpy.context)
        self.assertEqual(support.feature_id, self.cut)
        self.assertEqual(support.face.anchor.feature_id, self.base)
        document = p.read_document(scene)
        packet = p.current_packet(scene, self.host())
        evaluator = p.runtime(scene).evaluator
        curved = next(index for index in range(packet.arrays["face_count"])
            if evaluator.measure_element(document, self.body, n.TopologyElement(n.TopologyKind.FACE, index)).radius_mm is not None)
        pick = p.resolve(scene, self.host(), "FACE", curved, packet.token)
        p.apply_pick(bpy.context, self.host(), pick)
        before = fingerprint()
        with self.assertRaisesRegex(ValueError, "planar"):
            commands.selected_face_support(bpy.context)
        self.assertEqual(fingerprint(), before)
        stale = self.select_target(self.top)
        commands.set_parameter(bpy.context, "thickness", 12)
        runtime = p.runtime(scene)
        runtime.last_pick, runtime.selected_picks = stale, [stale]
        before = fingerprint()
        with self.assertRaises(ValueError):
            commands.selected_face_support(bpy.context)
        self.assertEqual(fingerprint(), before)
        self.select_target(self.top)
        self.host().scale.x = 2
        bpy.context.view_layer.update()
        with self.assertRaisesRegex(ValueError, "Incompatible operation - Convert to regular mesh first"):
            commands.selected_face_support(bpy.context)
        self.host().scale.x = 1
        bpy.context.view_layer.update()

    def test_solid_deletion_cannot_silently_remove_a_supported_other_body(self):
        self.pocket()
        scene = bpy.context.scene
        self.select_target(self.top)
        support = commands.selected_face_support(bpy.context, reversed=False)
        draft = Session(p.read_document(scene), name="Supported independent boss", support=support,
                        evaluator=p.runtime(scene).evaluator)
        draft.add_circle((3, 3), 1)
        commands.commit(scene, draft.document)
        boss = commands.extrude_sketch(bpy.context, draft.sketch_id, 2)
        boss_body = p.read_document(scene).get_feature(boss).body_id
        self.assertEqual(rows(self.host(boss_body).matrix_world), rows(self.host().matrix_world))
        self.select_host()
        before = fingerprint()
        with self.assertRaisesRegex(ValueError, "supports another Solid"):
            commands.delete(bpy.context)
        self.assertEqual(fingerprint(), before)
        self.host(boss_body).select_set(True)
        commands.delete(bpy.context)
        document = p.read_document(scene)
        self.assertEqual(document.features, [])
        self.assertEqual(p.hosts(scene, document.id), [])
        self.assertNotIn(draft.sketch_id, {item.id for item in document.sketches})

    def test_unused_attached_sketch_blocks_source_solid_delete_and_conversion(self):
        scene = bpy.context.scene
        draft = self.draft()
        commands.commit(scene, draft.document)
        sketch = draft.sketch_id
        document = p.read_document(scene)
        self.assertFalse(any(item.sketch_id == sketch for item in document.features))
        before = fingerprint()
        for operation in (commands.delete, commands.convert):
            with self.assertRaisesRegex(ValueError, "supports an unused sketch"):
                operation(bpy.context)
            self.assertEqual(fingerprint(), before, "Rejected Solid operation discarded an unused attached sketch")
        self.assertEqual(bpy.ops.blendercad.delete_sketch(sketch_id=sketch), {"FINISHED"})
        self.assertNotIn(sketch, {item.id for item in p.read_document(scene).sketches})
        self.volume(6000)
        commands.delete(bpy.context)
        document = p.read_document(scene)
        self.assertEqual(document.features, [])
        self.assertEqual(p.hosts(scene, document.id), [])


with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, presentation as p
        from blendercad.sketch_commands import Session
        n = addon._native
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(BlenderSketchSupportChecks))
        if not result.wasSuccessful():
            raise AssertionError("Packaged Blender face-support regression failed")
        print("Blender face supports: current face capture, detached solving, placed frames, dependent pockets, explicit repair/detach, duplication, deletion, save/reopen, undo and STEP passed")
    finally:
        addon.unregister()
