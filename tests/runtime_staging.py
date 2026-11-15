"""Reject mixed OCCT discovery and preserve unowned staging contents."""

import argparse
from pathlib import Path
import subprocess
import tempfile

parser = argparse.ArgumentParser()
for name in ("cmake", "script", "module", "resource-dir", "library-dir", "work-dir"):
    parser.add_argument("--" + name, required=True, type=Path)
options = parser.parse_args()
options.work_dir.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(dir=options.work_dir) as temporary:
    work = Path(temporary)

    def stage(library_dir, output):
        return subprocess.run([str(options.cmake), f"-DMODULE={options.module}", f"-DOUTPUT_DIR={output}",
            f"-DRESOURCE_DIR={options.resource_dir}", f"-DLIBRARY_DIR={library_dir}",
            "-P", str(options.script)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

    empty = work / "incomplete selected OCCT"
    empty.mkdir()
    result = stage(empty, work / "mixed output")
    assert result.returncode and "outside the selected library directory" in result.stdout, result.stdout
    output = work / "unowned output"
    (output / ".libs").mkdir(parents=True)
    sentinel = output / ".libs/keep.txt"
    sentinel.write_text("unrelated contents")
    result = stage(options.library_dir, output)
    assert result.returncode and "unmarked runtime staging" in result.stdout, result.stdout
    assert sentinel.read_text() == "unrelated contents"
print("Runtime staging: mixed OCCT fallback rejected; unowned staging contents preserved")
