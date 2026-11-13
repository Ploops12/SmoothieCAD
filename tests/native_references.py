"""Persistent construction references across rebuilds and actual OCCT cuts."""

import argparse
import copy
import json
from pathlib import Path
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
sys.path.insert(0, str(Path(options.module_dir).resolve()))
sys.path.insert(0, str(Path(__file__).parent))
import _native as n
from native_features import rectangle, circle

T, R = n.TopologyKind, n.ReferenceState


def plate():
    doc = n.Document()
    problem = rectangle(20, 30)
    sketch = doc.add_sketch("Plate", problem)
    feature = doc.add_extrusion("Base", sketch, 5)
    return doc, sketch, feature, doc.get_feature(feature).body_id


def anchor(doc, feature, body, kind, key):
    return n.PersistentReference(body, kind, n.ReferenceAnchor(feature, key))


class ReferenceChecks(unittest.TestCase):
    def test_all_extrusion_domains_capture_and_survive_reorder_dimensions_and_reopen(self):
        doc, sketch, feature, body = plate()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(doc)
        display = evaluator.tessellate(doc, body)
        counts = [(T.FACE, display["face_count"]), (T.EDGE, len(display["edge_offsets"]) - 1),
                  (T.VERTEX, len(display["cad_vertices"]) // 3)]
        references = []
        for kind, count in counts:
            for index in range(count):
                target = evaluator.capture_reference(doc, body, kind, index)
                result = evaluator.resolve_reference(doc, target)
                self.assertEqual(result.state, R.RESOLVED)
                self.assertEqual([(item.kind, item.index) for item in result.candidates], [(kind, index)])
                references.append((doc.add_reference(f"{kind.name} {index}", target), target))
                evaluator.evaluate(doc)
        # Changing array order cannot redefine a semantic source identity.
        problem = doc.get_sketch(sketch)
        problem.entities = list(reversed(problem.entities))
        doc.replace_sketch(sketch, problem)
        doc.set_feature_depth(feature, 8)
        self.assertEqual(evaluator.evaluate(doc).computed_count, 1)
        for _id, target in references:
            self.assertEqual(evaluator.resolve_reference(doc, target).state, R.RESOLVED)
        reopened = n.Document.from_snapshot(json.loads(json.dumps(doc.to_snapshot())))
        rebuilt = n.FeatureEvaluator()
        report = rebuilt.evaluate(reopened)
        self.assertEqual(len(report.references), len(references))
        self.assertTrue(all(entry.resolution.state == R.RESOLVED for entry in report.references))
        self.assertEqual([entry.reference_id for entry in report.references], [item[0] for item in references])
        # Saved references contain no display topology ordinals or generations.
        self.assertTrue(all(set(item) == {"id", "name", "version", "body", "kind", "feature", "key"}
                            for item in reopened.to_snapshot()["references"]))

    def test_earlier_constraint_edit_keeps_semantic_side_and_hole_references(self):
        doc, sketch, feature, body = plate()
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(doc)
        cap = anchor(doc, feature, body, T.FACE, "cap:end")
        doc.add_reference("Top", cap)
        hole_profile = circle()
        hole_profile.plane.origin.z = -1
        hole = doc.add_sketch("Hole", hole_profile)
        cut = doc.add_subtractive_extrusion("Drill", feature, hole, 7)
        evaluator.evaluate(doc)
        tool_side = anchor(doc, cut, body, T.FACE, "side:" + doc.get_sketch(hole).entities[0].id)
        self.assertEqual(evaluator.resolve_reference(doc, cap).state, R.RESOLVED)
        self.assertEqual(evaluator.resolve_reference(doc, tool_side).state, R.RESOLVED)
        doc.add_reference("Hole wall", tool_side)
        # Every currently generated intersection edge either has a unique
        # history name or explicitly refuses persistence, never ordinal fallback.
        evaluator.evaluate(doc)
        edge_count = len(evaluator.tessellate(doc, body)["edge_offsets"]) - 1
        generated = []
        for index in range(edge_count):
            try:
                target = evaluator.capture_reference(doc, body, T.EDGE, index)
            except ValueError:
                continue
            self.assertEqual(evaluator.resolve_reference(doc, target).state, R.RESOLVED)
            if target.anchor.key.startswith("derived:"):
                generated.append(target)
        self.assertTrue(generated, "No OCCT-generated intersection edge acquired provenance")
        problem = doc.get_sketch(sketch)
        constraints = problem.constraints
        for constraint in constraints:
            if constraint.kind == n.SketchConstraintKind.LENGTH and constraint.targets[0].entity == problem.entities[0].id:
                constraint.value = 25
        problem.constraints = constraints
        doc.replace_sketch(sketch, problem)
        self.assertEqual(evaluator.evaluate(doc).computed_count, 2)
        for target in [cap, tool_side, *generated]:
            self.assertEqual(evaluator.resolve_reference(doc, target).state, R.RESOLVED)

    def test_actual_cut_split_deletion_unknown_and_explicit_repair(self):
        doc, _sketch, feature, body = plate()
        top = anchor(doc, feature, body, T.FACE, "cap:end")
        bottom = anchor(doc, feature, body, T.FACE, "cap:start")
        saved = doc.add_reference("Original top", top)
        # A transverse slot removes a strip through the full plate. Top/bottom
        # faces split into two actual OCCT faces; the saved reference must not
        # silently choose either fragment.
        slot = rectangle(2, 40)
        slot.plane.origin = n.Vector3(9, -5, 0)
        slot_sketch = doc.add_sketch("Through slot", slot)
        cut = doc.add_subtractive_extrusion("Split plate", feature, slot_sketch, 6)
        evaluator = n.FeatureEvaluator()
        report = evaluator.evaluate(doc)
        self.assertEqual(report.features[-1].state, n.EvaluationState.UP_TO_DATE, report.features[-1].diagnostic)
        result = evaluator.resolve_reference(doc, top)
        self.assertEqual(result.state, R.SPLIT)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(report.references[0].resolution.state, R.SPLIT)
        self.assertEqual(evaluator.resolve_reference(doc, bottom).state, R.SPLIT)
        # Select a uniquely named slot wall as the explicit replacement region.
        side = anchor(doc, cut, body, T.FACE, "side:" + slot.entities[1].id)
        self.assertEqual(evaluator.resolve_reference(doc, side).state, R.RESOLVED)
        doc.repair_reference(saved, side)
        self.assertEqual(evaluator.evaluate(doc).references[0].resolution.state, R.RESOLVED)
        # A shallow removal at the upper surface deletes the old upper cap
        # without substituting the lower surface of the pocket.
        doc2, _, base2, body2 = plate()
        cover = rectangle(40, 50)
        cover.plane.origin = n.Vector3(-10, -10, 4)
        cutter = doc2.add_sketch("Upper slab", cover)
        doc2.add_subtractive_extrusion("Remove original top", base2, cutter, 2)
        other = n.FeatureEvaluator()
        other.evaluate(doc2)
        deleted = other.resolve_reference(doc2, anchor(doc2, base2, body2, T.FACE, "cap:end"))
        self.assertEqual(deleted.state, R.DELETED)
        self.assertEqual(deleted.candidates, [])
        unknown = other.resolve_reference(doc2, anchor(doc2, base2, body2, T.FACE, "unknown:repairable"))
        self.assertEqual(unknown.state, R.AMBIGUOUS)

    def test_unavailable_stale_divergent_and_wrong_body_are_not_current_references(self):
        doc, _, feature, body = plate()
        target = anchor(doc, feature, body, T.FACE, "cap:end")
        doc.add_reference("Top", target)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(doc)
        changed = copy.deepcopy(doc.to_snapshot())
        changed["features"][0]["depth"]["value_mm"] = 6
        divergent = n.Document.from_snapshot(changed)
        with self.assertRaises(RuntimeError):
            evaluator.resolve_reference(divergent, target)
        with self.assertRaises(ValueError):
            evaluator.capture_reference(doc, body, T.FACE, 9999)
        doc.set_feature_depth(feature, 0)
        report = evaluator.evaluate(doc)
        self.assertEqual(report.references[0].resolution.state, R.UNAVAILABLE)
        with self.assertRaises(RuntimeError):
            evaluator.capture_reference(doc, body, T.FACE, 0)
        doc.set_feature_depth(feature, 5)
        doc.set_feature_suppressed(feature, True)
        self.assertEqual(evaluator.evaluate(doc).references[0].resolution.state, R.UNAVAILABLE)
        doc.set_feature_suppressed(feature, False)
        doc.set_rollback_before(feature)
        self.assertEqual(evaluator.evaluate(doc).references[0].resolution.state, R.UNAVAILABLE)
        doc.set_rollback_before(None)
        evaluator.evaluate(doc)
        invalid = n.PersistentReference(n.new_document_id(), T.FACE, target.anchor)
        with self.assertRaises(ValueError):
            evaluator.resolve_reference(doc, invalid)

    def test_replacing_source_id_never_reattaches_to_equal_geometry(self):
        doc, sketch, feature, body = plate()
        profile = doc.get_sketch(sketch)
        removed = profile.entities[0].id
        target = anchor(doc, feature, body, T.FACE, "side:" + removed)
        saved = doc.add_reference("Side tied to original line", target)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(doc)
        self.assertEqual(evaluator.resolve_reference(doc, target).state, R.RESOLVED)
        # Replace the entire sketch with coincident geometry but fresh entity
        # IDs. Geometry equality is not permission to transfer design identity.
        doc.replace_sketch(sketch, rectangle(20, 30))
        report = evaluator.evaluate(doc)
        self.assertEqual(report.references[0].reference_id, saved)
        self.assertEqual(report.references[0].resolution.state, R.DELETED)
        self.assertEqual(evaluator.resolve_reference(doc, target).candidates, [])

    def test_branch_diagnostic_does_not_depend_on_uuid_selected_start(self):
        doc, sketch, feature, _body = plate()
        profile = doc.get_sketch(sketch)
        # Make the dangling branch the lexicographically first source so the
        # canonical walk cannot mask a branched junction as merely open.
        snapshot = doc.to_snapshot()
        snapshot["sketches"][0]["entities"].append({"id": "00000000-0000-4000-8000-000000000001", "type": "line",
                                                   "start": [0, 0], "end": [-2, -2]})
        changed = n.Document.from_snapshot(snapshot)
        evaluator = n.FeatureEvaluator()
        report = evaluator.evaluate(changed)
        self.assertEqual(report.features[0].state, n.EvaluationState.FAILED)
        self.assertIn("ambiguous", report.features[0].diagnostic)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
