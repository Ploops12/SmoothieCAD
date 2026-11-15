"""Verify the source companion can rebuild/relink offline through CMake."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile


parser = argparse.ArgumentParser()
for name in ("cmake", "blender", "archive", "output-dir"):
    parser.add_argument("--" + name, required=True, type=Path)
options = parser.parse_args()
output = options.output_dir.resolve()
output.mkdir(parents=True, exist_ok=True)
sources = output / "extracted sources"
sources.mkdir(exist_ok=True)
with tarfile.open(options.archive) as archive:
    incoming = json.load(archive.extractfile("SOURCE-MANIFEST.json"))
    allowed = set(incoming["files"]) | {"SOURCE-MANIFEST.json", "BUILDING-AND-RELINKING.md"}
    # Reusing compiler outputs must not reuse a source file omitted from the
    # incoming archive. Otherwise an incomplete companion could pass on rerun.
    for path in sources.rglob("*"):
        if path.is_file() and path.relative_to(sources).as_posix() not in allowed:
            path.unlink()
    changed = []
    for name, expected in incoming["files"].items():
        path = sources / name
        if not path.is_file():
            changed.append(path)
        else:
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    changed.append(path)
    archive.extractall(sources, filter="data")
    # Archives use reproducible timestamps. Preserve incremental dependency
    # builds, but make changed inputs newer than existing objects on reruns.
    for path in changed:
        path.touch()
manifest = json.loads((sources / "SOURCE-MANIFEST.json").read_text())
for name, expected in manifest["files"].items():
    with (sources / name).open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == expected, name
build = output / "offline build"
command = [str(options.cmake), "-S", str(sources / "project"), "-B", str(build),
    "-DCMAKE_BUILD_TYPE=Release", "-DBLENDERCAD_OFFLINE=ON", "-DBLENDERCAD_USE_SYSTEM_DEPENDENCIES=OFF"]
for name in ("occt", "eigen", "boost", "pybind11"):
    command += [f"-DFETCHCONTENT_SOURCE_DIR_BLENDERCAD_{name.upper()}={sources / 'dependencies' / name}"]
environment = {name: value for name, value in os.environ.items()
    if name != "CASROOT" and not name.startswith("CSF_") and name not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT"}}
environment["ALSOFT_DRIVERS"] = "null"


def run(command, name, timeout=1800):
    print("Source companion: " + name, flush=True)
    with (output / f"{name}.log").open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=environment, timeout=timeout)
    assert result.returncode == 0, f"Source companion {name} failed; see {output / (name + '.log')}"


run(command, "configure")
run([str(options.cmake), "--build", str(build), "--target", "stage_runtime", "-j2"], "build")
run(command, "reconfigure")
run([str(options.cmake), "--build", str(build), "--target", "stage_runtime", "-j2"], "rebuild-after-configure")
runtime = json.loads((build / "package/blendercad/runtime-manifest.json").read_text())
occt = [record for record in runtime["libraries"] if record["soname"].startswith("libTK")]
assert occt and all(Path(record["source_path"]).is_relative_to(build / "_deps/occt-install") for record in occt)
run([str(options.blender), "--background", "--factory-startup", "--python-exit-code", "1",
     "--python", str(sources / "project/tests/native_fillets.py"), "--", "--module-dir", str(build / "addon/blendercad")], "native-fillets", timeout=60)
(output / "result.json").write_text(json.dumps({"passed": True, "offline": True,
    "source_file_count": len(manifest["files"]), "occt_libraries": len(occt), "native_fillets": True,
    "archive_sha256": hashlib.sha256(options.archive.read_bytes()).hexdigest()}, indent=2))
print("Source companion offline OCCT/native rebuild and fillet checks passed", flush=True)
