"""Constant-radius fillets, named edge inputs and guarded feature history."""

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

K, E, T, S = n.SketchConstraintKind, n.SketchElement, n.TopologyKind, n.EvaluationState
CORNER_LOSS = 1 - math.pi / 4


def ref(entity, element=E.GEOMETRY):
    return n.SketchReference(entity.id, element)


def rectangle(width=30, height=20, x=0, y=0):
    points = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    lines = [n.SketchEntity(n.Line2(n.Point2(*points[i]), n.Point2(*points[(i + 1) % 4]))) for i in range(4)]
    problem = n.SketchProblem()
    problem.entities = lines
    problem.constraints = [n.SketchConstraint(K.COINCIDENT, [ref(lines[i], E.LINE_END), ref(lines[(i + 1) % 4], E.LINE_START)])
                           for i in range(4)] + [
        n.SketchConstraint(K.HORIZONTAL if i % 2 == 0 else K.VERTICAL, [ref(line)]) for i, line in enumerate(lines)] + [
        n.SketchConstraint(K.LENGTH, [ref(lines[0])], width),
        n.SketchConstraint(K.LENGTH, [ref(lines[1])], height),
        n.SketchConstraint(K.DISTANCE_X, [n.SketchReference(), ref(lines[0], E.LINE_START)], x),
        n.SketchConstraint(K.DISTANCE_Y, [n.SketchReference(), ref(lines[0], E.LINE_START)], y)]
    return problem


def circle(radius=2):
    entity = n.SketchEntity(n.Circle2(n.Point2(10, 10), radius))
    problem = n.SketchProblem()
    problem.entities = [entity]
    problem.constraints = [n.SketchConstraint(K.RADIUS, [ref(entity)], radius),
        n.SketchConstraint(K.DISTANCE_X, [n.SketchReference(), ref(entity, E.CIRCLE_CENTER)], 10),
        n.SketchConstraint(K.DISTANCE_Y, [n.SketchReference(), ref(entity, E.CIRCLE_CENTER)], 10)]
    return problem


def part():
    document = n.Document()
    problem = rectangle()
    sketch = document.add_sketch("Plate", problem)
    base = document.add_extrusion("Base", sketch, 5)
    body = document.get_feature(base).body_id
    targets = [n.PersistentReference(body, T.EDGE, n.ReferenceAnchor(base, "edge:sweep:" + line.id + ":start"))
               for line in problem.entities]
    return document, sketch, base, body, targets


def target_key(target):
    return target.body_id, target.kind, target.anchor.feature_id, target.anchor.key


def identity(number):
    return f"00000000-0000-4000-8000-{number:012x}"


def provenance(prefix, kind, origins, ordered=True):
    signatures = [feature + "/" + str(len(key)) + ":" + key for feature, key in origins]
    if ordered:
        signatures.sort()
    return prefix + ":" + str(kind) + ":" + "".join(str(len(item)) + ":" + item for item in signatures)


def derived(kind, origins, ordered=True):
    return provenance("derived", kind, origins, ordered)


def vertical_edges(kernel, shape, length):
    count = n.geometry_topology(kernel, shape).counts[1]
    return [i for i in range(count) if math.isclose(
        n.geometry_measure(kernel, shape, n.TopologyElement(T.EDGE, i)).length_mm, length,
        rel_tol=1e-9, abs_tol=1e-10)]


class FilletChecks(unittest.TestCase):
    def assert_atomic_rejection(self, document, mutation, exception=ValueError):
        before = copy.deepcopy(document.to_snapshot())
        with self.assertRaises(exception):
            mutation()
        self.assertEqual(document.to_snapshot(), before)

    def test_reference_clone_remaps_structured_keys_without_rewriting_opaque_intent(self):
        first, second, line, anchor, body = (identity(i) for i in (0xaaa, 0xbbb, 0xccc, 0xddd, 0xeee))
        mappings = {first: identity(0x222), second: identity(0x111), line: identity(0x333),
                    anchor: identity(0x444), body: identity(0x555)}
        def reference(key):
            return n.PersistentReference(body, T.EDGE, n.ReferenceAnchor(anchor, key))
        source_key = "edge:sweep:" + line + ":start"
        remapped = n.remap_reference(reference(source_key), mappings)
        self.assertEqual(remapped.body_id, mappings[body])
        self.assertEqual(remapped.anchor.feature_id, mappings[anchor])
        self.assertEqual(remapped.anchor.key, "edge:sweep:" + mappings[line] + ":start")
        nested = derived(1, [(first, "side:" + line), (second, "edge:start:" + line)])
        key = derived(1, [(anchor, nested), (first, "vertex:end:" + line + ":start")])
        expected_nested = derived(1, [(mappings[first], "side:" + mappings[line]),
                                     (mappings[second], "edge:start:" + mappings[line])])
        expected = derived(1, [(mappings[anchor], expected_nested),
                              (mappings[first], "vertex:end:" + mappings[line] + ":start")])
        self.assertEqual(n.remap_reference(reference(key), mappings).anchor.key, expected)
        boundary = provenance("boundary", 1, [(second, key), (first, nested)])
        expected_boundary = provenance("boundary", 1, [(mappings[second], expected),
                                                       (mappings[first], expected_nested)])
        self.assertEqual(n.remap_reference(reference(boundary), mappings).anchor.key, expected_boundary)
        mixed = derived(1, [(anchor, boundary)])
        self.assertEqual(n.remap_reference(reference(mixed), mappings).anchor.key,
                         derived(1, [(mappings[anchor], expected_boundary)]))
        # Mapped UUID order is reversed: preserving the old origin order would
        # produce a key different from the new body's canonical provenance.
        self.assertNotEqual(expected_nested, derived(1, [(mappings[first], "side:" + mappings[line]),
                                                        (mappings[second], "edge:start:" + mappings[line])], ordered=False))
        canonical = derived(1, [(first, "side:" + line), (second, "edge:start:" + line)])
        malformed = ["future:" + line, "side:" + line.upper(), "edge:sweep:" + line + ":unknown",
                     "derived:3:" + canonical[10:], canonical[:-1], "derived:1:0" + canonical[10:],
                     derived(1, [(second, "edge:start:" + line), (first, "side:" + line)], ordered=False),
                     derived(1, [(first, "side:" + line), (first, "side:" + line)]),
                     "boundary:3:" + canonical[10:], "boundary:1:0" + canonical[10:],
                     provenance("boundary", 1, [(second, "side:" + line), (first, "side:" + line)], ordered=False)]
        for opaque in malformed:
            with self.subTest(key=opaque):
                cloned = n.remap_reference(reference(opaque), mappings)
                self.assertEqual(cloned.anchor.key, opaque, "Opaque/noncanonical intent was normalized into another reference")
                self.assertEqual(cloned.body_id, mappings[body])
                self.assertEqual(cloned.anchor.feature_id, mappings[anchor])
        with self.assertRaises(ValueError):
            n.remap_reference(reference(source_key), {first: mappings[first], second: mappings[first]})
        with self.assertRaises(ValueError):
            n.remap_reference(reference(source_key), {first: mappings[first], first.upper(): mappings[second]})
        nested = "cap:start"
        for _ in range(130):
            nested = derived(1, [(first, nested)])
        with self.assertRaises(ValueError):
            n.remap_reference(reference(nested), mappings)

    def test_kernel_fillet_across_scales_planes_and_nonmutation(self):
        kernel = n.new_geometry_adapter()
        for scale in (0.01, 1, 1000):
            with self.subTest(scale=scale):
                plane = n.SketchPlane()
                plane.origin = n.Vector3(10 * scale, 20 * scale, 30 * scale)
                plane.x_axis, plane.y_axis = n.Vector3(0, 1, 0), n.Vector3(0, 0, 1)
                shape = kernel.extrude_polygon([n.Point2(0, 0), n.Point2(2 * scale, 0),
                                                n.Point2(2 * scale, 3 * scale), n.Point2(0, 3 * scale)], plane, 4 * scale)
                edges = vertical_edges(kernel, shape, 4 * scale)
                self.assertEqual(len(edges), 4)
                before = kernel.tessellate(shape, max(0.001, 0.01 * scale), 0.2)
                radius = 0.2 * scale
                for chosen in (edges[:1], edges):
                    rounded = kernel.fillet(shape, chosen, radius)
                    expected = 24 * scale ** 3 - len(chosen) * CORNER_LOSS * radius ** 2 * 4 * scale
                    self.assertTrue(kernel.is_valid(rounded))
                    self.assertLessEqual(kernel.max_tolerance_mm(rounded), 0.001)
                    self.assertTrue(math.isclose(kernel.volume_mm3(rounded), expected, rel_tol=1e-9))
                    topology = n.geometry_topology(kernel, rounded)
                    self.assertEqual(len(topology.argument_history), sum(n.geometry_topology(kernel, shape).counts))
                    self.assertEqual(topology.tool_history, [])
                self.assertTrue(math.isclose(kernel.volume_mm3(shape), 24 * scale ** 3, rel_tol=1e-10))
                after = kernel.tessellate(shape, max(0.001, 0.01 * scale), 0.2)
                for key in ("positions", "triangles", "triangle_faces", "edge_positions", "edge_offsets", "cad_vertices"):
                    np.testing.assert_array_equal(before[key], after[key])

    def test_kernel_failure_bounds_and_step_round_trip(self):
        kernel = n.new_geometry_adapter()
        shape = kernel.make_box(30, 20, 5)
        edges = vertical_edges(kernel, shape, 5)
        before = kernel.tessellate(shape, 0.01, 0.2)
        for indices, radius in (([], 1), ([edges[0], edges[0]], 1), ([100000], 1),
                                ([-1], 1), ([True], 1), ([1.5], 1), ([edges[0]], True),
                                ([edges[0]], 0), ([edges[0]], -1),
                                ([edges[0]], math.nan), ([edges[0]], math.inf), ([edges[0]], 100)):
            with self.subTest(indices=indices, radius=radius), self.assertRaises((ValueError, TypeError, RuntimeError, OverflowError)):
                kernel.fillet(shape, indices, radius)
            self.assertAlmostEqual(kernel.volume_mm3(shape), 3000, places=8)
            self.assertTrue(kernel.is_valid(shape))
        after = kernel.tessellate(shape, 0.01, 0.2)
        np.testing.assert_array_equal(before["positions"], after["positions"])
        np.testing.assert_array_equal(before["triangles"], after["triangles"])
        other = n.new_geometry_adapter()
        other.make_box(1, 1, 1)
        with self.assertRaises(ValueError):
            other.fillet(shape, edges[:1], 1)
        rounded = kernel.fillet(shape, edges[:1], 1.5)
        expected = 3000 - CORNER_LOSS * 1.5 ** 2 * 5
        topology = n.geometry_topology(kernel, rounded)
        circular = [i for i in range(topology.counts[1]) if (
            (radius := n.geometry_measure(kernel, rounded, n.TopologyElement(T.EDGE, i)).radius_mm) is not None
            and math.isclose(radius, 1.5, rel_tol=1e-9))]
        self.assertEqual(len(circular), 2)
        for index in circular:
            direct = [entry for entry in topology.argument_history if any(
                target.kind == T.EDGE and target.index == index for target in [*entry.modified, *entry.generated])]
            boundary = [entry for entry in topology.argument_history if any(
                target.kind == T.EDGE and target.index == index for target in entry.boundary_generated)]
            self.assertFalse(direct, "Exact boundary inference was misreported as OCCT Generated/Modified history")
            self.assertTrue(boundary, "A new cap arc lacks explicit exact-incidence boundary evidence")
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "filleted.step")
            kernel.write_step(rounded, path)
            reopened = kernel.read_step(path)
            self.assertTrue(kernel.is_valid(reopened))
            self.assertTrue(math.isclose(kernel.volume_mm3(reopened), expected, rel_tol=1e-9))
            radii = [n.geometry_measure(kernel, reopened, n.TopologyElement(T.FACE, i)).radius_mm
                     for i in range(n.geometry_topology(kernel, reopened).counts[0])]
            self.assertTrue(any(radius is not None and math.isclose(radius, 1.5, rel_tol=1e-9) for radius in radii))

    def test_feature_interface_schema_validation_noops_and_overflow(self):
        document, sketch, base, body, edges = part()
        fillet = document.add_fillet("Corner round", base, edges[:1], 1)
        feature = document.get_feature(fillet)
        self.assertEqual((feature.type, feature.input_id, feature.body_id), ("fillet", base, body))
        self.assertIsNone(feature.sketch_id)
        self.assertIsNone(feature.depth)
        self.assertEqual(feature.radius, 1)
        self.assertEqual(set(feature.dependencies), {base})
        self.assertNotIn(sketch, feature.dependencies)
        self.assertEqual([target_key(edge) for edge in feature.edges], [target_key(edges[0])])
        snapshot = document.to_snapshot()
        self.assertEqual(snapshot["schema_version"], 8)
        record = snapshot["features"][-1]
        self.assertEqual(set(record), {"id", "name", "version", "type", "body", "input", "radius", "edges", "suppressed"})
        self.assertEqual(record["radius"], {"value_mm": 1})
        self.assertEqual(set(record["edges"][0]), {"body", "kind", "feature", "key"})
        self.assertEqual(record["edges"][0]["kind"], "edge")
        self.assertEqual(n.Document.from_snapshot(json.loads(json.dumps(snapshot))).to_snapshot(), snapshot)
        document.set_fillet_radius(fillet, 1)
        document.set_fillet_edges(fillet, edges[:1])
        self.assertEqual(document.to_snapshot(), snapshot)
        detached_edges = feature.edges
        document.set_fillet_edges(fillet, edges[1:2])
        self.assertEqual(target_key(detached_edges[0]), target_key(edges[0]))
        self.assertEqual(target_key(feature.edges[0]), target_key(edges[0]))
        self.assert_atomic_rejection(document, lambda: document.set_feature_depth(fillet, 2))
        self.assert_atomic_rejection(document, lambda: document.set_fillet_radius(base, 2))
        self.assert_atomic_rejection(document, lambda: document.set_fillet_edges(fillet, []))
        self.assert_atomic_rejection(document, lambda: document.set_fillet_edges(fillet, [edges[0], edges[0]]))
        self_anchor = n.PersistentReference(body, T.EDGE, n.ReferenceAnchor(fillet, edges[0].anchor.key))
        self.assert_atomic_rejection(document, lambda: document.set_fillet_edges(fillet, [self_anchor]))
        for mutate in (
            lambda s, f: f.update(sketch=sketch), lambda s, f: f.update(depth={"value_mm": 1}),
            lambda s, f: f["radius"].update(parameter="missing"), lambda s, f: f.update(edges=[]),
            lambda s, f: f["edges"][0].update(kind="face"), lambda s, f: f["edges"][0].update(index=0),
            lambda s, f: f.update(version=2), lambda s, f: s.update(schema_version=6),
            lambda s, f: s.update(schema_version=9),
        ):
            candidate = copy.deepcopy(snapshot)
            mutate(candidate, candidate["features"][-1])
            with self.assertRaises(ValueError):
                n.Document.from_snapshot(candidate)
        exhausted_snapshot = document.to_snapshot()
        exhausted_snapshot["revision"] = 2 ** 64 - 1
        exhausted = n.Document.from_snapshot(exhausted_snapshot)
        exhausted.set_fillet_radius(fillet, 1)
        self.assert_atomic_rejection(exhausted, lambda: exhausted.set_fillet_radius(fillet, 2), OverflowError)
        self.assert_atomic_rejection(exhausted, lambda: exhausted.set_fillet_edges(fillet, edges[:1]), OverflowError)

    def test_invalid_definitions_tip_ownership_and_aliases_fail_without_substitution(self):
        document, _sketch, base, body, edges = part()
        document.add_parameter("angle", 30, "deg")
        other_sketch = document.add_sketch("Other", rectangle(8, 6))
        other = document.add_extrusion("Other", other_sketch, 2)
        other_body = document.get_feature(other).body_id
        bad = [n.PersistentReference(other_body, T.EDGE, n.ReferenceAnchor(base, edges[0].anchor.key)),
               n.PersistentReference(body, T.FACE, n.ReferenceAnchor(base, "cap:start")),
               n.PersistentReference(body, T.EDGE, n.ReferenceAnchor(other, edges[0].anchor.key)),
               n.PersistentReference(body, T.EDGE, n.ReferenceAnchor(n.new_document_id(), edges[0].anchor.key))]
        for target in bad:
            self.assert_atomic_rejection(document, lambda: document.add_fillet("Bad", base, [target], 1))
        for radius in ("missing", "angle", math.inf, math.nan):
            self.assert_atomic_rejection(document, lambda: document.add_fillet("Bad", base, edges[:1], radius))
        fillet = document.add_fillet("Round", base, edges[:1], 1)
        self.assert_atomic_rejection(document, lambda: document.add_fillet("Wrong history tip", base, edges[1:2], 1))
        # Distinct saved aliases may identify the same evaluated edge. Reject
        # those at evaluation instead of silently applying the radius twice.
        document, sketch, base, _body, edges = part()
        last_line = document.get_sketch(sketch).entities[-1].id
        alias = n.PersistentReference(edges[0].body_id, T.EDGE, n.ReferenceAnchor(base, "edge:sweep:" + last_line + ":end"))
        duplicate_alias = document.add_fillet("Aliased duplicate", base, [edges[0], alias], 1)
        report = n.FeatureEvaluator().evaluate(document)
        self.assertEqual(report.features[-1].feature_id, duplicate_alias)
        self.assertEqual(report.features[-1].state, S.FAILED)
        self.assertTrue(report.features[-1].diagnostic)

    def test_parameter_cache_generated_face_references_and_reconstruction(self):
        document, _sketch, base, body, edges = part()
        document.add_parameter("round_radius", 1, "mm")
        fillet = document.add_fillet("Round", base, edges[:1], "round_radius")
        other_sketch = document.add_sketch("Independent profile", circle())
        other = document.add_extrusion("Independent", other_sketch, 2)
        other_body = document.get_feature(other).body_id
        evaluator = n.FeatureEvaluator()
        self.assertEqual(evaluator.evaluate(document).computed_count, 3)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 3000 - CORNER_LOSS * 5, places=8)
        display = evaluator.tessellate(document, body)
        generated = next(i for i in range(display["face_count"]) if (
            (radius := evaluator.measure_element(document, body, n.TopologyElement(T.FACE, i)).radius_mm) is not None
            and math.isclose(radius, 1, rel_tol=1e-9)))
        target = evaluator.capture_reference(document, body, T.FACE, generated)
        self.assertEqual(target.anchor.feature_id, fillet)
        reference_id = document.add_reference("Cylindrical round", target)
        evaluator.evaluate(document)
        display = evaluator.tessellate(document, body)
        circular = next(i for i in range(len(display["edge_offsets"]) - 1) if (
            (radius := evaluator.measure_element(document, body, n.TopologyElement(T.EDGE, i)).radius_mm) is not None
            and math.isclose(radius, 1, rel_tol=1e-9)))
        edge_target = evaluator.capture_reference(document, body, T.EDGE, circular)
        self.assertEqual(edge_target.anchor.feature_id, fillet)
        self.assertTrue(edge_target.anchor.key.startswith("boundary:1:"), edge_target.anchor.key)
        edge_reference = document.add_reference("Round cap arc", edge_target)
        evaluator.evaluate(document)
        other_generation = evaluator.tessellate(document, other_body)["geometry_generation"]
        document.add_parameter("unused", 1)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (0, 3))
        document.set_parameter_value("round_radius", 2)
        report = evaluator.evaluate(document)
        self.assertEqual((report.computed_count, report.reused_count), (1, 2))
        self.assertEqual(evaluator.tessellate(document, other_body)["geometry_generation"], other_generation)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 3000 - CORNER_LOSS * 4 * 5, places=8)
        self.assertEqual(evaluator.resolve_reference(document, document.get_reference(reference_id).target).state, n.ReferenceState.RESOLVED)
        self.assertAlmostEqual(evaluator.measure_reference(document, document.get_reference(reference_id).target).radius_mm, 2, places=8)
        self.assertEqual(evaluator.resolve_reference(document, document.get_reference(edge_reference).target).state, n.ReferenceState.RESOLVED)
        self.assertAlmostEqual(evaluator.measure_reference(document, document.get_reference(edge_reference).target).radius_mm, 2, places=8)
        reopened = n.Document.from_snapshot(json.loads(json.dumps(document.to_snapshot())))
        rebuilt = n.FeatureEvaluator()
        rebuilt.evaluate(reopened)
        self.assertEqual(rebuilt.resolve_reference(reopened, reopened.get_reference(reference_id).target).state, n.ReferenceState.RESOLVED)
        self.assertAlmostEqual(rebuilt.measure_reference(reopened, reopened.get_reference(reference_id).target).radius_mm, 2, places=8)
        self.assertEqual(rebuilt.resolve_reference(reopened, reopened.get_reference(edge_reference).target).state, n.ReferenceState.RESOLVED)
        self.assertAlmostEqual(rebuilt.measure_reference(reopened, reopened.get_reference(edge_reference).target).radius_mm, 2, places=8)

    def test_failed_and_unresolved_fillets_block_dependents_and_support_explicit_repair(self):
        document, _sketch, base, body, edges = part()
        fillet = document.add_fillet("Round", base, edges[:1], 1)
        hole = document.add_sketch("Hole", circle())
        cut = document.add_subtractive_extrusion("Hole cut", fillet, hole, 5)
        evaluator = n.FeatureEvaluator()
        evaluator.evaluate(document)
        generation = evaluator.tessellate(document, body)["geometry_generation"]
        for radius in (0, -1, 100):
            document.set_fillet_radius(fillet, radius)
            report = evaluator.evaluate(document)
            self.assertEqual([item.state for item in report.features], [S.UP_TO_DATE, S.FAILED, S.BLOCKED])
            display = evaluator.tessellate(document, body)
            self.assertFalse(display["current"])
            self.assertEqual(display["geometry_generation"], generation)
            with self.assertRaises(RuntimeError):
                evaluator.volume_mm3(document, body)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "stale.step"
                path.write_text("unchanged")
                with self.assertRaises(RuntimeError):
                    evaluator.write_step(document, body, str(path))
                self.assertEqual(path.read_text(), "unchanged")
        document.set_fillet_radius(fillet, 1)
        unknown = n.PersistentReference(body, T.EDGE, n.ReferenceAnchor(base, "unknown:repairable:edge"))
        document.set_fillet_edges(fillet, [unknown])
        report = evaluator.evaluate(document)
        self.assertEqual([item.state for item in report.features], [S.UP_TO_DATE, S.FAILED, S.BLOCKED])
        self.assertTrue(report.features[1].diagnostic)
        document.set_fillet_edges(fillet, edges[:1])
        self.assertEqual([item.state for item in evaluator.evaluate(document).features], [S.UP_TO_DATE] * 3)
        document.set_feature_suppressed(fillet, True)
        self.assertEqual([item.state for item in evaluator.evaluate(document).features], [S.UP_TO_DATE, S.SUPPRESSED, S.BLOCKED])
        document.set_feature_suppressed(fillet, False)
        document.set_rollback_before(fillet)
        report = evaluator.evaluate(document)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 3000, places=8)
        document.set_rollback_before(None)
        evaluator.evaluate(document)
        self.assertAlmostEqual(evaluator.volume_mm3(document, body), 3000 - CORNER_LOSS * 5 - 20 * math.pi, places=8)
        self.assert_atomic_rejection(document, lambda: document.reorder_features([fillet, base, cut]))
        self.assert_atomic_rejection(document, lambda: document.erase_feature(fillet))
        document.erase_feature(fillet, cascade=True)
        self.assertEqual([item.id for item in document.features], [base])

    def test_split_deleted_edges_and_additional_anchor_dependencies_are_explicit(self):
        for depth, state in ((1, n.ReferenceState.SPLIT), (5, n.ReferenceState.DELETED)):
            with self.subTest(depth=depth):
                document, _sketch, base, body, edges = part()
                tool = rectangle(2, 2, -1, -1)
                tool.plane.origin = n.Vector3(0, 0, 1 if depth == 1 else 0)
                sketch = document.add_sketch("Corner notch", tool)
                cut = document.add_subtractive_extrusion("Notch", base, sketch, depth)
                evaluator = n.FeatureEvaluator()
                evaluator.evaluate(document)
                self.assertEqual(evaluator.resolve_reference(document, edges[0]).state, state)
                fillet = document.add_fillet("Unresolved corner", cut, edges[:1], 0.2)
                self.assertEqual(set(document.get_feature(fillet).dependencies), {cut, base})
                report = evaluator.evaluate(document)
                self.assertEqual(report.features[-1].state, S.FAILED)
                self.assertTrue(report.features[-1].diagnostic)
                self.assert_atomic_rejection(document, lambda: document.reorder_features([base, fillet, cut]))
                document.erase_feature(cut, cascade=True)
                self.assertEqual([item.id for item in document.features], [base])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
