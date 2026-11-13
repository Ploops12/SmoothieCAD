"""Current-document and persistent-reference guards for exact geometry queries."""

import argparse
import copy
from pathlib import Path
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
sys.path.insert(0, str(Path(options.module_dir).resolve()))
sys.path.insert(0, str(Path(__file__).parent))
import _native as n
from native_features import rectangle


class QueryGuards(unittest.TestCase):
    def test_queries_require_current_geometry_and_resolved_reference(self):
        doc = n.Document()
        sketch = doc.add_sketch("Plate", rectangle(20, 30))
        feature = doc.add_extrusion("Plate", sketch, 5)
        body = doc.get_feature(feature).body_id
        target = n.PersistentReference(body, n.TopologyKind.FACE, n.ReferenceAnchor(feature, "cap:end"))
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(doc)
        face = evaluator.resolve_reference(doc, target).candidates[0]
        self.assertAlmostEqual(evaluator.measure_reference(doc, target).area_mm2, 600, places=8)
        self.assertAlmostEqual(evaluator.measure_element(doc, body, face).area_mm2, 600, places=8)
        closest = evaluator.closest_point_reference(doc, target, n.Vector3(10, 10, 8))
        self.assertAlmostEqual(closest.point_mm.z, 5, places=10)
        hit = evaluator.ray_cast(doc, body, n.Vector3(10, 10, 20), n.Vector3(0, 0, -1))
        self.assertEqual(hit.face_index, face.index)
        self.assertAlmostEqual(hit.normal.z, 1, places=10)
        divergent = copy.deepcopy(doc.to_snapshot())
        divergent["features"][0]["depth"]["value_mm"] = 7
        changed = n.Document.from_snapshot(divergent)
        for query in (lambda source: evaluator.measure_reference(source, target),
                      lambda source: evaluator.measure_element(source, body, face),
                      lambda source: evaluator.closest_point_element(source, body, face, n.Vector3(10, 10, 8)),
                      lambda source: evaluator.ray_cast(source, body, n.Vector3(10, 10, 20), n.Vector3(0, 0, -1))):
            with self.assertRaises(RuntimeError):
                query(changed)
        doc.set_feature_depth(feature, 0)
        evaluator.evaluate(doc)
        for query in (lambda: evaluator.measure_element(doc, body, face),
                      lambda: evaluator.measure_reference(doc, target),
                      lambda: evaluator.closest_point_reference(doc, target, n.Vector3(10, 10, 8)),
                      lambda: evaluator.ray_cast(doc, body, n.Vector3(10, 10, 20), n.Vector3(0, 0, -1))):
            with self.assertRaises(RuntimeError):
                query()

    def test_split_reference_cannot_be_measured_or_used_for_snapping(self):
        doc = n.Document()
        sketch = doc.add_sketch("Plate", rectangle(20, 30))
        feature = doc.add_extrusion("Base", sketch, 5)
        body = doc.get_feature(feature).body_id
        target = n.PersistentReference(body, n.TopologyKind.FACE, n.ReferenceAnchor(feature, "cap:end"))
        slot = rectangle(2, 40)
        slot.plane.origin = n.Vector3(9, -5, 0)
        cut = doc.add_sketch("Slot", slot)
        doc.add_subtractive_extrusion("Split", feature, cut, 6)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(doc)
        result = evaluator.resolve_reference(doc, target)
        self.assertEqual(result.state, n.ReferenceState.SPLIT)
        with self.assertRaises(RuntimeError):
            evaluator.measure_reference(doc, target)
        with self.assertRaises(RuntimeError):
            evaluator.closest_point_reference(doc, target, n.Vector3(10, 10, 8))
        # An explicitly requested current candidate may still be inspected,
        # without persisting that ordinal or silently choosing it for the ref.
        self.assertAlmostEqual(sum(evaluator.measure_element(doc, body, item).area_mm2 for item in result.candidates), 540, places=8)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
