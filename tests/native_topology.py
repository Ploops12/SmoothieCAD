"""OCCT construction/history metadata agrees with authoritative display domains."""

import argparse
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

K, R = n.TopologyKind, n.TopologySeedRole
SLOTS = {K.FACE: 0, K.EDGE: 1, K.VERTEX: 2}


def identity(element):
    return SLOTS[element.kind], element.index


def rectangle(x0, y0, x1, y1):
    return [n.Point2(x0, y0), n.Point2(x1, y0), n.Point2(x1, y1), n.Point2(x0, y1)]


def signed_display_volume(display):
    vertices = display["positions"].reshape((-1, 3))
    triangles = display["triangles"].reshape((-1, 3))
    first, second, third = (vertices[triangles[:, index]] for index in range(3))
    return np.einsum("ij,ij->i", first, np.cross(second, third)).sum() / 6


class TopologyChecks(unittest.TestCase):
    def setUp(self):
        self.kernel = n.new_geometry_adapter()
        self.plane = n.SketchPlane()

    def check_domains(self, shape):
        topology = n.geometry_topology(self.kernel, shape)
        display = self.kernel.tessellate(shape, 0.01, 0.2)
        self.assertEqual(topology.counts, [display["face_count"], len(display["edge_offsets"]) - 1,
                                           len(display["cad_vertices"]) // 3])
        self.assertEqual(set(display["triangle_faces"]), set(range(topology.counts[0])))
        for seed in topology.seeds:
            self.assertLess(seed.target.index, topology.counts[SLOTS[seed.target.kind]])
        return topology, display

    def points_for(self, display, element):
        if element.kind == K.FACE:
            triangles = display["triangles"].reshape((-1, 3))
            triangles = triangles[display["triangle_faces"] == element.index]
            return display["positions"].reshape((-1, 3))[np.unique(triangles)]
        if element.kind == K.EDGE:
            first, last = display["edge_offsets"][element.index:element.index + 2]
            return display["edge_positions"].reshape((-1, 3))[first:last]
        return display["cad_vertices"].reshape((-1, 3))[element.index:element.index + 1]

    def test_polygon_seed_input_identity_and_copy_display_mapping(self):
        self.plane.origin = n.Vector3(10, 20, 30)
        self.plane.x_axis = n.Vector3(0, 1, 0)
        self.plane.y_axis = n.Vector3(0, 0, 1)
        points = rectangle(1, 2, 4, 7)
        # Reversing input winding must not renumber semantic profile_index.
        for ordered in (points, list(reversed(points))):
            shape = self.kernel.extrude_polygon(ordered, self.plane, 6)
            topology, display = self.check_domains(shape)
            self.assertEqual(topology.counts, [6, 12, 8])
            self.assertEqual(len(topology.seeds), 26)
            self.assertEqual(len({identity(seed.target) for seed in topology.seeds}), 26)
            self.assertEqual(topology.argument_history, [])
            self.assertEqual(topology.tool_history, [])
            self.assertAlmostEqual(signed_display_volume(display), 3 * 5 * 6, places=9)
            for seed in topology.seeds:
                world = self.points_for(display, seed.target)
                local = np.column_stack((world[:, 1] - 20, world[:, 2] - 30, world[:, 0] - 10))
                if seed.role in (R.START_CAP, R.END_CAP):
                    self.assertEqual(seed.target.kind, K.FACE)
                    np.testing.assert_allclose(local[:, 2], 0 if seed.role == R.START_CAP else 6, atol=1e-10)
                elif seed.role in (R.SIDE, R.START_EDGE, R.END_EDGE):
                    self.assertEqual(seed.target.kind, K.FACE if seed.role == R.SIDE else K.EDGE)
                    index = seed.profile_index
                    first = np.array([ordered[index].x, ordered[index].y])
                    last = np.array([ordered[(index + 1) % 4].x, ordered[(index + 1) % 4].y])
                    direction = last - first
                    cross = (local[:, 0] - first[0]) * direction[1] - (local[:, 1] - first[1]) * direction[0]
                    np.testing.assert_allclose(cross, 0, atol=1e-10)
                    if seed.role != R.SIDE:
                        np.testing.assert_allclose(local[:, 2], 0 if seed.role == R.START_EDGE else 6, atol=1e-10)
                else:
                    self.assertEqual(seed.target.kind, K.EDGE if seed.role == R.SWEEP_EDGE else K.VERTEX)
                    expected = np.array([ordered[seed.profile_index].x, ordered[seed.profile_index].y])
                    np.testing.assert_allclose(local[:, :2], np.broadcast_to(expected, local[:, :2].shape), atol=1e-10)
                    if seed.role != R.SWEEP_EDGE:
                        np.testing.assert_allclose(local[:, 2], 0 if seed.role == R.START_VERTEX else 6, atol=1e-10)

    def test_circle_semantics_seam_and_caps(self):
        shape = self.kernel.extrude_circle(n.Point2(3, 4), 2, self.plane, 5)
        topology, display = self.check_domains(shape)
        self.assertEqual(topology.counts, [3, 3, 2])
        self.assertEqual(len(topology.seeds), 8)
        self.assertEqual(len({identity(seed.target) for seed in topology.seeds}), 8)
        self.assertTrue(all(seed.profile_index == 0 for seed in topology.seeds))
        self.assertTrue(math.isclose(signed_display_volume(display), math.pi * 4 * 5, rel_tol=0.01))
        for seed in topology.seeds:
            points = self.points_for(display, seed.target)
            if seed.role in (R.START_CAP, R.START_EDGE, R.START_VERTEX):
                np.testing.assert_allclose(points[:, 2], 0, atol=1e-10)
            elif seed.role in (R.END_CAP, R.END_EDGE, R.END_VERTEX):
                np.testing.assert_allclose(points[:, 2], 5, atol=1e-10)
            else:
                self.assertAlmostEqual(points[:, 2].min(), 0)
                self.assertAlmostEqual(points[:, 2].max(), 5)
            if seed.role in (R.SIDE, R.START_EDGE, R.END_EDGE, R.SWEEP_EDGE, R.START_VERTEX, R.END_VERTEX):
                np.testing.assert_allclose(np.linalg.norm(points[:, :2] - [3, 4], axis=1), 2, atol=1e-10)

    def test_boolean_history_unchanged_split_deleted_generated_and_nonmutation(self):
        body = self.kernel.extrude_polygon(rectangle(0, 0, 20, 30), self.plane, 5)
        body_topology, before = self.check_domains(body)
        # This slot crosses both cap faces, removes middle portions of two
        # opposite side faces/edges, and splits the remaining material in two.
        cutter_plane = n.SketchPlane()
        cutter_plane.origin = n.Vector3(0, 0, -1)
        tool = self.kernel.extrude_polygon(rectangle(8, -1, 12, 31), cutter_plane, 7)
        tool_topology = n.geometry_topology(self.kernel, tool)
        result = self.kernel.subtract(body, tool)
        topology, result_display = self.check_domains(result)
        self.assertAlmostEqual(signed_display_volume(result_display), 16 * 30 * 5, places=8)
        self.assertEqual(topology.seeds, [])
        self.assertEqual(len(topology.argument_history), sum(body_topology.counts))
        self.assertEqual(len(topology.tool_history), sum(tool_topology.counts))
        self.assertTrue(any(len(entry.modified) > 1 for entry in topology.argument_history), "No explicit split relation")
        self.assertTrue(any(entry.deleted and not entry.modified for entry in topology.tool_history), "No explicit deleted relation")
        self.assertTrue(any(entry.generated for entry in topology.argument_history + topology.tool_history), "No generated intersection topology")
        for history, source in ((topology.argument_history, body_topology), (topology.tool_history, tool_topology)):
            self.assertEqual(len({identity(entry.source) for entry in history}), sum(source.counts))
            for entry in history:
                self.assertLess(entry.source.index, source.counts[SLOTS[entry.source.kind]])
                for target in entry.modified:
                    self.assertEqual(entry.source.kind, target.kind)
                for target in entry.modified + entry.generated:
                    self.assertLess(target.index, topology.counts[SLOTS[target.kind]])
        after = self.kernel.tessellate(body, 0.01, 0.2)
        np.testing.assert_array_equal(after["positions"], before["positions"])
        np.testing.assert_array_equal(after["triangles"], before["triangles"])
        close_volume = self.kernel.volume_mm3(result)
        self.assertAlmostEqual(close_volume, 16 * 30 * 5, places=8)
        # No-op subtraction retains exact same topology elements through IsSame
        # rather than inventing continuation relationships from coordinates.
        outside = self.kernel.extrude_circle(n.Point2(100, 100), 1, self.plane, 5)
        unchanged = self.kernel.subtract(body, outside)
        unchanged_topology = n.geometry_topology(self.kernel, unchanged)
        self.assertTrue(all(len(entry.modified) == 1 and not entry.deleted for entry in unchanged_topology.argument_history))

    def test_opaque_seedless_shapes_step_and_scoped_handle_queries(self):
        box = self.kernel.make_box(2, 3, 4)
        topology, _ = self.check_domains(box)
        self.assertEqual(topology.counts, [6, 12, 8])
        self.assertEqual(topology.seeds, [])
        other = n.new_geometry_adapter()
        with self.assertRaises(ValueError):
            n.geometry_topology(other, box)
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "topology.step")
            self.kernel.write_step(box, filename)
            restored = self.kernel.read_step(filename)
            restored_topology, _ = self.check_domains(restored)
            self.assertEqual(restored_topology.counts, topology.counts)
            self.assertEqual(restored_topology.seeds, [])
        self.kernel.release(box)
        with self.assertRaises(ValueError):
            n.geometry_topology(self.kernel, box)


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not outcome.wasSuccessful():
        raise SystemExit(1)
