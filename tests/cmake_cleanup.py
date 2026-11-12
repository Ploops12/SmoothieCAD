"""Check CMake cleanup boundaries using disposable build-tree fixtures."""

import argparse
from pathlib import Path
import subprocess
import tempfile
import unittest

parser = argparse.ArgumentParser()
parser.add_argument("--cmake", required=True)
parser.add_argument("--script", required=True)
options = parser.parse_args()


class CleanupChecks(unittest.TestCase):
    def run_cleanup(self, directory, mode):
        return subprocess.run([options.cmake, f"-DBLENDERCAD_BUILD_DIR={directory}",
                               f"-DMODE={mode}", "-P", options.script], capture_output=True, text=True)

    def test_dependency_cleanup_preserves_other_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".blendercad-build").touch()
            for cache in ("_deps", "dependencies"):
                (root / cache).mkdir()
                (root / cache / "download.bin").write_bytes(b"cached dependency")
            (root / "keep.txt").write_text("unrelated build output")
            result = self.run_cleanup(root, "dependencies")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "_deps").exists())
            self.assertFalse((root / "dependencies").exists())
            self.assertEqual((root / "keep.txt").read_text(), "unrelated build output")

    def test_full_cleanup_and_refusal_of_unmarked_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "build"
            root.mkdir()
            (root / "keep.txt").touch()
            result = self.run_cleanup(root, "all")
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((root / "keep.txt").exists())
            (root / ".blendercad-build").touch()
            result = self.run_cleanup(root, "all")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main(argv=[__file__], verbosity=2)
