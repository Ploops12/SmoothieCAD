"""Persistent planar face supports: definition, history, evaluation and schema."""

import argparse
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(arguments)
sys.path.insert(0, str(Path(options.module_dir).resolve()))
import _native as n

K, E, S, T = n.SketchConstraintKind, n.SketchElement, n.EvaluationState, n.TopologyKind


def ref(entity, element=E.GEOMETRY):
    return n.SketchReference(entity.id, element)


def rectangle(width=30, height=20):
    points = [(0, 0), (width, 0), (width, height), (0, height)]
    lines = [n.SketchEntity(n.Line2(n.Point2(*points[index]), n.Point2(*points[(index + 1) % 4])))
             for index in range(4)]
    problem = n.SketchProblem()
    problem.entities = lines
    problem.constraints = [n.SketchConstraint(K.COINCIDENT,
        [ref(lines[index], E.LINE_END), ref(lines[(index + 1) % 4], E.LINE_START)]) for index in range(4)]
    problem.constraints += [n.SketchConstraint(K.HORIZONTAL if index % 2 == 0 else K.VERTICAL, [ref(line)])
                            for index, line in enumerate(lines)]
    problem.constraints += [n.SketchConstraint(K.LENGTH, [ref(lines[0])], width),
                            n.SketchConstraint(K.LENGTH, [ref(lines[1])], height),
                            n.SketchConstraint(K.COINCIDENT, [ref(lines[0], E.LINE_START), n.SketchReference()])]
    return problem


def circle(radius=2, x=15, y=-10):
    entity = n.SketchEntity(n.Circle2(n.Point2(x, y), radius))
    center = ref(entity, E.CIRCLE_CENTER)
    problem = n.SketchProblem()
    problem.entities = [entity]
    problem.constraints = [n.SketchConstraint(K.RADIUS, [ref(entity)], radius),
        n.SketchConstraint(K.DISTANCE_X, [n.SketchReference(), center], x),
        n.SketchConstraint(K.DISTANCE_Y, [n.SketchReference(), center], y)]
    return problem


def support_of(document, sketch):
    return next(item.support for item in document.sketches if item.id == sketch)


def face(body, creator, key="cap:end"):
    return n.PersistentReference(body, T.FACE, n.ReferenceAnchor(creator, key))


def vector(value):
    return np.array((value.x, value.y, value.z), dtype=np.float64)


def plane_rows(plane):
    return tuple(tuple(vector(value)) for value in (plane.origin, plane.x_axis, plane.y_axis))


def plate(scale=1, plane=None):
    document = n.Document()
    document.add_parameter("thickness", 10 * scale, "mm")
    profile = rectangle(30 * scale, 20 * scale)
    if plane is not None:
        profile.plane = plane
    sketch = document.add_sketch("Plate profile", profile)
    base = document.add_extrusion("Plate", sketch, "thickness")
    return document, sketch, base, document.get_feature(base).body_id


def pocket_part(scale=1):
    document, profile, base, body = plate(scale)
    support = n.SketchSupport(base, face(body, base), n.Vector3(1, 0, 0), reversed=True)
    sketch = document.add_sketch("Face pocket", circle(2 * scale, 15 * scale, -10 * scale), support)
    cut = document.add_subtractive_extrusion("Blind pocket", base, sketch, 3 * scale)
    return document, profile, base, body, sketch, cut


class SketchSupportChecks(unittest.TestCase):
    def close(self, actual, expected):
        self.assertTrue(math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-12), (actual, expected))

    def assert_atomic(self, document, mutation, exception=ValueError):
        before = copy.deepcopy(document.to_snapshot())
        with self.assertRaises(exception):
            mutation()
        self.assertEqual(document.to_snapshot(), before)

    def test_moving_face_drives_blind_pocket_in_same_pass_at_three_scales(self):
        for scale in (0.01, 1, 20):
            with self.subTest(scale=scale):
                document, _profile, base, body, sketch, cut = pocket_part(scale)
                evaluator = n.FeatureEvaluator()
                before = document.to_snapshot()
                report = evaluator.evaluate(document)
                self.assertEqual([item.state for item in report.features], [S.UP_TO_DATE] * 2,
                                 [item.diagnostic for item in report.features])
                self.close(evaluator.volume_mm3(document, body), (6000 - math.pi * 4 * 3) * scale ** 3)
                self.close(evaluator.evaluated_sketch(document, sketch).plane.origin.z, 10 * scale)
                self.assertEqual(document.to_snapshot(), before, "Evaluation rewrote persistent support or sketch guesses")
                self.assertEqual(document.geometric_tolerance_mm, 0.001)
                identities = [item.id for item in document.features]
                document.set_parameter_value("thickness", 16 * scale)
                report = evaluator.evaluate(document)
                self.assertEqual((report.computed_count, report.reused_count), (2, 0))
                self.assertEqual([item.id for item in document.features], identities)
                self.assertEqual(document.get_feature(cut).input_id, base)
                self.close(evaluator.evaluated_sketch(document, sketch).plane.origin.z, 16 * scale)
                self.close(evaluator.volume_mm3(document, body), (9600 - math.pi * 4 * 3) * scale ** 3)
                hit = evaluator.ray_cast(document, body, n.Vector3(15 * scale, 10 * scale, 30 * scale),
                                         n.Vector3(0, 0, -1), 40 * scale)
                self.assertIsNotNone(hit)
                self.close(hit.point_mm.z, 13 * scale)
                self.assertEqual(evaluator.evaluate(document).reused_count, 2)

    def test_parameter_resolution_is_not_a_geometry_support_resolver(self):
        document, _profile, _base, _body, sketch, _cut = pocket_part()
        radius = document.get_sketch(sketch).constraints[0].id
        document.add_parameter("hole_radius", 2.5, "mm")
        document.set_sketch_dimension(sketch, radius, "hole_radius")
        before = document.to_snapshot()
        numeric = document.parameterized_sketch(sketch)
        self.assertEqual(numeric.constraints[0].value, 2.5)
        self.assertIsNone(numeric.constraints[0].parameter)
        self.assertEqual(numeric.plane.origin.z, 0)
        with self.assertRaises((ValueError, RuntimeError)):
            document.evaluated_sketch(sketch)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        resolved = evaluator.evaluated_sketch(document, sketch)
        self.assertEqual(resolved.plane.origin.z, 10)
        self.assertEqual(resolved.constraints[0].value, 2.5)
        self.assertEqual(document.to_snapshot(), before)
        copied = support_of(document, sketch)
        copied.offset_u_mm = 4
        self.assertEqual(support_of(document, sketch).offset_u_mm, 0,
                         "Returned support copy mutated saved document outside a command")

    def test_oriented_frames_offsets_reversal_and_projected_saved_x_axis(self):
        document, _profile, base, body = plate()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        for reverse, origin, normal in ((False, (3, 4, 12), (0, 0, 1)),
                                        (True, (3, -4, 8), (0, 0, -1))):
            support = n.SketchSupport(base, face(body, base), n.Vector3(2, 0, 7),
                offset_u_mm=3, offset_v_mm=4, normal_offset_mm=2, reversed=reverse)
            frame = evaluator.support_plane(document, support)
            np.testing.assert_allclose(vector(frame.origin), origin, atol=1e-12)
            np.testing.assert_allclose(vector(frame.x_axis), (1, 0, 0), atol=1e-12)
            np.testing.assert_allclose(np.cross(vector(frame.x_axis), vector(frame.y_axis)), normal, atol=1e-12)
        line = document.get_sketch(document.get_feature(base).sketch_id).entities[0]
        side = n.SketchSupport(base, face(body, base, "side:" + line.id), n.Vector3(1, 0, 0),
                             offset_u_mm=3, offset_v_mm=4, normal_offset_mm=2)
        frame = evaluator.support_plane(document, side)
        np.testing.assert_allclose(vector(frame.origin), (3, -2, 4), atol=1e-12)
        np.testing.assert_allclose(np.cross(vector(frame.x_axis), vector(frame.y_axis)), (0, -1, 0), atol=1e-12)
        result = evaluator.resolve_reference(document, face(body, base))
        exact = evaluator.planar_face(document, body, result.candidates[0].index)
        self.assertIsNotNone(exact)
        self.close(exact.origin.z, 10)

    def test_arbitrary_part_plane_uses_projected_part_origin_not_face_centroid(self):
        plane = n.SketchPlane()
        plane.origin, plane.x_axis, plane.y_axis = n.Vector3(7, 11, 13), n.Vector3(0, 1, 0), n.Vector3(0, 0, 1)
        document, _profile, base, body = plate(plane=plane)
        support = n.SketchSupport(base, face(body, base), n.Vector3(0, 1, 0), reversed=True)
        sketch = document.add_sketch("Arbitrary supported pocket", circle(2, 26, -23), support)
        document.add_subtractive_extrusion("Pocket", base, sketch, 3)
        evaluator = n.FeatureEvaluator()
        report = evaluator.evaluate(document)
        self.assertTrue(all(item.state == S.UP_TO_DATE for item in report.features),
                        [item.diagnostic for item in report.features])
        frame = evaluator.support_plane(document, support)
        np.testing.assert_allclose(vector(frame.origin), (17, 0, 0), atol=1e-10)
        self.close(evaluator.volume_mm3(document, body), 6000 - math.pi * 4 * 3)
        document.set_parameter_value("thickness", 14)
        evaluator.evaluate(document)
        np.testing.assert_allclose(vector(evaluator.evaluated_sketch(document, sketch).plane.origin), (21, 0, 0), atol=1e-10)
        self.close(evaluator.volume_mm3(document, body), 8400 - math.pi * 4 * 3)

    def test_support_changes_recompute_consumers_but_reuse_independent_geometry(self):
        document, _profile, _base, body, sketch, _cut = pocket_part()
        independent_sketch = document.add_sketch("Independent profile", rectangle(2, 3))
        independent = document.add_extrusion("Independent", independent_sketch, 2)
        other_body = document.get_feature(independent).body_id
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        other_generation = evaluator.tessellate(document, other_body)["geometry_generation"]
        document.add_parameter("unused", 1)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (0, 3))
        changed = support_of(document, sketch)
        changed.offset_u_mm = 2
        document.set_sketch_support(sketch, changed)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (1, 2))
        self.assertEqual(evaluator.tessellate(document, other_body)["geometry_generation"], other_generation)
        self.close(evaluator.volume_mm3(document, body), 6000 - math.pi * 4 * 3)
        document.set_parameter_value("thickness", 14)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (2, 1))
        self.assertEqual(evaluator.tessellate(document, other_body)["geometry_generation"], other_generation)

    def test_historical_context_is_not_current_tip_and_split_deleted_ambiguous_fail_explicitly(self):
        for failure in ("split", "deleted", "ambiguous"):
            with self.subTest(reference_state=failure):
                document, _profile, base, body = plate()
                if failure == "split":
                    tool = rectangle(2, 30)
                    frame = n.SketchPlane()
                    frame.origin = n.Vector3(14, -5, 0)
                    tool.plane = frame
                    tool_sketch = document.add_sketch("Split slot", tool)
                    context = document.add_subtractive_extrusion("Split top", base, tool_sketch, 11)
                elif failure == "deleted":
                    tool = rectangle(50, 40)
                    frame = n.SketchPlane()
                    frame.origin = n.Vector3(-10, -10, 9)
                    tool.plane = frame
                    tool_sketch = document.add_sketch("Remove top slab", tool)
                    context = document.add_subtractive_extrusion("Delete original top", base, tool_sketch, 2)
                else:
                    context = base
                target = face(body, base)
                historical = n.SketchSupport(base, target, n.Vector3(1, 0, 0))
                attached = document.add_sketch("Historical face", circle(1, 5, 5), historical)
                feature = document.add_extrusion("Supported independent body", attached, 2)
                new_body = document.get_feature(feature).body_id
                evaluator = n.FeatureEvaluator()
                initial = evaluator.evaluate(document)
                self.assertEqual(initial.features[-1].state, S.UP_TO_DATE,
                                 initial.features[-1].diagnostic)
                self.close(evaluator.volume_mm3(document, new_body), 2 * math.pi)
                good = evaluator.tessellate(document, new_body)
                broken_target = face(body, base, "unknown:repairable") if failure == "ambiguous" else target
                broken = n.SketchSupport(context, broken_target, n.Vector3(1, 0, 0))
                document.set_sketch_support(attached, broken)
                report = evaluator.evaluate(document)
                self.assertEqual(report.features[-1].state, S.FAILED)
                self.assertEqual(evaluator.resolve_reference(document, broken_target).state,
                    {"split": n.ReferenceState.SPLIT, "deleted": n.ReferenceState.DELETED,
                     "ambiguous": n.ReferenceState.AMBIGUOUS}[failure])
                if failure != "ambiguous":
                    self.assertIn(failure, report.features[-1].diagnostic.lower())
                else:
                    self.assertIn("requires exactly one resolved face", report.features[-1].diagnostic.lower())
                stale = evaluator.tessellate(document, new_body)
                self.assertFalse(stale["current"])
                self.assertEqual(stale["geometry_generation"], good["geometry_generation"])
                np.testing.assert_array_equal(stale["positions"], good["positions"])
                self.assertEqual(support_of(document, attached).feature_id, context)
                with self.assertRaises((ValueError, RuntimeError)):
                    evaluator.support_plane(document, broken)
                document.set_sketch_support(attached, historical)
                repaired = evaluator.evaluate(document)
                self.assertEqual(repaired.features[-1].state, S.UP_TO_DATE)
                self.close(evaluator.volume_mm3(document, new_body), 2 * math.pi)

    def test_curved_support_and_degenerate_projection_are_not_fake_planar_frames(self):
        document, _profile, base, body, sketch, _cut = pocket_part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        parallel = support_of(document, sketch)
        parallel.x_direction = n.Vector3(0, 0, 1)
        document.set_sketch_support(sketch, parallel)
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[-1].state, S.FAILED)
        self.assertTrue(report.features[-1].diagnostic)
        document2 = n.Document()
        circular = circle(5, 0, 0)
        cylinder_sketch = document2.add_sketch("Cylinder profile", circular)
        cylinder = document2.add_extrusion("Cylinder", cylinder_sketch, 10)
        cylinder_body = document2.get_feature(cylinder).body_id
        curved = face(cylinder_body, cylinder, "side:" + circular.entities[0].id)
        support = n.SketchSupport(cylinder, curved, n.Vector3(1, 0, 0))
        attached = document2.add_sketch("Not a plane", circle(1, 0, 0), support)
        document2.add_extrusion("Unavailable planar extrusion", attached, 2)
        other = n.FeatureEvaluator()
        result = other.evaluate(document2)
        self.assertEqual(result.features[-1].state, S.FAILED)
        self.assertIn("plan", result.features[-1].diagnostic.lower())
        curved_result = other.resolve_reference(document2, curved)
        self.assertEqual(curved_result.state, n.ReferenceState.RESOLVED)
        self.assertIsNone(other.planar_face(document2, cylinder_body, curved_result.candidates[0].index))
        with self.assertRaises((ValueError, RuntimeError)):
            other.support_plane(document2, support)

    def test_unavailable_support_blocks_dependents_retains_display_and_rejects_export(self):
        document, _profile, base, body, sketch, cut = pocket_part()
        downstream_sketch = document.add_sketch("Following extrusion", circle(1, 2, 2),
            n.SketchSupport(cut, face(body, base, "cap:start"), n.Vector3(1, 0, 0)))
        downstream = document.add_extrusion("Dependent body", downstream_sketch, 1)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        good = evaluator.tessellate(document, body)
        document.set_feature_suppressed(base, True)
        report = evaluator.evaluate(document)
        by_id = {item.feature_id: item.state for item in report.features}
        self.assertEqual(by_id[base], S.SUPPRESSED)
        self.assertEqual(by_id[cut], S.BLOCKED)
        self.assertEqual(by_id[downstream], S.BLOCKED)
        stale = evaluator.tessellate(document, body)
        self.assertFalse(stale["current"])
        self.assertEqual(stale["geometry_generation"], good["geometry_generation"])
        with self.assertRaises((ValueError, RuntimeError)):
            evaluator.evaluated_sketch(document, sketch)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keep.step"
            path.write_bytes(b"existing file")
            with self.assertRaises(RuntimeError):
                evaluator.write_step(document, body, str(path))
            self.assertEqual(path.read_bytes(), b"existing file")
        document.set_feature_suppressed(base, False)
        self.assertTrue(all(item.state == S.UP_TO_DATE for item in evaluator.evaluate(document).features))

    def test_support_queries_use_full_snapshot_currentness_not_revision_only(self):
        document, _profile, _base, body, sketch, _cut = pocket_part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        altered = copy.deepcopy(document.to_snapshot())
        item = next(item for item in altered["sketches"] if item["id"] == sketch)
        item["support"]["offset_u_mm"] = 1
        divergent = n.Document.from_snapshot(altered)
        self.assertEqual(divergent.revision, document.revision)
        self.assertFalse(evaluator.matches_document(divergent))
        for query in (lambda: evaluator.evaluated_sketch(divergent, sketch),
                      lambda: evaluator.support_plane(divergent, support_of(divergent, sketch)),
                      lambda: evaluator.planar_face(divergent, body, 0)):
            with self.assertRaises(RuntimeError):
                query()
        evaluator.evaluate(divergent)
        self.close(evaluator.evaluated_sketch(divergent, sketch).plane.origin.x, 1)

    def test_dependency_cycles_reorder_and_deletion_closure_are_atomic(self):
        document, profile, base, body, sketch, cut = pocket_part()
        independent_sketch = document.add_sketch("Other profile", rectangle(2, 3))
        independent = document.add_extrusion("Other body", independent_sketch, 2)
        unused = document.add_sketch("Unused supported sketch", circle(1, 5, 5),
            n.SketchSupport(base, face(body, base), n.Vector3(1, 0, 0)))
        bookmark = document.add_reference("Top", face(body, base))
        self.assertIn(base, document.feature_dependencies(cut))
        self.assertIn(sketch, document.feature_dependencies(cut))
        self.assert_atomic(document, lambda: document.reorder_features([cut, base, independent]))
        self.assert_atomic(document, lambda: document.set_sketch_support(sketch,
            n.SketchSupport(cut, face(body, base), n.Vector3(1, 0, 0))))
        self.assert_atomic(document, lambda: document.set_sketch_support(profile,
            n.SketchSupport(cut, face(body, base), n.Vector3(1, 0, 0))))
        self.assert_atomic(document, lambda: document.erase_feature(base))
        self.assert_atomic(document, lambda: document.erase_sketch(profile))
        before = copy.deepcopy(document.to_snapshot())
        for remove in (lambda candidate: candidate.erase_feature(base, cascade=True),
                       lambda candidate: candidate.erase_sketch(profile, cascade=True)):
            candidate = n.Document.from_snapshot(before)
            remove(candidate)
            self.assertEqual([item.id for item in candidate.features], [independent])
            self.assertTrue({sketch, unused}.isdisjoint(item.id for item in candidate.sketches))
            self.assertNotIn(bookmark, {item.id for item in candidate.references})
            self.assertEqual(n.Document.from_snapshot(candidate.to_snapshot()).to_snapshot(), candidate.to_snapshot())

    def test_support_structural_validation_and_explicit_detach(self):
        document, _profile, base, body, sketch, _cut = pocket_part()
        support = support_of(document, sketch)
        for invalid in (n.SketchSupport(n.new_document_id(), face(body, base), n.Vector3(1, 0, 0)),
                        n.SketchSupport(base, face(n.new_document_id(), base), n.Vector3(1, 0, 0)),
                        n.SketchSupport(base, n.PersistentReference(body, T.EDGE, n.ReferenceAnchor(base, "cap:end")), n.Vector3(1, 0, 0)),
                        n.SketchSupport(base, face(body, base), n.Vector3(0, 0, 0)),
                        n.SketchSupport(base, face(body, base), n.Vector3(float("nan"), 0, 0))):
            self.assert_atomic(document, lambda invalid=invalid: document.set_sketch_support(sketch, invalid))
        nonidentity = circle()
        plane = n.SketchPlane()
        plane.origin = n.Vector3(0, 0, 10)
        nonidentity.plane = plane
        self.assert_atomic(document, lambda: document.add_sketch("Competing plane", nonidentity, support))
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        frame = evaluator.support_plane(document, support)
        document.detach_sketch(sketch, frame)
        self.assertIsNone(support_of(document, sketch))
        self.assertEqual(plane_rows(document.evaluated_sketch(sketch).plane), plane_rows(frame))
        snapshot = next(item for item in document.to_snapshot()["sketches"] if item["id"] == sketch)
        self.assertIn("plane", snapshot)
        self.assertNotIn("support", snapshot)
        document.set_parameter_value("thickness", 16)
        evaluator.evaluate(document)
        self.close(evaluator.evaluated_sketch(document, sketch).plane.origin.z, 10)
        self.close(evaluator.volume_mm3(document, body), 9600 - math.pi * 4 * 3)

    def test_schema_eight_support_alternative_is_strict_and_legacy_versions_migrate(self):
        document, _profile, _base, _body, sketch, _cut = pocket_part()
        snapshot = document.to_snapshot()
        self.assertEqual(snapshot["schema_version"], 8)
        item = next(item for item in snapshot["sketches"] if item["id"] == sketch)
        self.assertEqual(item["version"], 3)
        self.assertEqual(set(item), {"id", "name", "version", "support", "entities", "constraints"})
        self.assertEqual(set(item["support"]), {"feature", "face", "x_direction", "offset_u_mm", "offset_v_mm", "normal_offset_mm", "reversed"})
        self.assertEqual(set(item["support"]["face"]), {"body", "kind", "feature", "key"})
        restored = n.Document.from_snapshot(json.loads(json.dumps(snapshot)))
        self.assertEqual(restored.to_snapshot(), snapshot)
        mutations = (
            lambda s, i: s.update(schema_version=9),
            lambda s, i: s.update(schema_version=7),
            lambda s, i: i.update(version=2),
            lambda s, i: i.update(version=4),
            lambda s, i: i.update(plane=s["sketches"][0]["plane"]),
            lambda s, i: i.pop("support"),
            lambda s, i: i["support"].update(extra=0),
            lambda s, i: i["support"].update(offset_u_mm=True),
            lambda s, i: i["support"].update(normal_offset_mm=float("nan")),
            lambda s, i: i["support"].update(reversed=1),
            lambda s, i: i["support"].update(x_direction=[0, 0, 0]),
            lambda s, i: i["support"].update(x_direction=[1, 0]),
            lambda s, i: i["support"]["face"].update(kind="edge"),
        )
        for mutation in mutations:
            candidate = copy.deepcopy(snapshot)
            attached = next(item for item in candidate["sketches"] if item["id"] == sketch)
            mutation(candidate, attached)
            with self.assertRaises(ValueError):
                n.Document.from_snapshot(candidate)
        plain, _profile, _base, _body = plate()
        for schema in range(1, 8):
            legacy = copy.deepcopy(plain.to_snapshot())
            legacy["schema_version"] = schema
            if schema < 4:
                legacy.pop("references")
            if schema < 3:
                for key in ("features", "rollback_before"):
                    legacy.pop(key)
            if schema < 2:
                legacy.pop("sketches")
            before = copy.deepcopy(legacy)
            migrated = n.Document.from_snapshot(legacy)
            self.assertEqual(legacy, before)
            self.assertEqual(migrated.schema_version, 8)
            self.assertEqual(migrated.id, plain.id)
            self.assertEqual(migrated.revision, plain.revision)
            self.assertTrue(all(item.support is None for item in migrated.sketches))


unittest.main(argv=[sys.argv[0]])
