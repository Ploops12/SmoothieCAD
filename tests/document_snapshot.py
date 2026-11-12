"""Versioned BlenderCAD document snapshot round-trip and validation checks."""

import argparse
import copy
import json
from pathlib import Path
import sys
import unittest

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(arguments)
sys.path.insert(0, str(Path(options.module_dir).resolve()))
import _native as n


def identity(number):
    return f"00000000-0000-4000-8000-{number:012x}"


def populated_problem(offset=0):
    problem = n.SketchProblem()
    problem.plane.origin = n.Vector3(12.5, -7.0, 3.25)
    problem.plane.x_axis = n.Vector3(0.0, 1.0, 0.0)
    problem.plane.y_axis = n.Vector3(0.0, 0.0, 1.0)
    line = n.SketchEntity(n.Line2(n.Point2(-2.0, 0.5), n.Point2(8.0, 0.5)), identity(3 + offset))
    circle = n.SketchEntity(n.Circle2(n.Point2(2.0, 4.0), 1.75), identity(4 + offset))
    problem.entities = [line, circle]
    problem.constraints = [
        n.SketchConstraint(n.SketchConstraintKind.HORIZONTAL,
                           [n.SketchReference(line.id, n.SketchElement.GEOMETRY)], id=identity(5 + offset)),
        n.SketchConstraint(n.SketchConstraintKind.RADIUS,
                           [n.SketchReference(circle.id, n.SketchElement.GEOMETRY)],
                           value=1.75, id=identity(6 + offset)),
        n.SketchConstraint(n.SketchConstraintKind.DISTANCE_X,
                           [n.SketchReference(),
                            n.SketchReference(circle.id, n.SketchElement.CIRCLE_CENTER)],
                           value=2.0, id=identity(7 + offset)),
    ]
    return problem


def complete_document():
    document = n.Document()
    document.set_geometric_tolerance_mm(0.002)
    document.add_parameter("plate_width", 32.5, "mm")
    document.add_parameter("tilt", 15.0, "deg")
    document.add_parameter("factor", 1.25, "unitless")
    sketch_id = document.add_sketch("Profile", populated_problem())
    document.rename_sketch(sketch_id, "Profile sketch")
    return document


def feature_document():
    document = complete_document()
    profile_id = document.sketches[0].id
    pocket_sketch_id = document.add_sketch("Pocket profile", populated_problem(200))
    extrusion_id = document.add_extrusion("Base extrusion", profile_id, "plate_width")
    pocket_id = document.add_subtractive_extrusion(
        "Pocket", extrusion_id, pocket_sketch_id, 4.25
    )
    document.set_feature_suppressed(pocket_id, True)
    document.set_rollback_before(pocket_id)
    return document, profile_id, pocket_sketch_id, extrusion_id, pocket_id


class DocumentSnapshotChecks(unittest.TestCase):
    def test_every_constraint_and_target_kind_has_a_stable_snapshot(self):
        problem = populated_problem()
        first, circle = problem.entities
        second = n.SketchEntity(n.Line2(n.Point2(8, 0.5), n.Point2(8, 10.5)), identity(10))
        problem.entities = [first, circle, second]
        def ref(entity, element=n.SketchElement.GEOMETRY):
            return n.SketchReference(entity.id, element)
        a, b, c = ref(first), ref(second), ref(circle)
        start = ref(first, n.SketchElement.LINE_START)
        end = ref(first, n.SketchElement.LINE_END)
        other_start = ref(second, n.SketchElement.LINE_START)
        other_end = ref(second, n.SketchElement.LINE_END)
        center = ref(circle, n.SketchElement.CIRCLE_CENTER)
        kinds = n.SketchConstraintKind
        cases = [(kinds.COINCIDENT, [end, other_start], 0),
                 (kinds.HORIZONTAL, [a], 0), (kinds.VERTICAL, [b], 0),
                 (kinds.PARALLEL, [a, b], 0), (kinds.PERPENDICULAR, [a, b], 0),
                 (kinds.DISTANCE, [start, center], 5), (kinds.LENGTH, [a], 10),
                 (kinds.RADIUS, [c], 1.75), (kinds.DIAMETER, [c], 3.5),
                 (kinds.DISTANCE_X, [n.SketchReference(), start], -2),
                 (kinds.DISTANCE_Y, [n.SketchReference(), other_end], 10.5)]
        problem.constraints = [n.SketchConstraint(kind, targets, value, identity(20+i))
                               for i, (kind, targets, value) in enumerate(cases)]
        document = n.Document()
        document.add_sketch("Constraint definitions", problem)
        snapshot = document.to_snapshot()
        self.assertEqual([item["type"] for item in snapshot["sketches"][0]["constraints"]],
                         ["coincident", "horizontal", "vertical", "parallel", "perpendicular",
                          "distance", "length", "radius", "diameter", "distance_x", "distance_y"])
        self.assertEqual(n.Document.from_snapshot(json.loads(json.dumps(snapshot))).to_snapshot(), snapshot)

    def test_json_round_trip_preserves_ids_plane_entities_constraints_and_revision(self):
        document = complete_document()
        original = document.to_snapshot()
        encoded = json.dumps(original, sort_keys=True, separators=(",", ":"))
        restored = n.Document.from_snapshot(json.loads(encoded))
        self.assertEqual(restored.to_snapshot(), original)
        self.assertEqual(restored.revision, document.revision)
        self.assertEqual(restored.id, document.id)
        self.assertEqual([item.id for item in restored.sketches], [item["id"] for item in original["sketches"]])

    def test_schema_one_migrates_in_memory_and_emits_schema_three(self):
        document = n.Document()
        document.add_parameter("height", 4.0, "mm")
        old_snapshot = document.to_snapshot()
        old_snapshot["schema_version"] = 1
        del old_snapshot["sketches"]
        del old_snapshot["features"]
        del old_snapshot["rollback_before"]
        restored = n.Document.from_snapshot(old_snapshot)
        self.assertEqual(restored.schema_version, 3)
        self.assertEqual(restored.to_snapshot()["schema_version"], 3)
        self.assertEqual(restored.sketches, [])
        self.assertEqual(restored.revision, old_snapshot["revision"])

    def test_schema_two_migrates_in_memory_and_emits_schema_three(self):
        snapshot = complete_document().to_snapshot()
        snapshot["schema_version"] = 2
        del snapshot["features"]
        del snapshot["rollback_before"]
        restored = n.Document.from_snapshot(snapshot)
        self.assertEqual(restored.schema_version, 3)
        self.assertEqual(restored.to_snapshot()["schema_version"], 3)
        self.assertEqual(restored.to_snapshot()["sketches"], snapshot["sketches"])
        self.assertEqual(restored.to_snapshot()["features"], [])
        self.assertIsNone(restored.rollback_before)
        self.assertEqual(restored.revision, snapshot["revision"])

    def test_feature_snapshot_round_trip_and_metadata(self):
        document, profile_id, pocket_sketch_id, extrusion_id, pocket_id = feature_document()
        snapshot = document.to_snapshot()
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        restored = n.Document.from_snapshot(json.loads(encoded))
        self.assertEqual(restored.to_snapshot(), snapshot)
        self.assertEqual(restored.rollback_before, pocket_id)
        self.assertEqual(len(restored.features), 2)

        extrusion, pocket = restored.features
        self.assertEqual((extrusion.id, extrusion.name, extrusion.version),
                         (extrusion_id, "Base extrusion", 1))
        self.assertEqual(extrusion.type, "extrusion")
        self.assertEqual(extrusion.sketch_id, profile_id)
        self.assertIsNone(extrusion.input_id)
        self.assertEqual(extrusion.depth, "plate_width")
        self.assertEqual(document.length_value(extrusion.depth), 32.5)
        self.assertEqual(extrusion.body_id, pocket.body_id)

        self.assertEqual((pocket.id, pocket.type, pocket.input_id, pocket.sketch_id),
                         (pocket_id, "subtractive_extrusion", extrusion_id, pocket_sketch_id))
        self.assertEqual(pocket.depth, 4.25)
        self.assertTrue(pocket.suppressed)
        self.assertIn(extrusion_id, pocket.dependencies)
        self.assertIn(pocket_sketch_id, pocket.dependencies)
        self.assertEqual(restored.get_feature(pocket_id).id, pocket_id)

    def test_feature_depth_input_forms_and_record_shapes(self):
        snapshot = feature_document()[0].to_snapshot()
        extrusion, pocket = snapshot["features"]
        self.assertEqual(set(extrusion), {"id", "name", "version", "type", "body",
                                          "sketch", "input", "depth", "suppressed"})
        self.assertEqual(extrusion["type"], "extrusion")
        self.assertIsNone(extrusion["input"])
        self.assertEqual(extrusion["depth"], {"parameter": "plate_width"})
        self.assertEqual(pocket["type"], "subtractive_extrusion")
        self.assertEqual(pocket["input"], extrusion["id"])
        self.assertEqual(pocket["depth"], {"value_mm": 4.25})

    def test_invalid_feature_records_and_references_are_rejected(self):
        snapshot = feature_document()[0].to_snapshot()
        mutations = [
            lambda s: s["features"][0].update(unknown="field"),
            lambda s: s["features"][0].update(input=identity(900)),
            lambda s: s["features"][1].update(input=None),
            lambda s: s["features"][0].update(type="fillet"),
            lambda s: s["features"][0].update(body=identity(900)),
            lambda s: s["features"][0].update(sketch=identity(900)),
            lambda s: s["features"][0].update(depth={"value_mm": True}),
            lambda s: s["features"][0].update(depth={"parameter": "missing"}),
            lambda s: s["features"][0].update(depth={"value_mm": 1, "parameter": "plate_width"}),
            lambda s: s["features"][0].update(suppressed=1),
            lambda s: s["features"][0].update(version=0),
            lambda s: s.update(rollback_before=identity(900)),
        ]
        for index, mutate in enumerate(mutations):
            malformed = copy.deepcopy(snapshot)
            mutate(malformed)
            with self.subTest(index=index), self.assertRaises(ValueError):
                n.Document.from_snapshot(malformed)

        duplicate = copy.deepcopy(snapshot)
        duplicate["features"][0]["id"] = duplicate["document_id"]
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(duplicate)

    def test_feature_edits_and_dependency_rejections_are_atomic(self):
        document, _, _, extrusion_id, pocket_id = feature_document()
        before = document.to_snapshot()
        failing_edits = [
            lambda: document.reorder_features([pocket_id, extrusion_id]),
            lambda: document.erase_feature(extrusion_id),
            lambda: document.erase_feature(pocket_id),
            lambda: document.erase_sketch(document.features[0].sketch_id),
            lambda: document.set_feature_depth(extrusion_id, "not_a_parameter"),
            lambda: document.set_rollback_before(identity(901)),
        ]
        for edit in failing_edits:
            with self.assertRaises(ValueError):
                edit()
            self.assertEqual(document.to_snapshot(), before)

        document.set_feature_depth(extrusion_id, "plate_width")
        document.set_feature_suppressed(pocket_id, True)
        document.set_rollback_before(pocket_id)
        document.reorder_features([extrusion_id, pocket_id])
        self.assertEqual(document.to_snapshot(), before)
        document.erase_feature(pocket_id, cascade=True)
        self.assertIsNone(document.rollback_before)
        self.assertEqual([item.id for item in document.features], [extrusion_id])
        self.assertEqual(len(document.sketches), 2)
        self.assertEqual(document.revision, before["revision"] + 1)

    def test_feature_revision_exhaustion_is_atomic(self):
        original, profile, pocket_sketch, extrusion, pocket = feature_document()
        snapshot = original.to_snapshot()
        snapshot["revision"] = (1 << 64) - 1
        document = n.Document.from_snapshot(snapshot)
        edits = [lambda: document.add_extrusion("New", profile, 2),
                 lambda: document.set_feature_depth(extrusion, 2),
                 lambda: document.set_feature_suppressed(pocket, False),
                 lambda: document.set_rollback_before(None),
                 lambda: document.erase_feature(pocket, cascade=True),
                 lambda: document.erase_sketch(pocket_sketch, cascade=True)]
        for edit in edits:
            with self.assertRaises(OverflowError):
                edit()
            self.assertEqual(document.to_snapshot(), snapshot)

    def test_future_document_and_sketch_versions_are_rejected(self):
        snapshot = complete_document().to_snapshot()
        future_document = copy.deepcopy(snapshot)
        future_document["schema_version"] = 4
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(future_document)
        future_sketch = copy.deepcopy(snapshot)
        future_sketch["sketches"][0]["version"] = 2
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(future_sketch)

        future_feature = feature_document()[0].to_snapshot()
        future_feature["features"][0]["version"] = 2
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(future_feature)

    def test_well_formed_conflicting_definitions_round_trip_without_solving(self):
        problem = populated_problem()
        line_id = problem.entities[0].id
        problem.constraints += [
            n.SketchConstraint(n.SketchConstraintKind.LENGTH,
                               [n.SketchReference(line_id, n.SketchElement.GEOMETRY)],
                               value=5.0, id=identity(8)),
            n.SketchConstraint(n.SketchConstraintKind.LENGTH,
                               [n.SketchReference(line_id, n.SketchElement.GEOMETRY)],
                               value=9.0, id=identity(9)),
        ]
        document = n.Document()
        document.add_sketch("Conflict to repair", problem)
        snapshot = document.to_snapshot()
        self.assertEqual(n.Document.from_snapshot(snapshot).to_snapshot(), snapshot)

    def test_malformed_and_dangling_references_are_rejected(self):
        snapshot = complete_document().to_snapshot()
        dangling = copy.deepcopy(snapshot)
        dangling["sketches"][0]["constraints"][0]["targets"][0]["entity"] = identity(99)
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(dangling)

        unknown_field = copy.deepcopy(snapshot)
        unknown_field["sketches"][0]["constraints"][0]["solver_handle"] = 42
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(unknown_field)

        malformed_target = copy.deepcopy(snapshot)
        malformed_target["sketches"][0]["constraints"][0]["targets"][0]["element"] = "face_0"
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(malformed_target)

    def test_duplicate_global_identities_are_rejected(self):
        snapshot = complete_document().to_snapshot()
        duplicate = copy.deepcopy(snapshot)
        duplicate["sketches"][0]["entities"][0]["id"] = duplicate["document_id"]
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(duplicate)

        duplicate = copy.deepcopy(snapshot)
        duplicate["sketches"][0]["constraints"][0]["id"] = duplicate["sketches"][0]["id"]
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(duplicate)

    def test_invalid_numbers_and_revisions_are_rejected(self):
        snapshot = complete_document().to_snapshot()
        for bad_value in (True, float("inf"), float("nan"), "12", 1 << 4096):
            malformed = copy.deepcopy(snapshot)
            malformed["geometric_tolerance_mm"] = bad_value
            with self.subTest(value=bad_value), self.assertRaises(ValueError):
                n.Document.from_snapshot(malformed)

        for bad_revision in (-1, True, 1.0, 1 << 80):
            malformed = copy.deepcopy(snapshot)
            malformed["revision"] = bad_revision
            with self.subTest(revision=bad_revision), self.assertRaises(ValueError):
                n.Document.from_snapshot(malformed)

        negative_radius = copy.deepcopy(snapshot)
        negative_radius["sketches"][0]["entities"][1]["radius"] = -1.0
        with self.assertRaises(ValueError):
            n.Document.from_snapshot(negative_radius)

    def test_failed_edit_is_atomic_and_snapshot_views_are_copies(self):
        document = complete_document()
        before = document.to_snapshot()
        revision = document.revision
        invalid_problem = populated_problem()
        invalid_problem.constraints = [n.SketchConstraint(
            n.SketchConstraintKind.HORIZONTAL,
            [n.SketchReference(identity(100), n.SketchElement.GEOMETRY)],
            id=identity(101),
        )]
        with self.assertRaises(ValueError):
            document.replace_sketch(document.sketches[0].id, invalid_problem)
        self.assertEqual(document.revision, revision)
        self.assertEqual(document.to_snapshot(), before)

        sketches = document.sketches
        detached = sketches[0].problem
        detached.plane.origin.x = 900.0
        detached.entities = []
        self.assertEqual(document.to_snapshot(), before)
        fetched = document.get_sketch(sketches[0].id)
        fetched.plane.origin.x = -800.0
        fetched.constraints = []
        self.assertEqual(document.to_snapshot(), before)

    def test_no_op_and_valid_edit_revisions(self):
        document = complete_document()
        sketch_id = document.sketches[0].id
        before = document.to_snapshot()
        document.set_parameter_value("plate_width", 32.5)
        document.set_geometric_tolerance_mm(0.002)
        document.rename_sketch(sketch_id, "Profile sketch")
        document.replace_sketch(sketch_id, document.get_sketch(sketch_id))
        self.assertEqual(document.to_snapshot(), before)
        with self.assertRaises(ValueError):
            document.add_sketch("Duplicate child IDs", document.get_sketch(sketch_id))
        with self.assertRaises(ValueError):
            document.rename_sketch(sketch_id, "hidden\x00suffix")
        self.assertEqual(document.to_snapshot(), before)
        candidate = document.get_sketch(sketch_id)
        candidate.plane.origin.x += 1
        document.replace_sketch(sketch_id, candidate)
        self.assertEqual(document.revision, before["revision"] + 1)
        self.assertEqual(document.sketches[0].id, sketch_id)
        self.assertEqual(document.sketches[0].problem.entities[0].id, before["sketches"][0]["entities"][0]["id"])
        document.rename_sketch(sketch_id, "Edited profile")
        self.assertEqual(document.revision, before["revision"] + 2)

    def test_revision_exhaustion_is_atomic(self):
        snapshot = complete_document().to_snapshot()
        snapshot["revision"] = (1 << 64) - 1
        document = n.Document.from_snapshot(snapshot)
        sketch_id = document.sketches[0].id
        candidate = document.get_sketch(sketch_id)
        candidate.plane.origin.z += 1
        edits = [lambda: document.add_parameter("new", 1),
                 lambda: document.set_parameter_value("plate_width", 30),
                 lambda: document.set_geometric_tolerance_mm(0.003),
                 lambda: document.add_sketch("New", n.SketchProblem()),
                 lambda: document.replace_sketch(sketch_id, candidate),
                 lambda: document.rename_sketch(sketch_id, "Changed")]
        for edit in edits:
            with self.assertRaises(OverflowError):
                edit()
            self.assertEqual(document.to_snapshot(), snapshot)
        document.set_parameter_value("plate_width", 32.5)
        document.replace_sketch(sketch_id, document.get_sketch(sketch_id))
        self.assertEqual(document.to_snapshot(), snapshot)

    def test_invalid_schema_shapes_and_geometry_are_rejected(self):
        snapshot = complete_document().to_snapshot()
        mutations = [lambda s: s.update(schema_version=True),
                     lambda s: s.update(schema_version=2.0),
                     lambda s: s.update(parameters=()),
                     lambda s: s.update(unknown_definition={}),
                     lambda s: s["sketches"][0]["plane"].update(origin=[0, 0]),
                     lambda s: s["sketches"][0]["plane"].update(x_axis=[0, 0, 1]),
                     lambda s: s["sketches"][0]["entities"][0].update(type="nurbs"),
                     lambda s: s["sketches"][0]["entities"][0].update(end=[-2, 0.5]),
                     lambda s: s["sketches"][0]["constraints"][0].update(value=1),
                     lambda s: s["sketches"][0]["constraints"][1].update(value=True),
                     lambda s: s["sketches"][0].update(version=-1)]
        for index, mutate in enumerate(mutations):
            corrupted = copy.deepcopy(snapshot)
            mutate(corrupted)
            with self.subTest(index=index), self.assertRaises(ValueError):
                n.Document.from_snapshot(corrupted)
        for key in snapshot:
            corrupted = copy.deepcopy(snapshot)
            del corrupted[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                n.Document.from_snapshot(corrupted)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
