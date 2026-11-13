"""Measurements, closest points and rays query trimmed B-reps, not display triangles."""

import argparse
import math
from pathlib import Path
import sys
import unittest

import numpy as np

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(arguments)
sys.path.insert(0, str(Path(options.module_dir).resolve()))
import _native as n

K, R = n.TopologyKind, n.TopologySeedRole


def vector(value):
    return np.array([value.x, value.y, value.z])


def rectangle(width=20, height=30):
    return [n.Point2(0, 0), n.Point2(width, 0), n.Point2(width, height), n.Point2(0, height)]


class QueryChecks(unittest.TestCase):
    def setUp(self):
        self.kernel = n.new_geometry_adapter()
        self.plane = n.SketchPlane()

    def seed(self, shape, role, profile=0):
        return next(seed.target for seed in n.geometry_topology(self.kernel, shape).seeds
                    if seed.role == role and seed.profile_index == profile)

    def test_analytic_area_length_radius_centers_and_vertices(self):
        cylinder = self.kernel.extrude_circle(n.Point2(3, 4), 2, self.plane, 5)
        side = n.geometry_measure(self.kernel, cylinder, self.seed(cylinder, R.SIDE))
        self.assertAlmostEqual(side.area_mm2, 2 * math.pi * 2 * 5, places=10)
        self.assertAlmostEqual(side.radius_mm, 2)
        self.assertIsNone(side.length_mm)
        np.testing.assert_allclose(vector(side.center_mm), [3, 4, 2.5], atol=1e-10)
        cap = n.geometry_measure(self.kernel, cylinder, self.seed(cylinder, R.END_CAP))
        self.assertAlmostEqual(cap.area_mm2, math.pi * 4, places=10)
        self.assertIsNone(cap.radius_mm)
        np.testing.assert_allclose(vector(cap.center_mm), [3, 4, 5], atol=1e-10)
        ring = n.geometry_measure(self.kernel, cylinder, self.seed(cylinder, R.START_EDGE))
        self.assertAlmostEqual(ring.length_mm, 4 * math.pi, places=10)
        self.assertAlmostEqual(ring.radius_mm, 2)
        self.assertIsNone(ring.area_mm2)
        np.testing.assert_allclose(vector(ring.center_mm), [3, 4, 0], atol=1e-10)
        vertex_element = self.seed(cylinder, R.END_VERTEX)
        vertex = n.geometry_measure(self.kernel, cylinder, vertex_element)
        self.assertEqual(vertex.element.kind, K.VERTEX)
        self.assertIsNone(vertex.area_mm2)
        self.assertIsNone(vertex.length_mm)
        self.assertIsNone(vertex.radius_mm)
        np.testing.assert_allclose(vector(vertex.center_mm), [5, 4, 5], atol=1e-10)
        closest = n.geometry_closest_point(self.kernel, cylinder, vertex_element, n.Vector3(5, 4, 9))
        np.testing.assert_allclose(vector(closest.point_mm), [5, 4, 5], atol=1e-10)
        self.assertAlmostEqual(closest.distance_mm, 4)

    def test_trimmed_hole_miss_closest_boundary_and_inward_hole_normal(self):
        body = self.kernel.extrude_polygon(rectangle(), self.plane, 5)
        cap = self.seed(body, R.END_CAP)
        tool = self.kernel.extrude_circle(n.Point2(10, 15), 3, self.plane, 5)
        cut = self.kernel.subtract(body, tool)
        history = n.geometry_topology(self.kernel, cut).argument_history
        cap_target, = next(entry.modified for entry in history
                           if entry.source.kind == cap.kind and entry.source.index == cap.index)
        measure = n.geometry_measure(self.kernel, cut, cap_target)
        self.assertAlmostEqual(measure.area_mm2, 600 - math.pi * 9, places=8)
        closest = n.geometry_closest_point(self.kernel, cut, cap_target, n.Vector3(10, 15, 8))
        point = vector(closest.point_mm)
        self.assertAlmostEqual(point[2], 5, places=8)
        self.assertAlmostEqual(np.linalg.norm(point[:2] - [10, 15]), 3, places=8)
        self.assertAlmostEqual(closest.distance_mm, math.sqrt(18), places=8)
        self.assertIsNone(n.geometry_ray_cast(self.kernel, cut, n.Vector3(10, 15, 8), n.Vector3(0, 0, -1), 20))
        hit = n.geometry_ray_cast(self.kernel, cut, n.Vector3(10, 15, 2.5), n.Vector3(4, 0, 0), 20)
        np.testing.assert_allclose(vector(hit.point_mm), [13, 15, 2.5], atol=1e-8)
        np.testing.assert_allclose(vector(hit.normal), [-1, 0, 0], atol=1e-8)
        self.assertAlmostEqual(hit.distance_mm, 3, places=8)
        self.assertAlmostEqual(n.geometry_measure(self.kernel, cut, n.TopologyElement(K.FACE, hit.face_index)).radius_mm, 3)

    def test_closest_edge_respects_endpoints_not_supporting_infinite_line(self):
        shape = self.kernel.extrude_polygon(rectangle(), self.plane, 5)
        edge = self.seed(shape, R.END_EDGE)
        closest = n.geometry_closest_point(self.kernel, shape, edge, n.Vector3(25, -2, 7))
        np.testing.assert_allclose(vector(closest.point_mm), [20, 0, 5], atol=1e-10)
        self.assertAlmostEqual(closest.distance_mm, math.sqrt(33), places=10)
        measured = n.geometry_measure(self.kernel, shape, edge)
        self.assertAlmostEqual(measured.length_mm, 20)
        self.assertIsNone(measured.radius_mm)
        np.testing.assert_allclose(vector(measured.center_mm), [10, 0, 5], atol=1e-10)

    def test_exact_cylinder_silhouette_beyond_coarse_display_and_nonmutation(self):
        radius = 10
        shape = self.kernel.extrude_circle(n.Point2(0, 0), radius, self.plane, 5)
        before = self.kernel.tessellate(shape, 2, 1)
        positions = before["positions"].reshape((-1, 3))
        angles = np.unique(np.round(np.mod(np.arctan2(positions[:, 1], positions[:, 0]), 2 * math.pi), 12))
        gaps = np.diff(np.r_[angles, angles[0] + 2 * math.pi])
        index = int(np.argmax(gaps))
        angle = angles[index] + gaps[index] / 2
        radial = np.array([math.cos(angle), math.sin(angle), 0])
        tangent = np.array([-math.sin(angle), math.cos(angle), 0])
        mesh_extent = (positions @ radial).max()
        ray_offset = (mesh_extent + radius) / 2
        self.assertGreater(radius - mesh_extent, 0.01)
        # Every triangle lies on the opposite side of this separating plane:
        # this grazing ray cannot hit the coarse display but does hit the circle.
        origin = radial * ray_offset - tangent * 20 + np.array([0, 0, 2.5])
        hit = n.geometry_ray_cast(self.kernel, shape, n.Vector3(*origin), n.Vector3(*(tangent * 7)), 50)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.face_index, self.seed(shape, R.SIDE).index)
        self.assertAlmostEqual(hit.distance_mm, 20 - math.sqrt(radius * radius - ray_offset * ray_offset), places=8)
        point = vector(hit.point_mm)
        self.assertAlmostEqual(np.linalg.norm(point[:2]), radius, places=8)
        np.testing.assert_allclose(vector(hit.normal), [point[0] / radius, point[1] / radius, 0], atol=1e-8)
        self.assertGreater(point @ radial, mesh_extent)
        after = self.kernel.tessellate(shape, 2, 1)
        np.testing.assert_array_equal(after["positions"], before["positions"])
        np.testing.assert_array_equal(after["triangles"], before["triangles"])

    def test_arbitrary_plane_reversed_winding_outward_normals_and_distance_limit(self):
        self.plane.origin = n.Vector3(10, 20, 30)
        self.plane.x_axis = n.Vector3(0, 1, 0)
        self.plane.y_axis = n.Vector3(0, 0, 1)
        points = rectangle(2, 3)
        for ordered in (points, list(reversed(points))):
            shape = self.kernel.extrude_polygon(ordered, self.plane, 4)
            hit = n.geometry_ray_cast(self.kernel, shape, n.Vector3(20, 21, 31.5), n.Vector3(-7, 0, 0), 10)
            np.testing.assert_allclose(vector(hit.point_mm), [14, 21, 31.5], atol=1e-10)
            np.testing.assert_allclose(vector(hit.normal), [1, 0, 0], atol=1e-10)
            self.assertAlmostEqual(hit.distance_mm, 6)
            self.assertEqual(hit.face_index, self.seed(shape, R.END_CAP).index)
            opposite = n.geometry_ray_cast(self.kernel, shape, n.Vector3(5, 21, 31.5), n.Vector3(1, 0, 0), 10)
            np.testing.assert_allclose(vector(opposite.normal), [-1, 0, 0], atol=1e-10)
            self.assertIsNone(n.geometry_ray_cast(self.kernel, shape, n.Vector3(20, 21, 31.5), n.Vector3(-1, 0, 0), 5))
            inside = n.geometry_ray_cast(self.kernel, shape, n.Vector3(12, 21, 31.5), n.Vector3(1, 0, 0), 5)
            self.assertAlmostEqual(inside.distance_mm, 2)
            np.testing.assert_allclose(vector(inside.normal), [1, 0, 0], atol=1e-10)

    def test_scales_and_invalid_finite_index_direction_and_handle_inputs(self):
        for scale in (0.01, 1, 1000):
            shape = self.kernel.extrude_circle(n.Point2(0, 0), 2 * scale, self.plane, 5 * scale)
            side = self.seed(shape, R.SIDE)
            measure = n.geometry_measure(self.kernel, shape, side)
            self.assertAlmostEqual(measure.area_mm2 / (scale * scale), 20 * math.pi, places=8)
            self.assertAlmostEqual(measure.radius_mm / scale, 2)
            hit = n.geometry_ray_cast(self.kernel, shape, n.Vector3(3 * scale, 0, 2.5 * scale), n.Vector3(-1, 0, 0), 10 * scale)
            self.assertAlmostEqual(hit.distance_mm / scale, 1, places=8)
            point = n.geometry_closest_point(self.kernel, shape, self.seed(shape, R.START_EDGE), n.Vector3(3 * scale, 0, 0))
            self.assertAlmostEqual(point.distance_mm / scale, 1, places=8)
        element = self.seed(shape, R.SIDE)
        # Direction is dimensionless: harmless finite rescaling must not fail
        # because a squared norm overflows or underflows double precision.
        for magnitude in (1e-300, 1e308):
            hit = n.geometry_ray_cast(self.kernel, shape, n.Vector3(3000, 3000, 7500),
                                      n.Vector3(-magnitude, -magnitude, -magnitude), 20000)
            np.testing.assert_allclose(vector(hit.point_mm), [500, 500, 5000], atol=1e-8)
            self.assertAlmostEqual(hit.distance_mm, 2500 * math.sqrt(3), places=8)
        for kind in (K.FACE, K.EDGE, K.VERTEX):
            invalid = n.TopologyElement(kind, 100000)
            with self.assertRaises(ValueError):
                n.geometry_measure(self.kernel, shape, invalid)
            with self.assertRaises(ValueError):
                n.geometry_closest_point(self.kernel, shape, invalid, n.Vector3(0, 0, 0))
        for value in (math.inf, -math.inf, math.nan):
            with self.assertRaises(ValueError):
                n.geometry_closest_point(self.kernel, shape, element, n.Vector3(value, 0, 0))
            with self.assertRaises(ValueError):
                n.geometry_ray_cast(self.kernel, shape, n.Vector3(value, 0, 0), n.Vector3(1, 0, 0), 1)
            with self.assertRaises(ValueError):
                n.geometry_ray_cast(self.kernel, shape, n.Vector3(0, 0, 0), n.Vector3(value, 0, 0), 1)
            with self.assertRaises(ValueError):
                n.geometry_ray_cast(self.kernel, shape, n.Vector3(0, 0, 0), n.Vector3(1, 0, 0), value)
        for maximum in (0, -1):
            with self.assertRaises(ValueError):
                n.geometry_ray_cast(self.kernel, shape, n.Vector3(0, 0, 0), n.Vector3(1, 0, 0), maximum)
        with self.assertRaises(ValueError):
            n.geometry_ray_cast(self.kernel, shape, n.Vector3(0, 0, 0), n.Vector3(0, 0, 0), 1)
        other = n.new_geometry_adapter()
        for kernel, released in ((other, False), (self.kernel, True)):
            if released:
                self.kernel.release(shape)
            with self.assertRaises(ValueError):
                n.geometry_measure(kernel, shape, element)
            with self.assertRaises(ValueError):
                n.geometry_closest_point(kernel, shape, element, n.Vector3(0, 0, 0))
            with self.assertRaises(ValueError):
                n.geometry_ray_cast(kernel, shape, n.Vector3(0, 0, 0), n.Vector3(1, 0, 0), 1)


if __name__ == "__main__":
    outcome = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not outcome.wasSuccessful():
        raise SystemExit(1)
