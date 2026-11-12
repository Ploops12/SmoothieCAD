"""Focused native-boundary checks, usable in CPython or Blender background mode."""

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

import numpy as np

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(arguments)
sys.path.insert(0, str(Path(options.module_dir).resolve()))
import _native as native


class GeometryChecks(unittest.TestCase):
    def setUp(self):
        self.geometry = native.new_geometry_adapter()
        self.plane = native.SketchPlane()

    def rectangle(self, width, height):
        return [native.Point2(0, 0), native.Point2(width, 0),
                native.Point2(width, height), native.Point2(0, height)]

    def test_extrusion_across_scales(self):
        for scale in (0.01, 1.0, 1000.0):
            with self.subTest(scale=scale):
                shape = self.geometry.extrude_polygon(self.rectangle(2 * scale, 3 * scale),
                                                      self.plane, 4 * scale)
                self.assertTrue(self.geometry.is_valid(shape))
                self.assertLessEqual(self.geometry.max_tolerance_mm(shape), 0.001)
                self.assertAlmostEqual(self.geometry.volume_mm3(shape) / (24 * scale**3), 1.0, places=10)
        reversed_shape = self.geometry.extrude_polygon(list(reversed(self.rectangle(2, 3))), self.plane, 4)
        self.assertAlmostEqual(self.geometry.volume_mm3(reversed_shape), 24, places=10)

    def test_arbitrary_plane_and_display_mappings(self):
        self.plane.origin = native.Vector3(10, 20, 30)
        self.plane.x_axis = native.Vector3(0, 1, 0)
        self.plane.y_axis = native.Vector3(0, 0, 1)
        shape = self.geometry.extrude_polygon(self.rectangle(2, 3), self.plane, 4)
        mesh = self.geometry.tessellate(shape, 0.01, 0.2)
        positions = mesh["positions"].reshape(-1, 3)
        triangles = mesh["triangles"].reshape(-1, 3)
        np.testing.assert_allclose(positions.min(axis=0), [10, 20, 30])
        np.testing.assert_allclose(positions.max(axis=0), [14, 22, 33])
        self.assertEqual(positions.dtype, np.float64)
        self.assertEqual(triangles.dtype, np.uint32)
        self.assertEqual(mesh["face_count"], 6)
        self.assertEqual(set(mesh["triangle_faces"]), set(range(6)))
        self.assertEqual(len(mesh["triangle_faces"]), len(triangles))
        self.assertEqual(len(mesh["edge_offsets"]), 13)
        self.assertEqual(mesh["edge_offsets"][-1] * 3, len(mesh["edge_positions"]))
        self.assertEqual(len(mesh["cad_vertices"]), 24)
        a, b, c = (positions[triangles[:, i]] for i in range(3))
        signed_volume = np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6
        self.assertAlmostEqual(signed_volume, 24, places=10)

    def test_subtraction_and_curved_tessellation(self):
        body = self.geometry.make_box(20, 30, 5)
        hole = self.geometry.extrude_circle(native.Point2(10, 15), 3, self.plane, 5)
        result = self.geometry.subtract(body, hole)
        self.assertTrue(self.geometry.is_valid(result))
        self.assertAlmostEqual(self.geometry.volume_mm3(body), 3000, places=8)
        self.assertAlmostEqual(self.geometry.volume_mm3(hole), math.pi * 9 * 5, places=8)
        self.assertAlmostEqual(self.geometry.volume_mm3(result), 3000 - math.pi * 9 * 5, places=8)
        coarse = self.geometry.tessellate(result, 0.1, 0.5)
        fine = self.geometry.tessellate(result, 0.001, 0.1)
        self.assertGreater(len(fine["triangles"]), len(coarse["triangles"]))
        self.assertGreater(len(fine["edge_positions"]), len(coarse["edge_positions"]))

    def test_step_round_trip(self):
        shape = self.geometry.extrude_polygon(self.rectangle(12, 8), self.plane, 3)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "part.step")
            self.geometry.write_step(shape, path)
            restored = self.geometry.read_step(path)
            self.assertTrue(self.geometry.is_valid(restored))
            self.assertAlmostEqual(self.geometry.volume_mm3(restored), 288, places=8)
            self.assertEqual(self.geometry.tessellate(restored, 0.01, 0.2)["face_count"], 6)

    def test_invalid_inputs_and_handle_isolation(self):
        with self.assertRaises(ValueError):
            native.new_geometry_adapter(-1)
        with self.assertRaises(RuntimeError):
            native.new_geometry_adapter(1e-10).make_box(1, 1, 1)
        for value in (0, -1, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.geometry.make_box(value, 1, 1)
        with self.assertRaises(ValueError):
            self.geometry.extrude_polygon(self.rectangle(1, 1)[:2], self.plane, 1)
        with self.assertRaises((ValueError, RuntimeError)):
            self.geometry.extrude_polygon([native.Point2(0, 0), native.Point2(1, 1),
                                           native.Point2(0, 1), native.Point2(1, 0)], self.plane, 1)
        malformed = native.SketchPlane()
        malformed.y_axis = native.Vector3(1, 0, 0)
        with self.assertRaises(ValueError):
            self.geometry.extrude_polygon(self.rectangle(1, 1), malformed, 1)
        shape = self.geometry.make_box(1, 1, 1)
        other = native.new_geometry_adapter()
        other.make_box(2, 2, 2)
        with self.assertRaises(ValueError):
            other.volume_mm3(shape)
        with self.assertRaises(RuntimeError):
            self.geometry.subtract(shape, shape)
        with self.assertRaises(ValueError):
            self.geometry.tessellate(shape, 0, 0.2)
        self.geometry.release(shape)
        with self.assertRaises(ValueError):
            self.geometry.volume_mm3(shape)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                self.geometry.read_step(str(Path(directory) / "missing.step"))


class DocumentChecks(unittest.TestCase):
    def test_uuid_rejects_extra_separators(self):
        identifier = native.new_document_id()
        self.assertTrue(native.is_document_id(identifier))
        self.assertFalse(native.is_document_id("-" + identifier[1:]))
        self.assertFalse(native.is_document_id(identifier[:-1] + "-"))

    def test_snapshot_identity_units_revision(self):
        document = native.Document()
        self.assertEqual(str(uuid.UUID(document.id)), document.id)
        document.add_parameter("width", 20, "mm")
        document.add_parameter("angle", 90, "deg")
        document.set_parameter_value("width", 30)
        document.set_parameter_value("width", 30)
        self.assertEqual(document.revision, 3)
        restored = native.Document.from_snapshot(json.loads(json.dumps(document.to_snapshot())))
        self.assertEqual(restored.to_snapshot(), document.to_snapshot())
        future = document.to_snapshot()
        future["schema_version"] += 1
        with self.assertRaises(ValueError):
            native.Document.from_snapshot(future)
        with self.assertRaises(ValueError):
            document.add_parameter("width", 1, "mm")
        with self.assertRaises(ValueError):
            document.add_parameter("temperature", 1, "K")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
