"""Named document parameters driving persistent, solver-neutral sketch dimensions."""

import argparse
import copy
import gc
import json
import math
from pathlib import Path
import sys
import unittest

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(arguments)
sys.path.insert(0, str(Path(options.module_dir).resolve()))
import _native as n

K, E, S = n.SketchConstraintKind, n.SketchElement, n.EvaluationState


def ref(entity, element=E.GEOMETRY):
    return n.SketchReference(entity.id, element)


def rectangle(width=20, height=10):
    points = [(0, 0), (width, 0), (width, height), (0, height)]
    lines = [n.SketchEntity(n.Line2(n.Point2(*points[i]), n.Point2(*points[(i + 1) % 4]))) for i in range(4)]
    problem = n.SketchProblem()
    problem.entities = lines
    dimensions = [n.SketchConstraint(K.LENGTH, [ref(lines[0])], width),
                  n.SketchConstraint(K.LENGTH, [ref(lines[1])], height)]
    problem.constraints = [n.SketchConstraint(K.COINCIDENT, [ref(lines[i], E.LINE_END), ref(lines[(i + 1) % 4], E.LINE_START)])
                           for i in range(4)] + [
        n.SketchConstraint(K.HORIZONTAL if i % 2 == 0 else K.VERTICAL, [ref(line)])
        for i, line in enumerate(lines)] + dimensions + [
        n.SketchConstraint(K.COINCIDENT, [ref(lines[0], E.LINE_START), n.SketchReference()])]
    return problem, dimensions[0].id, dimensions[1].id


def circle(radius=2, x=5, y=5):
    entity = n.SketchEntity(n.Circle2(n.Point2(x, y), radius))
    problem = n.SketchProblem()
    problem.entities = [entity]
    radius_constraint = n.SketchConstraint(K.RADIUS, [ref(entity)], radius)
    problem.constraints = [radius_constraint,
        n.SketchConstraint(K.DISTANCE_X, [n.SketchReference(), ref(entity, E.CIRCLE_CENTER)], x),
        n.SketchConstraint(K.DISTANCE_Y, [n.SketchReference(), ref(entity, E.CIRCLE_CENTER)], y)]
    return problem, radius_constraint.id


def constraint(problem, identifier):
    return next(item for item in problem.constraints if item.id == identifier)


class SketchParameterChecks(unittest.TestCase):
    def assert_atomic_rejection(self, document, mutation, exception=ValueError):
        before = copy.deepcopy(document.to_snapshot())
        with self.assertRaises(exception):
            mutation()
        self.assertEqual(document.to_snapshot(), before)

    def test_binding_resolves_numeric_copy_without_rewriting_guesses_or_identities(self):
        document = n.Document()
        document.add_parameter("width", 30, "mm")
        document.add_parameter("height", 12, "mm")
        problem, width_id, height_id = rectangle()
        sketch = document.add_sketch("Profile", problem)
        document.set_sketch_dimension(sketch, width_id, "width")
        document.set_sketch_dimension(sketch, height_id, "height")
        raw = document.get_sketch(sketch)
        self.assertEqual(constraint(raw, width_id).parameter, "width")
        self.assertEqual(constraint(raw, width_id).value, 0)
        self.assertEqual(raw.entities[0].geometry.end.x, 20)
        with self.assertRaises(ValueError):
            n.new_sketch_solver().solve(raw)
        before = document.to_snapshot()
        evaluated = document.evaluated_sketch(sketch)
        self.assertEqual([item.id for item in evaluated.entities], [item.id for item in problem.entities])
        self.assertEqual([item.id for item in evaluated.constraints], [item.id for item in problem.constraints])
        self.assertEqual(constraint(evaluated, width_id).value, 30)
        self.assertTrue(all(item.parameter is None for item in evaluated.constraints))
        report = n.new_sketch_solver().solve(evaluated)
        self.assertEqual(report.state, n.SketchSolveState.FULLY_CONSTRAINED, report.diagnostics)
        self.assertAlmostEqual(evaluated.entities[0].geometry.end.x, 30, places=6)
        self.assertAlmostEqual(evaluated.entities[1].geometry.end.y, 12, places=6)
        self.assertEqual(document.to_snapshot(), before)
        document.set_parameter_value("width", 35)
        self.assertEqual(constraint(document.evaluated_sketch(sketch), width_id).value, 35)
        self.assertEqual(document.get_sketch(sketch).entities[0].geometry.end.x, 20)
        # Returned copies survive vector replacement and destruction of owner.
        detached = document.evaluated_sketch(sketch)
        document.replace_sketch(sketch, document.get_sketch(sketch))
        del document
        gc.collect()
        self.assertEqual(constraint(detached, width_id).value, 35)
        self.assertEqual(constraint(raw, width_id).parameter, "width")

    def test_all_dimensional_kinds_bind_and_geometric_constraints_reject_bindings(self):
        for kind in (K.DISTANCE, K.LENGTH, K.RADIUS, K.DIAMETER, K.DISTANCE_X, K.DISTANCE_Y):
            with self.subTest(kind=kind):
                document = n.Document()
                document.add_parameter("size", 3, "mm")
                line = n.SketchEntity(n.Line2(n.Point2(0, 0), n.Point2(4, 0)))
                curve = n.SketchEntity(n.Circle2(n.Point2(3, 4), 2))
                targets = [ref(line)] if kind == K.LENGTH else [ref(curve)] if kind in (K.RADIUS, K.DIAMETER) else [n.SketchReference(), ref(curve, E.CIRCLE_CENTER)]
                dimension = n.SketchConstraint(kind, targets, 99)
                dimension.parameter = "size"
                problem = n.SketchProblem()
                problem.entities, problem.constraints = [line, curve], [dimension]
                sketch = document.add_sketch("Bound definition", problem)
                self.assertEqual(document.get_sketch(sketch).constraints[0].value, 0)
                self.assertEqual(document.evaluated_sketch(sketch).constraints[0].value, 3)
                if kind in (K.DISTANCE_X, K.DISTANCE_Y):
                    document.set_parameter_value("size", -3)
                    self.assertEqual(document.evaluated_sketch(sketch).constraints[0].value, -3)
        document = n.Document()
        document.add_parameter("size", 3, "mm")
        document.add_parameter("angle", 30, "deg")
        document.add_parameter("factor", 2)
        problem, width_id, _height_id = rectangle()
        sketch = document.add_sketch("Profile", problem)
        geometric_id = problem.constraints[0].id
        for name in ("missing", "angle", "factor"):
            self.assert_atomic_rejection(document, lambda: document.set_sketch_dimension(sketch, width_id, name))
        self.assert_atomic_rejection(document, lambda: document.set_sketch_dimension(sketch, geometric_id, "size"))
        self.assert_atomic_rejection(document, lambda: document.set_sketch_dimension(sketch, width_id, -2))
        self.assert_atomic_rejection(document, lambda: document.set_sketch_dimension(sketch, width_id, 0))
        self.assert_atomic_rejection(document, lambda: document.set_sketch_dimension(sketch, n.new_document_id(), 3))
        self.assert_atomic_rejection(document, lambda: document.set_sketch_dimension(n.new_document_id(), width_id, 3))
        bad = document.get_sketch(sketch)
        items = bad.constraints
        items[0].parameter = "size"
        bad.constraints = items
        self.assert_atomic_rejection(document, lambda: document.replace_sketch(sketch, bad))

    def test_dimension_definition_noops_literal_override_and_revision_overflow(self):
        document = n.Document()
        document.add_parameter("width", 20, "mm")
        problem, width_id, _ = rectangle()
        sketch = document.add_sketch("Profile", problem)
        revision = document.revision
        document.set_sketch_dimension(sketch, width_id, 20)
        self.assertEqual(document.revision, revision)
        document.set_sketch_dimension(sketch, width_id, "width")
        self.assertEqual(document.revision, revision + 1)
        document.set_sketch_dimension(sketch, width_id, "width")
        self.assertEqual(document.revision, revision + 1)
        document.set_sketch_dimension(sketch, width_id, 20)
        self.assertEqual(document.revision, revision + 2)
        self.assertIsNone(constraint(document.get_sketch(sketch), width_id).parameter)
        self.assertEqual(document.to_snapshot()["sketches"][0]["version"], 1)
        document.set_parameter_value("width", 50)
        self.assertEqual(constraint(document.evaluated_sketch(sketch), width_id).value, 20)
        snapshot = document.to_snapshot()
        snapshot["revision"] = 2 ** 64 - 1
        exhausted = n.Document.from_snapshot(snapshot)
        exhausted.set_sketch_dimension(sketch, width_id, 20)
        self.assert_atomic_rejection(exhausted, lambda: exhausted.set_sketch_dimension(sketch, width_id, "width"), OverflowError)
        document.set_sketch_dimension(sketch, width_id, "width")
        snapshot = document.to_snapshot()
        snapshot["revision"] = 2 ** 64 - 1
        exhausted = n.Document.from_snapshot(snapshot)
        exhausted.set_sketch_dimension(sketch, width_id, "width")
        self.assert_atomic_rejection(exhausted, lambda: exhausted.set_sketch_dimension(sketch, width_id, 50), OverflowError)

    def test_snapshot_binding_version_and_strict_numeric_or_parameter_alternatives(self):
        document = n.Document()
        document.add_parameter("width", 30, "mm")
        problem, width_id, _ = rectangle()
        sketch = document.add_sketch("Profile", problem)
        numeric = document.to_snapshot()
        self.assertEqual(numeric["sketches"][0]["version"], 1)
        document.set_sketch_dimension(sketch, width_id, "width")
        snapshot = document.to_snapshot()
        self.assertEqual(snapshot["schema_version"], 8)
        self.assertEqual(snapshot["sketches"][0]["version"], 2)
        dimension = next(item for item in snapshot["sketches"][0]["constraints"] if item["id"] == width_id)
        self.assertEqual(set(dimension), {"id", "type", "targets", "parameter"})
        self.assertEqual(dimension["parameter"], "width")
        restored = n.Document.from_snapshot(json.loads(json.dumps(snapshot)))
        self.assertEqual(restored.to_snapshot(), snapshot)
        self.assertEqual(constraint(restored.evaluated_sketch(sketch), width_id).value, 30)
        for mutation in (
            lambda s, c: c.update(value=30),
            lambda s, c: c.pop("parameter"),
            lambda s, c: c.update(parameter=3),
            lambda s, c: c.update(parameter="missing"),
            lambda s, c: s["sketches"][0].update(version=1),
            lambda s, c: s.update(schema_version=5),
            lambda s, c: s.update(schema_version=9),
            lambda s, c: s["sketches"][0].update(version=4),
        ):
            candidate = copy.deepcopy(snapshot)
            item = next(item for item in candidate["sketches"][0]["constraints"] if item["id"] == width_id)
            mutation(candidate, item)
            with self.assertRaises(ValueError):
                n.Document.from_snapshot(candidate)

    def test_profile_parameters_drive_history_with_stable_references_cache_and_failure_states(self):
        document = n.Document()
        document.add_parameter("width", 20, "mm")
        document.add_parameter_expression("height", "width / 2", "mm")
        profile, width_id, height_id = rectangle()
        sketch = document.add_sketch("Plate", profile)
        document.set_sketch_dimension(sketch, width_id, "width")
        document.set_sketch_dimension(sketch, height_id, "height")
        hole, _ = circle()
        hole_sketch = document.add_sketch("Hole", hole)
        base = document.add_extrusion("Plate", sketch, 5)
        cut = document.add_subtractive_extrusion("Hole", base, hole_sketch, 5)
        independent = document.add_extrusion("Independent", hole_sketch, 2)
        body = document.get_feature(base).body_id
        independent_body = document.get_feature(independent).body_id
        target = n.PersistentReference(body, n.TopologyKind.FACE, n.ReferenceAnchor(base, "cap:start"))
        evaluator = n.FeatureEvaluator()
        self.assertEqual(evaluator.evaluate(document).computed_count, 3)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 1000 - 20 * math.pi, places=8)
        # Cap splits caused by the hole can be explicit; a side face would also
        # survive dimensions, but the pre-cut body lineage is the named intent.
        initial_reference = evaluator.resolve_reference(document, target)
        stable_state = initial_reference.state
        other_generation = evaluator.tessellate(document, independent_body)["geometry_generation"]
        definitions = document.to_snapshot()["sketches"]
        document.set_parameter_value("width", 30)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (2, 1))
        self.assertEqual([entry.state for entry in report.features], [S.UP_TO_DATE] * 3)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 2250 - 20 * math.pi, places=8)
        self.assertEqual(evaluator.tessellate(document, independent_body)["geometry_generation"], other_generation)
        self.assertEqual(evaluator.resolve_reference(document, target).state, stable_state)
        self.assertEqual(document.to_snapshot()["sketches"], definitions)
        generation = evaluator.tessellate(document, body)["geometry_generation"]
        document.set_parameter_value("width", 0)  # Valid parameter, invalid resolved LENGTH.
        report = evaluator.evaluate(document)
        self.assertEqual([entry.state for entry in report.features], [S.FAILED, S.BLOCKED, S.UP_TO_DATE])
        self.assertTrue(report.features[0].diagnostic)
        display = evaluator.tessellate(document, body)
        self.assertFalse(display["current"])
        self.assertEqual(display["geometry_generation"], generation)
        with self.assertRaises(RuntimeError):
            evaluator.volume_mm3(document, body)
        document.set_parameter_value("width", 25)
        self.assertEqual([entry.state for entry in evaluator.evaluate(document).features], [S.UP_TO_DATE] * 3)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 25 * 12.5 * 5 - 20 * math.pi, places=8)

    def test_bound_radius_on_arbitrary_plane_measures_analytic_geometry(self):
        document = n.Document()
        document.add_parameter("radius", 3, "mm")
        problem, radius_id = circle(radius=2, x=0, y=0)
        problem.plane.origin = n.Vector3(10, 20, 30)
        problem.plane.x_axis, problem.plane.y_axis = n.Vector3(0, 1, 0), n.Vector3(0, 0, 1)
        sketch = document.add_sketch("Offset circular profile", problem)
        document.set_sketch_dimension(sketch, radius_id, "radius")
        feature = document.add_extrusion("Cylinder", sketch, 4)
        body = document.get_feature(feature).body_id
        target = n.PersistentReference(body, n.TopologyKind.FACE, n.ReferenceAnchor(feature, "cap:start"))
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), math.pi * 9 * 4, places=8)
        self.assertEqual(evaluator.resolve_reference(document, target).state, n.ReferenceState.RESOLVED)
        self.assertAlmostEqual(evaluator.measure_reference(document, target).area_mm2, math.pi * 9, places=8)
        document.set_parameter_value("radius", 5)
        evaluator.evaluate(document)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), math.pi * 25 * 4, places=8)
        self.assertAlmostEqual(evaluator.measure_reference(document, target).area_mm2, math.pi * 25, places=8)
        self.assertEqual(document.get_sketch(sketch).entities[0].geometry.radius, 2)
        self.assertEqual(document.get_sketch(sketch).plane.origin.z, 30)


unittest.main(argv=[sys.argv[0]])
