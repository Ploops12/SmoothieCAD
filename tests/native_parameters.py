"""Directed, unit-aware parameter definitions and their evaluated feature inputs."""

import argparse
import copy
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


def values(document):
    return {item.name: item.value for item in document.parameters}


def parameter(document, name):
    return next(item for item in document.parameters if item.name == name)


def profile():
    entity = n.SketchEntity(n.Circle2(n.Point2(0, 0), 2))
    center = n.SketchReference(entity.id, n.SketchElement.CIRCLE_CENTER)
    problem = n.SketchProblem()
    problem.entities = [entity]
    problem.constraints = [
        n.SketchConstraint(n.SketchConstraintKind.RADIUS,
                           [n.SketchReference(entity.id, n.SketchElement.GEOMETRY)], 2),
        n.SketchConstraint(n.SketchConstraintKind.DISTANCE_X, [n.SketchReference(), center], 0),
        n.SketchConstraint(n.SketchConstraintKind.DISTANCE_Y, [n.SketchReference(), center], 0),
    ]
    return problem


class ParameterChecks(unittest.TestCase):
    def assert_rejected_atomically(self, document, mutation, exception=ValueError):
        before = copy.deepcopy(document.to_snapshot())
        with self.assertRaises(exception):
            mutation()
        self.assertEqual(document.to_snapshot(), before)

    def test_multihop_dependencies_units_and_literal_override(self):
        document = n.Document()
        document.add_parameter("width", 20, "mm")
        document.add_parameter("factor", 2)
        document.add_parameter_expression("half_width", "width / factor", "mm")
        document.add_parameter_expression("depth", "half_width + 500 um", "mm")
        document.add_parameter_expression("unrelated", "3 * (2 + 1)")
        self.assertEqual(values(document), {"width": 20, "factor": 2, "half_width": 10,
                                            "depth": 10.5, "unrelated": 9})
        document.set_parameter_value("width", 30)
        self.assertEqual(values(document)["half_width"], 15)
        self.assertEqual(values(document)["depth"], 15.5)
        self.assertEqual(values(document)["unrelated"], 9)
        revision = document.revision
        document.set_parameter_expression("depth", "half_width + 500 um")
        self.assertEqual(document.revision, revision)
        document.set_parameter_value("depth", 15.5)
        self.assertEqual(document.revision, revision + 1)
        self.assertIsNone(parameter(document, "depth").expression)
        document.set_parameter_value("width", 40)
        self.assertEqual(values(document)["half_width"], 20)
        self.assertEqual(values(document)["depth"], 15.5)
        revision = document.revision
        document.set_parameter_value("depth", 15.5)
        self.assertEqual(document.revision, revision)

    def test_dimensional_intermediates_conversions_precedence_and_quoted_names(self):
        document = n.Document()
        document.add_parameter("width", 10, "mm")
        document.add_parameter("height", 3, "mm")
        document.add_parameter_expression("length", "(width * height) / width", "mm")
        document.add_parameter_expression("ratio", "width / (2 * height)")
        document.add_parameter_expression("mixed", "1 m + 2cm - 3 mm + 4000um", "mm")
        document.add_parameter_expression("arithmetic", "-(2 + 3) * -4 / +2 + 1e-2")
        document.add_parameter_expression("angle", f"{math.pi:.17g} rad + 90 deg", "deg")
        document.add_parameter("plate width µm", 7, "mm")
        escaped_name = 'quote" and \\ name'
        document.add_parameter(escaped_name, 2, "mm")
        quoted = json.dumps(escaped_name, ensure_ascii=False)
        document.add_parameter_expression("quoted", f'"plate width µm" + {quoted}', "mm")
        self.assertEqual(values(document)["length"], 3)
        self.assertAlmostEqual(values(document)["ratio"], 10 / 6)
        self.assertEqual(values(document)["mixed"], 1021)
        self.assertAlmostEqual(values(document)["arithmetic"], 10.01)
        self.assertAlmostEqual(values(document)["angle"], 270, places=11)
        self.assertEqual(values(document)["quoted"], 9)
        for expression, unit in (("width + 1", "mm"), ("width + angle", "mm"),
                                 ("angle", "unitless"), ("width / height", "mm"),
                                 ("width * height", "mm"), ("1", "deg")):
            with self.subTest(expression=expression):
                self.assert_rejected_atomically(document, lambda: document.add_parameter_expression("bad", expression, unit))

    def test_unknown_cycle_and_arithmetic_errors_leave_all_definitions_unchanged(self):
        document = n.Document()
        document.add_parameter("base", 4, "mm")
        document.add_parameter("divisor", 2)
        document.add_parameter_expression("depth", "base / divisor", "mm")
        document.add_parameter_expression("consumer", "depth + 1 mm", "mm")
        for mutation in (
            lambda: document.set_parameter_expression("base", "consumer"),
            lambda: document.set_parameter_expression("base", "base"),
            lambda: document.set_parameter_expression("depth", "missing + 1 mm"),
            lambda: document.add_parameter_expression("new", "0 * missing"),
            lambda: document.set_parameter_value("divisor", 0),
            lambda: document.set_parameter_expression("depth", "1 mm / 0"),
            lambda: document.set_parameter_expression("depth", "1e308 * 1e308 mm"),
            lambda: document.add_parameter_expression("new", "1e999"),
            lambda: document.set_parameter_expression("missing", "1 mm"),
            lambda: document.add_parameter_expression("base", "1 mm", "mm"),
        ):
            self.assert_rejected_atomically(document, mutation)
        for expression in ("", " ", "width**2", "pow(2,3)", "1 +", "(2", "2 3", "1 inch",
                           "[1,2]", "__import__('os')", '"unterminated', '"bad\\q"', "1\x00+2"):
            with self.subTest(expression=expression):
                self.assert_rejected_atomically(document, lambda: document.add_parameter_expression("new", expression))

    def test_schema_records_restore_forward_references_and_reject_cached_values(self):
        document = n.Document()
        document.add_parameter("base", 5, "mm")
        document.add_parameter_expression("middle", "base * 2", "mm")
        document.add_parameter_expression("depth", "middle + 1 mm", "mm")
        snapshot = document.to_snapshot()
        self.assertEqual(snapshot["schema_version"], 7)
        self.assertEqual(snapshot["parameters"][0], {"name": "base", "value": 5, "unit": "mm"})
        self.assertEqual(snapshot["parameters"][1], {"name": "middle", "expression": "base * 2", "unit": "mm"})
        snapshot["parameters"].reverse()
        restored = n.Document.from_snapshot(json.loads(json.dumps(snapshot)))
        self.assertEqual(values(restored), {"depth": 11, "middle": 10, "base": 5})
        self.assertEqual(restored.to_snapshot(), snapshot)
        self.assertEqual(restored.revision, document.revision)
        malformed = []
        def case(mutate):
            candidate = copy.deepcopy(snapshot)
            mutate(candidate)
            malformed.append(candidate)
        case(lambda s: s["parameters"][0].update(value=999))
        case(lambda s: s["parameters"][0].pop("expression"))
        case(lambda s: s["parameters"][0].update(expression=42))
        case(lambda s: s["parameters"][0].update(expression=None))
        case(lambda s: s["parameters"][0].update(expression="middle + 1"))
        case(lambda s: s["parameters"][0].update(expression="middle / 0"))
        case(lambda s: s["parameters"][-1].update(expression="depth"))
        def add_cycle(s):
            s["parameters"][-1].pop("value")
            s["parameters"][-1]["expression"] = "depth"
        case(add_cycle)
        case(lambda s: s["parameters"][-1].update(value=True))
        case(lambda s: s.update(schema_version=4))
        case(lambda s: s.update(schema_version=8))
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    n.Document.from_snapshot(candidate)
        self.assertEqual(document.to_snapshot()["parameters"][1]["expression"], "base * 2")

    def test_limits_and_revision_exhaustion_fail_before_publication(self):
        document = n.Document()
        document.add_parameter("length", 5, "mm")
        for expression in ("1+" * 40000 + "1", "(" * 300 + "1" + ")" * 300, "-" * 300 + "1"):
            self.assert_rejected_atomically(document, lambda: document.add_parameter_expression("large", expression))
        snapshot = document.to_snapshot()
        snapshot["parameters"] = [{"name": f"p{i}", "unit": "unitless", "expression": f"p{i+1}"}
                                   for i in range(600)] + [{"name": "p600", "unit": "unitless", "value": 1}]
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(snapshot)
        snapshot = document.to_snapshot()
        snapshot["revision"] = 2 ** 64 - 1
        exhausted = n.Document.from_snapshot(snapshot)
        exhausted.set_parameter_value("length", 5)
        self.assert_rejected_atomically(exhausted, lambda: exhausted.set_parameter_expression("length", "5 mm"), OverflowError)
        self.assert_rejected_atomically(exhausted, lambda: exhausted.add_parameter_expression("other", "2 mm", "mm"), OverflowError)
        snapshot["parameters"] = [{"name": "length", "unit": "mm", "expression": "5 mm"}]
        exhausted = n.Document.from_snapshot(snapshot)
        exhausted.set_parameter_expression("length", "5 mm")
        self.assert_rejected_atomically(exhausted, lambda: exhausted.set_parameter_value("length", 5), OverflowError)

    def test_feature_cache_tracks_resolved_inputs_not_unrelated_parameter_edits(self):
        document = n.Document()
        document.add_parameter("base", 3, "mm")
        document.add_parameter("factor", 2)
        document.add_parameter_expression("depth", "base * factor", "mm")
        document.add_parameter("unrelated", 12)
        sketch = document.add_sketch("Circle", profile())
        feature = document.add_extrusion("Driven", sketch, "depth")
        independent = document.add_extrusion("Independent", sketch, 2)
        body = document.get_feature(feature).body_id
        other_body = document.get_feature(independent).body_id
        evaluator = n.FeatureEvaluator()
        self.assertEqual(evaluator.evaluate(document).computed_count, 2)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), math.pi * 4 * 6, places=9)
        other_generation = evaluator.tessellate(document, other_body)["geometry_generation"]
        document.set_parameter_value("unrelated", 13)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (0, 2))
        document.set_parameter_value("factor", 3)
        with self.assertRaises(RuntimeError):
            evaluator.volume_mm3(document, body)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (1, 1))
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), math.pi * 4 * 9, places=9)
        self.assertEqual(evaluator.tessellate(document, other_body)["geometry_generation"], other_generation)
        before = document.to_snapshot()
        self.assert_rejected_atomically(document, lambda: document.set_parameter_expression("base", "depth"))
        self.assertEqual(document.to_snapshot(), before)
        self.assertTrue(evaluator.matches_document(document))
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), math.pi * 4 * 9, places=9)
        # A valid expression graph can still produce an invalid feature input.
        # Its failed geometry follows the ordinary last-valid/stale contract.
        generation = evaluator.tessellate(document, body)["geometry_generation"]
        document.set_parameter_value("factor", 0)
        report = evaluator.evaluate(document)
        self.assertEqual(report.features[0].state, n.EvaluationState.FAILED)
        display = evaluator.tessellate(document, body)
        self.assertFalse(display["current"])
        self.assertEqual(display["geometry_generation"], generation)
        with self.assertRaises(RuntimeError):
            evaluator.volume_mm3(document, body)
        document.set_parameter_value("factor", 3)
        self.assertEqual(evaluator.evaluate(document).features[0].state, n.EvaluationState.UP_TO_DATE)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), math.pi * 4 * 9, places=9)


unittest.main(argv=[sys.argv[0]])
