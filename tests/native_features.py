"""Document -> PlaneGCS -> OCCT feature evaluation and history acceptance."""

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

K, E, S = n.SketchConstraintKind, n.SketchElement, n.EvaluationState


def reference(entity, element=E.GEOMETRY):
    return n.SketchReference(entity.id, element)


def rectangle(width=20, height=30):
    points = [(0, 0), (width, 0), (width, height), (0, height)]
    lines = [n.SketchEntity(n.Line2(n.Point2(*points[i]), n.Point2(*points[(i+1) % 4]))) for i in range(4)]
    problem = n.SketchProblem()
    problem.entities = lines
    problem.constraints = [n.SketchConstraint(K.COINCIDENT, [reference(lines[i], E.LINE_END),
                                                            reference(lines[(i+1) % 4], E.LINE_START)]) for i in range(4)] + [
        n.SketchConstraint(K.HORIZONTAL if i % 2 == 0 else K.VERTICAL, [reference(line)]) for i, line in enumerate(lines)] + [
        n.SketchConstraint(K.LENGTH, [reference(lines[0])], width),
        n.SketchConstraint(K.LENGTH, [reference(lines[1])], height),
        n.SketchConstraint(K.COINCIDENT, [reference(lines[0], E.LINE_START), n.SketchReference()])]
    return problem


def circle():
    entity = n.SketchEntity(n.Circle2(n.Point2(10, 15), 3))
    center = reference(entity, E.CIRCLE_CENTER)
    problem = n.SketchProblem()
    problem.entities = [entity]
    problem.constraints = [n.SketchConstraint(K.RADIUS, [reference(entity)], 3),
                           n.SketchConstraint(K.DISTANCE_X, [n.SketchReference(), center], 10),
                           n.SketchConstraint(K.DISTANCE_Y, [n.SketchReference(), center], 15)]
    return problem


def part():
    document = n.Document()
    document.add_parameter("thickness", 5, "mm")
    profile = document.add_sketch("Plate", rectangle())
    hole = document.add_sketch("Hole", circle())
    base = document.add_extrusion("Base", profile, "thickness")
    cut = document.add_subtractive_extrusion("Hole cut", base, hole, "thickness")
    return document, profile, base, cut, document.get_feature(base).body_id


class FeatureChecks(unittest.TestCase):
    def test_evaluate_persist_display_and_step(self):
        document, _, base, cut, body = part()
        evaluator = n.FeatureEvaluator()
        self.assertFalse(evaluator.matches_document(document))
        report = evaluator.evaluate(document)
        self.assertEqual([item.state for item in report.features], [S.UP_TO_DATE] * 2)
        self.assertEqual(report.computed_count, 2)
        self.assertEqual(report.bodies[0].feature_id, cut)
        expected = 3000 - math.pi * 9 * 5
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), expected, places=8)
        display = evaluator.tessellate(document, body)
        self.assertTrue(display["current"])
        self.assertEqual(display["feature_id"], cut)
        self.assertEqual(display["geometry_feature_id"], cut)
        self.assertEqual(display["revision"], document.revision)
        self.assertEqual(display["linear_deflection_mm"], 0.01)
        self.assertEqual(display["angular_deflection_rad"], 0.2)
        self.assertEqual(len(display["triangle_faces"]) * 3, len(display["triangles"]))
        self.assertGreater(len(display["positions"]), 0)
        self.assertEqual(document.get_feature(base).body_id, document.get_feature(cut).body_id)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "plate.step")
            evaluator.write_step(document, body, path)
            kernel = n.new_geometry_adapter()
            restored = kernel.read_step(path)
            self.assertAlmostEqual(kernel.volume_mm3(restored), expected, places=7)
        reopened = n.Document.from_snapshot(json.loads(json.dumps(document.to_snapshot())))
        other = n.FeatureEvaluator()
        self.assertEqual(other.evaluate(reopened).computed_count, 2)
        self.assertAlmostEqual(other.volume_mm3(reopened, body), expected, places=8)

    def test_cache_invalidation_only_recomputes_affected_features(self):
        document, profile, base, cut, body = part()
        independent_sketch = rectangle(8, 6)
        independent_sketch.plane.origin = n.Vector3(100, 0, 0)
        other_sketch = document.add_sketch("Other", independent_sketch)
        other = document.add_extrusion("Other body", other_sketch, 2)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        generation = evaluator.tessellate(document, body)["geometry_generation"]
        repeated = evaluator.evaluate(document)
        self.assertEqual((repeated.computed_count, repeated.reused_count), (0, 3))
        document.add_parameter("unrelated", 12)
        with self.assertRaises(RuntimeError):
            evaluator.volume_mm3(document, body)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (0, 3))
        self.assertEqual(evaluator.tessellate(document, body)["geometry_generation"], generation)
        document.set_parameter_value("thickness", 6)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (2, 1))
        self.assertEqual(report.features[-1].feature_id, other)
        self.assertTrue(report.features[-1].reused)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 6 * (600 - math.pi * 9), places=8)
        document.rename_sketch(profile, "Renamed, same geometry")
        self.assertEqual(evaluator.evaluate(document).reused_count, 3)

    def test_earlier_sketch_edit_preserves_downstream_intent_and_saved_guesses(self):
        document, profile_id, base, cut, body = part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        candidate = document.get_sketch(profile_id)
        first_line = candidate.entities[0].id
        constraints = candidate.constraints
        for constraint in constraints:
            if constraint.kind == K.LENGTH and constraint.targets[0].entity == first_line:
                constraint.value = 25
        candidate.constraints = constraints
        document.replace_sketch(profile_id, candidate)
        before = document.to_snapshot()
        report = evaluator.evaluate(document)
        self.assertEqual([item.state for item in report.features], [S.UP_TO_DATE] * 2)
        self.assertEqual(report.computed_count, 2)
        self.assertEqual(document.to_snapshot(), before)
        self.assertEqual(document.get_sketch(profile_id).entities[0].geometry.end.x, 20)
        self.assertEqual(document.get_feature(cut).input_id, base)
        self.assertEqual(document.get_feature(cut).body_id, body)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 25 * 30 * 5 - math.pi * 9 * 5, places=7)
        self.assertAlmostEqual(evaluator.tessellate(document, body)["positions"].reshape(-1, 3)[:, 0].max(), 25, places=6)
        self.assertEqual(evaluator.evaluate(document).reused_count, 2)

    def test_failed_and_blocked_keep_display_but_never_export_stale_shape(self):
        document, profile_id, base, cut, body = part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        before = evaluator.tessellate(document, body)
        document.set_feature_depth(base, 0)
        report = evaluator.evaluate(document)
        self.assertEqual([item.state for item in report.features], [S.FAILED, S.BLOCKED])
        self.assertTrue(report.features[0].diagnostic)
        display = evaluator.tessellate(document, body)
        self.assertFalse(display["current"])
        self.assertEqual(display["geometry_generation"], before["geometry_generation"])
        np.testing.assert_array_equal(display["positions"], before["positions"])
        with self.assertRaises(RuntimeError):
            evaluator.volume_mm3(document, body)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "must_not_change.step"
            path.write_text("sentinel")
            with self.assertRaises(RuntimeError):
                evaluator.write_step(document, body, str(path))
            self.assertEqual(path.read_text(), "sentinel")
        document.set_feature_depth(base, "thickness")
        self.assertEqual(evaluator.evaluate(document).reused_count, 2)
        original = document.get_sketch(profile_id)
        conflicting = document.get_sketch(profile_id)
        conflicting.constraints += [n.SketchConstraint(K.LENGTH, [reference(conflicting.entities[0])], 30)]
        document.replace_sketch(profile_id, conflicting)
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[0].state, S.FAILED)
        self.assertTrue(report.features[0].sketch_report.conflicting_constraints)
        document.replace_sketch(profile_id, original)
        self.assertEqual(evaluator.evaluate(document).reused_count, 2)

    def test_tolerance_cycle_keeps_operand_handles_in_their_owner(self):
        document, _, base, cut, body = part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        old_generation = evaluator.tessellate(document, body)["geometry_generation"]
        document.set_geometric_tolerance_mm(1e-10)
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[0].state, S.FAILED)
        self.assertFalse(evaluator.tessellate(document, body)["current"])
        document.set_geometric_tolerance_mm(0.001)
        document.set_feature_depth(cut, 4)
        report = evaluator.evaluate(document)
        self.assertEqual([item.state for item in report.features], [S.UP_TO_DATE] * 2)
        self.assertEqual((report.computed_count, report.reused_count), (1, 1))
        self.assertGreater(evaluator.tessellate(document, body)["geometry_generation"], old_generation)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 3000 - math.pi * 9 * 4, places=8)

    def test_suppression_and_rollback_preserve_later_definitions(self):
        document, _, base, cut, body = part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        document.set_feature_suppressed(base, True)
        report = evaluator.evaluate(document)
        self.assertEqual([item.state for item in report.features], [S.SUPPRESSED, S.BLOCKED])
        self.assertFalse(evaluator.tessellate(document, body)["current"])
        document.set_feature_suppressed(base, False)
        self.assertEqual(evaluator.evaluate(document).reused_count, 2)
        document.set_rollback_before(cut)
        report = evaluator.evaluate(document)
        self.assertEqual([item.state for item in report.features], [S.UP_TO_DATE, S.ROLLED_BACK])
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 3000, places=8)
        self.assertEqual(evaluator.tessellate(document, body)["feature_id"], base)
        document.set_rollback_before(base)
        self.assertEqual(len(evaluator.evaluate(document).bodies), 0)
        self.assertEqual(len(document.features), 2)
        with self.assertRaises(ValueError):
            evaluator.volume_mm3(document, body)
        document.set_rollback_before(None)
        self.assertEqual(evaluator.evaluate(document).reused_count, 2)

    def test_reorder_and_dependency_deletion_update_available_bodies(self):
        document, _, base, cut, body = part()
        other_sketch = document.add_sketch("Other", rectangle(8, 6))
        other = document.add_extrusion("Other", other_sketch, 2)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        document.reorder_features([other, base, cut])
        self.assertEqual(evaluator.evaluate(document).reused_count, 3)
        before = document.to_snapshot()
        with self.assertRaises(ValueError):
            document.reorder_features([cut, base, other])
        with self.assertRaises(ValueError):
            document.erase_feature(base)
        self.assertEqual(document.to_snapshot(), before)
        document.erase_feature(base, cascade=True)
        report = evaluator.evaluate(document)
        self.assertEqual(len(report.bodies), 1)
        with self.assertRaises(ValueError):
            evaluator.volume_mm3(document, body)

    def test_same_revision_divergent_snapshot_is_not_a_current_result(self):
        document, _, _, _, body = part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        altered = copy.deepcopy(document.to_snapshot())
        altered["parameters"][0]["value"] = 7
        divergent = n.Document.from_snapshot(altered)
        self.assertEqual(divergent.revision, document.revision)
        self.assertFalse(evaluator.matches_document(divergent))
        with self.assertRaises(RuntimeError):
            evaluator.volume_mm3(divergent, body)
        evaluator.evaluate(divergent)
        self.assertAlmostEqual(evaluator.volume_mm3(divergent, body), 7 * (600 - math.pi * 9), places=8)
        with self.assertRaises(RuntimeError):
            evaluator.volume_mm3(document, body)

    def test_open_profile_and_foreign_document_cannot_reuse_old_display(self):
        document, profile, base, _, body = part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        altered = copy.deepcopy(document.to_snapshot())
        altered["document_id"] = n.new_document_id()
        altered["features"][0]["depth"] = {"value_mm": 0}
        foreign = n.Document.from_snapshot(altered)
        report = evaluator.evaluate(foreign)
        self.assertFalse(report.bodies[0].has_display)
        with self.assertRaises(RuntimeError):
            evaluator.tessellate(foreign, body)
        open_profile = n.SketchProblem()
        open_profile.entities = [n.SketchEntity(n.Line2(n.Point2(0, 0), n.Point2(1, 0))),
                                 n.SketchEntity(n.Line2(n.Point2(1, 0), n.Point2(1, 1))),
                                 n.SketchEntity(n.Line2(n.Point2(1, 1), n.Point2(2, 1)))]
        empty = n.Document()
        sketch = empty.add_sketch("Open", open_profile)
        empty.add_extrusion("Invalid profile", sketch, 1)
        report = evaluator.evaluate(empty)
        self.assertEqual(report.features[0].state, S.FAILED)
        self.assertIn("open", report.features[0].diagnostic)

    def test_new_body_identity_cannot_be_given_an_old_bodys_display(self):
        document, _, _, _, body = part()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        changed = copy.deepcopy(document.to_snapshot())
        new_body = n.new_document_id()
        for feature in changed["features"]:
            feature["body"] = new_body
        changed["features"][0]["depth"] = {"value_mm": 0}
        replacement = n.Document.from_snapshot(changed)
        report = evaluator.evaluate(replacement)
        self.assertEqual(report.bodies[0].body_id, new_body)
        self.assertFalse(report.bodies[0].has_display)
        with self.assertRaises(RuntimeError):
            evaluator.tessellate(replacement, new_body)

    def test_profile_connectivity_is_not_entity_order_or_direction(self):
        profile = n.SketchProblem()
        segments = [(n.Point2(2, 3), n.Point2(2, 0)), (n.Point2(0, 0), n.Point2(2, 0)),
                    (n.Point2(0, 3), n.Point2(0, 0)), (n.Point2(2, 3), n.Point2(0, 3))]
        profile.entities = [n.SketchEntity(n.Line2(a, b)) for a, b in segments]
        document = n.Document()
        sketch = document.add_sketch("Unordered loop", profile)
        feature = document.add_extrusion("Extrusion", sketch, 4)
        body = document.get_feature(feature).body_id
        evaluator = n.FeatureEvaluator()
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[0].state, S.UP_TO_DATE)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 24, places=9)
        profile.entities += [n.SketchEntity(n.Line2(n.Point2(0, 0), n.Point2(-2, -2)))]
        document.replace_sketch(sketch, profile)
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[0].state, S.FAILED)
        self.assertIn("ambiguous", report.features[0].diagnostic)

    def test_failed_new_cut_uses_explicit_stale_ancestor_display(self):
        document, _, base, cut, body = part()
        document.erase_feature(cut)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        outside = circle()
        outside.plane.origin.x = 100
        sketch = document.add_sketch("Outside part", outside)
        failed = document.add_subtractive_extrusion("No intersection", base, sketch, 5)
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[-1].state, S.FAILED)
        self.assertIn("did not remove material", report.features[-1].diagnostic)
        display = evaluator.tessellate(document, body)
        self.assertFalse(display["current"])
        self.assertEqual(display["feature_id"], failed)
        self.assertEqual(display["geometry_feature_id"], base)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
