"""Detached solid-feature previews against the packaged OCCT evaluator.

The scene fingerprint covers published definition, evaluator, picking packets,
display storage and selection. Preview validity is checked independently using
analytic CAD queries, rather than accepting a tessellated shape as evidence.
"""

import argparse
import copy
import importlib
import math
import sys
import tempfile
import unittest
import zipfile

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
LOSS = 1 - math.pi / 4


def clone(document):
    return n.Document.from_snapshot(copy.deepcopy(document.to_snapshot()))


def fingerprint():
    scene = bpy.context.scene
    state = p.runtime(scene)
    return (scene.blendercad_document_snapshot, id(state), id(state.evaluator),
            tuple((host[p.BODY], p.current_packet(scene, host).token,
                   p.display_for(scene, host).as_pointer(),
                   p.display_for(scene, host).data.as_pointer(),
                   tuple(tuple(vertex.co) for vertex in p.display_for(scene, host).data.vertices),
                   tuple(tuple(row) for row in host.matrix_world))
                  for host in p.hosts(scene, state.document.id)),
            tuple(sorted(obj.as_pointer() for obj in bpy.context.selected_objects)),
            bpy.context.active_object.as_pointer() if bpy.context.active_object else None,
            tuple((operator.bl_rna.identifier, operator.as_pointer())
                  for operator in bpy.context.window_manager.operators),
            tuple(p.pick_key(pick) for pick in state.selected_picks),
            len(bpy.data.objects), len(bpy.data.meshes))


class FeaturePreviewChecks(unittest.TestCase):
    def setUp(self):
        self.source = p.read_document(bpy.context.scene)
        self.evaluator = p.runtime(bpy.context.scene).evaluator
        self.source_snapshot = copy.deepcopy(self.source.to_snapshot())
        self.before = fingerprint()
        self.base = self.source.features[0].id
        self.body = self.source.get_feature(self.base).body_id
        self.other = self.source.features[1].id
        self.other_body = self.source.get_feature(self.other).body_id

    def tearDown(self):
        self.assertEqual(self.source.to_snapshot(), self.source_snapshot,
                         "Preview changed its caller's design document")
        self.assertTrue(self.evaluator.matches_document(self.source),
                        "Preview changed its caller's evaluator source")
        self.assertAlmostEqual(self.evaluator.volume_mm3(self.source, self.body), 3000)
        self.assertEqual(fingerprint(), self.before,
                         "Preview changed published scene/display/picking/selection state")

    def assert_close(self, actual, expected):
        self.assertTrue(math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8),
                        (actual, expected))

    def assert_volume(self, session, expected, body=None):
        self.assertTrue(session.valid, session.error)
        self.assertFalse(session.error)
        self.assertTrue(session.evaluator.matches_document(session.document))
        self.assertEqual(session.document.geometric_tolerance_mm, 0.001)
        self.assert_close(session.evaluator.volume_mm3(session.document, body or session.body_id), expected)
        self.assertIsNotNone(session.arrays)
        self.assertTrue(session.arrays["current"])
        self.assertEqual(session.arrays["body_id"], session.body_id)

    def edge(self, document=None):
        document = document or self.source
        line = document.get_sketch(document.get_feature(self.base).sketch_id).entities[0]
        return n.PersistentReference(self.body, n.TopologyKind.EDGE,
            n.ReferenceAnchor(self.base, "edge:sweep:" + line.id + ":start"))

    def radius(self, session, expected):
        values = [session.evaluator.measure_element(session.document, session.body_id,
                  n.TopologyElement(n.TopologyKind.FACE, index)).radius_mm
                  for index in range(session.arrays["face_count"])]
        self.assertTrue(any(value is not None and math.isclose(value, expected, rel_tol=1e-9)
                            for value in values), values)

    def test_extrusion_updates_have_stable_identities_and_real_geometry(self):
        session = Session(self.source, self.evaluator,
                          Operation("extrude", sketch_id=circle_sketch), 3)
        identifiers = session.feature_id, session.body_id
        self.assertNotIn(session.feature_id, {item.id for item in self.source.features})
        self.assert_volume(session, math.pi * 2 ** 2 * 3)
        for depth in (7, 2.5, 0.02):
            session.set_numeric(depth)
            self.assertEqual((session.feature_id, session.body_id), identifiers)
            self.assertEqual(session.input, depth)
            self.assert_close(session.value_mm, depth)
            self.assert_volume(session, math.pi * 2 ** 2 * depth)
        self.assertEqual(session.document.get_sketch(circle_sketch).entities[0].id,
                         self.source.get_sketch(circle_sketch).entities[0].id)

    def test_cut_preview_distinguishes_blind_and_through_and_recovers(self):
        session = Session(self.source, self.evaluator,
            Operation("cut", sketch_id=circle_sketch, input_id=self.base), 2)
        self.assertEqual(session.body_id, self.body)
        self.assert_volume(session, 3000 - math.pi * 2 ** 2 * 2)
        identifiers = session.feature_id, session.body_id
        session.set_numeric(8)
        self.assert_volume(session, 3000 - math.pi * 2 ** 2 * 5)
        self.assertIsNone(session.evaluator.ray_cast(session.document, self.body,
            n.Vector3(10, 10, -10), n.Vector3(0, 0, 1), 30))
        good_arrays = session.arrays
        good_display = copy.deepcopy(session.display_document.to_snapshot())
        session.set_numeric(0)
        self.assertFalse(session.valid)
        self.assertTrue(session.error)
        self.assertIs(session.arrays, good_arrays)
        self.assertEqual(session.display_document.to_snapshot(), good_display)
        with self.assertRaises(RuntimeError):
            session.evaluator.volume_mm3(session.document, self.body)
        session.set_numeric(4)
        self.assert_volume(session, 3000 - math.pi * 2 ** 2 * 4)
        self.assertIsNotNone(session.evaluator.ray_cast(session.document, self.body,
            n.Vector3(10, 10, -10), n.Vector3(0, 0, 1), 30))
        self.assertEqual((session.feature_id, session.body_id), identifiers)

    def test_fillet_uses_persistent_input_and_retains_last_valid_on_failure(self):
        target = self.edge()
        session = Session(self.source, self.evaluator,
            Operation("fillet", input_id=self.base, edges=(target,)), 1)
        self.assert_volume(session, 3000 - LOSS * 5)
        self.radius(session, 1)
        feature = session.document.get_feature(session.feature_id)
        self.assertEqual(feature.edges[0].anchor.key, target.anchor.key)
        identifier = session.feature_id
        session.set_numeric(2)
        self.assert_volume(session, 3000 - LOSS * 2 ** 2 * 5)
        self.radius(session, 2)
        good_arrays = session.arrays
        session.set_numeric(100)
        self.assertFalse(session.valid)
        self.assertTrue(session.error)
        self.assertIs(session.arrays, good_arrays)
        self.assertEqual(session.report.features[-1].state, n.EvaluationState.FAILED)
        session.set_numeric(1.5)
        self.assertEqual(session.feature_id, identifier)
        self.assert_volume(session, 3000 - LOSS * 1.5 ** 2 * 5)
        self.radius(session, 1.5)

    def test_depth_adjustment_updates_literal_parameter_without_unbinding(self):
        session = Session(self.source, self.evaluator, Operation("depth", feature_id=self.base))
        self.assertEqual(session.input, "shared_depth")
        session.set_numeric(8)
        self.assertEqual(session.document.get_feature(self.base).depth, "shared_depth")
        self.assertEqual(session.document.get_feature(self.other).depth, "shared_depth")
        self.assert_close(session.document.length_value("shared_depth"), 8)
        self.assert_volume(session, 30 * 20 * 8)
        self.assert_close(session.evaluator.volume_mm3(session.document, self.other_body), 2 * 3 * 8)
        session.set_input("double_depth")
        self.assertEqual(session.input, "double_depth")
        self.assert_volume(session, 30 * 20 * 16)
        saved = copy.deepcopy(session.document.to_snapshot())
        arrays = session.arrays
        with self.assertRaisesRegex(ValueError, "expression"):
            session.set_numeric(9)
        self.assertEqual(session.document.to_snapshot(), saved)
        self.assertIs(session.arrays, arrays)
        self.assertTrue(session.valid)
        session.set_input(6)  # Explicit replacement is distinct from dragging.
        self.assertEqual(session.input, 6)
        self.assert_volume(session, 30 * 20 * 6)
        self.assert_close(session.evaluator.volume_mm3(session.document, self.other_body), 2 * 3 * 8)

    def test_drag_fork_is_an_exact_cancel_baseline_and_bad_binding_is_atomic(self):
        session = Session(self.source, self.evaluator, Operation("depth", feature_id=self.base))
        baseline = session.fork()
        snapshot = copy.deepcopy(baseline.document.to_snapshot())
        session.set_numeric(9)
        self.assert_volume(session, 30 * 20 * 9)
        self.assertEqual(baseline.document.to_snapshot(), snapshot)
        self.assert_volume(baseline, 3000)
        baseline.set_numeric(7)
        self.assert_volume(baseline, 30 * 20 * 7)
        self.assert_volume(session, 30 * 20 * 9)
        saved = copy.deepcopy(session.document.to_snapshot())
        arrays = session.arrays
        with self.assertRaises(ValueError):
            session.set_input("missing_length_parameter")
        self.assertEqual(session.document.to_snapshot(), saved)
        self.assertIs(session.arrays, arrays)
        self.assert_volume(session, 30 * 20 * 9)

    def test_radius_editing_uses_same_drivers_and_geometry_as_creation(self):
        created = Session(self.source, self.evaluator,
            Operation("fillet", input_id=self.base, edges=(self.edge(),)), "round_radius")
        self.assert_volume(created, 3000 - LOSS * 5)
        session = Session(created.document, created.evaluator,
                          Operation("radius", feature_id=created.feature_id))
        session.set_numeric(1.5)
        self.assertEqual(session.document.get_feature(created.feature_id).radius, "round_radius")
        self.assert_volume(session, 3000 - LOSS * 1.5 ** 2 * 5)
        self.radius(session, 1.5)
        self.assertEqual(created.document.length_value("round_radius"), 1)
        self.assert_volume(created, 3000 - LOSS * 5)
        session.set_input("double_radius")
        self.assert_volume(session, 3000 - LOSS * 3 ** 2 * 5)
        with self.assertRaisesRegex(ValueError, "expression"):
            session.set_numeric(4)
        self.assertEqual(session.document.get_feature(created.feature_id).radius, "double_radius")

    def test_new_shared_parameter_failure_elsewhere_blocks_confirmation(self):
        document = clone(self.source)
        third = document.add_extrusion("Fillet stock", document.get_feature(self.base).sketch_id, 5)
        third_body = document.get_feature(third).body_id
        line = document.get_sketch(document.get_feature(third).sketch_id).entities[0]
        edge = n.PersistentReference(third_body, n.TopologyKind.EDGE,
            n.ReferenceAnchor(third, "edge:sweep:" + line.id + ":start"))
        fillet = document.add_fillet("Shared driver fillet", third, [edge], "shared_depth")
        evaluator = self.evaluator.fork()
        report = evaluator.evaluate(document)
        self.assertTrue(all(item.state == n.EvaluationState.UP_TO_DATE for item in report.features))
        session = Session(document, evaluator, Operation("depth", feature_id=self.base))
        good_arrays = session.arrays
        session.set_numeric(100)
        by_id = {item.feature_id: item for item in session.report.features}
        self.assertEqual(by_id[self.base].state, n.EvaluationState.UP_TO_DATE)
        self.assertEqual(by_id[fillet].state, n.EvaluationState.FAILED)
        self.assertFalse(session.valid, "Preview would confirm a newly failed shared-parameter user")
        self.assertTrue(session.error)
        self.assertIs(session.arrays, good_arrays)
        session.set_numeric(4)
        self.assert_volume(session, 30 * 20 * 4)
        self.assert_close(session.evaluator.volume_mm3(session.document, third_body), 3000 - LOSS * 4 ** 2 * 5)

    def test_preexisting_unrelated_failure_does_not_prevent_valid_edit(self):
        document = clone(self.source)
        document.set_feature_depth(self.other, 0)
        evaluator = self.evaluator.fork()
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[1].state, n.EvaluationState.FAILED)
        session = Session(document, evaluator, Operation("depth", feature_id=self.base))
        session.set_numeric(7)
        self.assert_volume(session, 30 * 20 * 7)
        self.assertEqual(session.report.features[1].state, n.EvaluationState.FAILED)

    def test_unavailable_features_and_mismatched_source_fail_closed(self):
        changed = clone(self.source)
        changed.set_parameter_value("shared_depth", 6)
        with self.assertRaises((ValueError, RuntimeError)):
            Session(changed, self.evaluator, Operation("depth", feature_id=self.base))
        for unavailable in ("suppressed", "rollback"):
            document = clone(self.source)
            if unavailable == "suppressed":
                document.set_feature_suppressed(self.base, True)
            else:
                document.set_rollback_before(self.base)
            evaluator = self.evaluator.fork()
            evaluator.evaluate(document)
            with self.assertRaises((ValueError, RuntimeError)):
                Session(document, evaluator, Operation("depth", feature_id=self.base))
            if unavailable == "rollback":
                with self.assertRaises((ValueError, RuntimeError)):
                    Session(document, evaluator, Operation("extrude", sketch_id=circle_sketch), 3)

    def test_arbitrary_plane_extrusion_is_part_local_analytic_geometry(self):
        document = clone(self.source)
        problem = n.SketchProblem()
        # pybind value properties copy their objects; assign the complete plane.
        plane = n.SketchPlane()
        plane.origin = n.Vector3(3, 4, 5)
        plane.x_axis, plane.y_axis = n.Vector3(0, 1, 0), n.Vector3(0, 0, 1)
        problem.plane = plane
        problem.entities = [n.SketchEntity(n.Circle2(n.Point2(0, 0), 2))]
        sketch = document.add_sketch("Offset YZ circle", problem)
        evaluator = self.evaluator.fork()
        evaluator.evaluate(document)
        session = Session(document, evaluator, Operation("extrude", sketch_id=sketch), 7)
        self.assert_volume(session, math.pi * 2 ** 2 * 7)
        hit = session.evaluator.ray_cast(session.document, session.body_id,
            n.Vector3(-10, 4, 5), n.Vector3(1, 0, 0), 40)
        self.assertIsNotNone(hit)
        # The source host has a world transform, but native preview geometry
        # stays in its design plane rather than baking that presentation in.
        self.assert_close(hit.point_mm.x, 3)


with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, presentation as p
        from blendercad.feature_commands import Operation, Session
        n = addon._native
        assert n is not None
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        commands.set_parameter(bpy.context, "shared_depth", 5, "mm")
        commands.set_parameter(bpy.context, "round_radius", 1, "mm")
        commands.set_parameter(bpy.context, "double_depth", 0, "mm", expression="shared_depth * 2")
        commands.set_parameter(bpy.context, "double_radius", 0, "mm", expression="round_radius * 2")
        first = commands.add_rectangle(bpy.context, 30, 20, 5)
        commands.set_depth(bpy.context, first, "shared_depth")
        second = commands.add_rectangle(bpy.context, 2, 3, 5)
        commands.set_depth(bpy.context, second, "shared_depth")
        document = p.read_document(bpy.context.scene)
        problem = n.SketchProblem()
        problem.entities = [n.SketchEntity(n.Circle2(n.Point2(10, 10), 2))]
        circle_sketch = document.add_sketch("Preview cutter", problem)
        commands.commit(bpy.context.scene, document)
        host = bpy.context.active_object
        host.location, host.rotation_euler = (0.12, -0.3, 0.04), (0.3, -0.2, 0.6)
        bpy.context.view_layer.update()
        packet = p.current_packet(bpy.context.scene, host)
        p.apply_pick(bpy.context, host, p.resolve(bpy.context.scene, host, "FACE", 0, packet.token))
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(FeaturePreviewChecks)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise AssertionError("Detached feature preview regression failed")

        # Publication uses the ordinary command journal, not a preview mesh or
        # an independently edited Blender definition. Background undo brackets
        # complement the real modal interaction covered by the window check.
        scene = bpy.context.scene
        source = p.read_document(scene)
        base = source.features[0].id
        body = source.get_feature(base).body_id
        session = Session(source, p.runtime(scene).evaluator, Operation("depth", feature_id=base))
        session.set_numeric(7)
        assert session.valid, session.error
        snapshot = scene.blendercad_document_snapshot
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="Before feature preview confirmation")
        commands.commit(scene, session.document)
        bpy.ops.ed.undo_push(message="Confirmed feature preview")
        current = p.read_document(scene)
        assert current.to_snapshot() == session.document.to_snapshot()
        close = lambda actual, expected: math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8)
        assert close(p.runtime(scene).evaluator.volume_mm3(current, body), 30 * 20 * 7)
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        assert scene.blendercad_document_snapshot == snapshot
        assert close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 3000)
        assert bpy.ops.ed.redo() == {"FINISHED"}
        scene = bpy.context.scene
        current = p.read_document(scene)
        assert current.to_snapshot() == session.document.to_snapshot()
        assert close(p.runtime(scene).evaluator.volume_mm3(current, body), 30 * 20 * 7)
        print("Feature previews: isolated CAD geometry, stable identities, cuts/fillets, literal/shared/expression drivers, failure recovery and command publication/undo passed")
    finally:
        addon.unregister()
