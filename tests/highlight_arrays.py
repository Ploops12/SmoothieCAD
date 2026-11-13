"""Semantic face/edge/vertex overlay extraction without a GPU context."""

import argparse
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import bpy
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
temporary = tempfile.TemporaryDirectory()
with zipfile.ZipFile(options.addon_zip) as archive:
    archive.extractall(temporary.name)
sys.path.insert(0, temporary.name)
highlight = importlib.import_module("blendercad.highlight")


def fixture():
    return {
        "positions": np.arange(27, dtype=np.float64),
        "triangles": np.array([0, 1, 2, 2, 3, 4, 4, 5, 6, 6, 7, 8], np.uint32),
        "triangle_faces": np.array([4, 1, 4, 0], np.uint32),
        "face_count": 5,
        "edge_positions": np.array([0, 0, 0, 1, 0, 0, 2, 0, 0, 8, 1, 0, 9, 1, 0], np.float64),
        "edge_offsets": np.array([0, 3, 3, 5], np.uint64),
        "cad_vertices": np.array([5, 6, 7, -3, -2, -1], np.float64),
    }


class HighlightChecks(unittest.TestCase):
    def test_noncontiguous_cad_face_triangles_are_selected_by_face_map(self):
        arrays = fixture()
        original = {key: value.copy() for key, value in arrays.items() if isinstance(value, np.ndarray)}
        primitive, positions, indices = highlight._selection_geometry(arrays, "FACE", 4, 0.001)
        self.assertEqual(primitive, "TRIS")
        np.testing.assert_array_equal(indices, [[0, 1, 2], [4, 5, 6]])
        np.testing.assert_allclose(positions, arrays["positions"].reshape((-1, 3)) * 0.001)
        self.assertEqual(positions.dtype, np.float32)
        for key, expected in original.items():
            np.testing.assert_array_equal(arrays[key], expected)

    def test_edge_ranges_do_not_connect_across_degenerate_entries(self):
        arrays = fixture()
        self.assertIsNone(highlight._selection_geometry(arrays, "EDGE", 1, 1))
        primitive, positions, indices = highlight._selection_geometry(arrays, "EDGE", 2, 0.5)
        self.assertEqual(primitive, "LINE_STRIP")
        self.assertIsNone(indices)
        np.testing.assert_allclose(positions, [[4, 0.5, 0], [4.5, 0.5, 0]])

    def test_cad_vertex_domain_is_not_surface_mesh_vertex_numbering(self):
        primitive, positions, indices = highlight._selection_geometry(fixture(), "VERTEX", 1, 0.001)
        self.assertEqual(primitive, "POINTS")
        self.assertIsNone(indices)
        np.testing.assert_allclose(positions, [[-0.003, -0.002, -0.001]])

    def test_invalid_or_empty_display_elements_never_create_wrong_batches(self):
        arrays = fixture()
        self.assertIsNone(highlight._selection_geometry(arrays, "FACE", 2, 1))
        for kind, index in (("FACE", 5), ("EDGE", 3), ("VERTEX", 2),
                            ("FACE", -1), ("EDGE", True), ("BODY", 0)):
            with self.subTest(kind=kind, index=index), self.assertRaises(ValueError):
                highlight._selection_geometry(arrays, kind, index, 1)

    def test_background_registration_does_not_allocate_gpu_resources(self):
        self.assertTrue(bpy.app.background)
        highlight.register()
        self.assertIsNone(highlight._handler)
        self.assertEqual(highlight._shaders, {})
        highlight.unregister()
        highlight.unregister()


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    temporary.cleanup()
    if not result.wasSuccessful():
        raise SystemExit(1)
