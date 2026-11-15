"""Run Blender against a relocated addon while host OCCT files are masked.

Bubblewrap changes only child mount namespaces. Missing-library controls also
import the extension directly, independently of the addon's manifest validator.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile


def inside(path, directory):
    return path == directory or directory in path.parents


def occt_library(path):
    return re.match(r"libTK[^/]*\.so(?:\.|$)", path.name) is not None


def host_files(manifest, extra_directories):
    directories = {Path(item) for item in extra_directories}
    directories.update(Path(item) for item in ("/usr/lib", "/usr/lib64", "/lib", "/lib64", "/usr/local/lib",
        "/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu"))
    files = set()
    for entry in manifest["libraries"]:
        source = Path(entry["source_path"])
        if occt_library(source):
            directories.add(source.parent)
            if source.is_file():
                files.add(source.resolve())
    for directory in directories:
        if directory.is_dir():
            for path in directory.glob("libTK*.so*"):
                if path.is_file() and occt_library(path):
                    files.add(path.resolve())
    if not files:
        raise AssertionError("Relocation negative control requires host/source OCCT libraries to mask")
    resources = {Path("/usr/share/opencascade/resources"), Path("/usr/share/opencascade-7.9.3/resources")}
    for entry in manifest["resources"]:
        source = Path(entry["source_path"])
        if source.is_file():
            resource_root = next((parent for parent in source.parents if parent.name == "resources"), source.parent)
            resources.add(resource_root.resolve())
    resources = sorted((path.resolve() for path in resources if path.is_dir()), key=lambda path: len(path.parts))
    # Avoid binding a nested path after its ancestor has already become empty.
    roots = []
    for path in resources:
        if not any(inside(path, ancestor) for ancestor in roots):
            roots.append(path)
    return sorted(files), roots


def clean_environment(work):
    environment = {key: value for key, value in os.environ.items()
        if key != "CASROOT" and not key.startswith("CSF_")
        and key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT", "PYTHONPATH"}}
    environment["ALSOFT_DRIVERS"] = "null"
    for key, directory in (("BLENDER_USER_CONFIG", "config"), ("TMPDIR", "tmp"),
                           ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "xdg-config")):
        path = work / directory
        path.mkdir(parents=True, exist_ok=True)
        environment[key] = str(path)
    return environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", required=True, type=Path)
    parser.add_argument("--bwrap", required=True, type=Path)
    parser.add_argument("--addon-zip", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--occt-library-dir", action="append", default=[], type=Path)
    options = parser.parse_args()
    output = options.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "result.json"
    result_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="relocated-", dir=output) as temporary:
        work = Path(temporary)
        positive = work / "package moved here"
        negative = work / "missing bundled runtime"
        faulty = work / "invalid runtime metadata"
        with zipfile.ZipFile(options.addon_zip) as archive:
            archive.extractall(positive)
            archive.extractall(negative)
            archive.extractall(faulty)
        package = positive / "blendercad"
        manifest = json.loads((package / "runtime-manifest.json").read_text())
        assert manifest["format_version"] == 1
        masked_libraries, masked_resources = host_files(manifest, options.occt_library_dir)
        assert any(path.name.startswith("libTKernel.so") for path in masked_libraries)
        required = next(entry for entry in manifest["libraries"] if entry["soname"].startswith("libTKFillet.so"))
        (negative / "blendercad" / required["path"]).unlink()
        kernel = next(entry for entry in manifest["libraries"] if entry["soname"].startswith("libTKernel.so"))
        foreign = work / "other CAD runtime" / kernel["soname"]
        foreign.parent.mkdir()
        shutil.copyfile(package / kernel["path"], foreign)
        empty_resources = work / "unavailable host resources"
        empty_resources.mkdir()
        environment = clean_environment(work)
        namespace = [str(options.bwrap.resolve()), "--die-with-parent", "--ro-bind", "/", "/",
                     "--dev", "/dev", "--proc", "/proc", "--bind", str(work), str(work)]
        for path in masked_libraries:
            namespace += ["--ro-bind", "/dev/null", str(path)]
        for path in masked_resources:
            namespace += ["--ro-bind", str(empty_resources), str(path)]
        reports = {}
        cases = (("positive", positive), ("missing-bootstrap", negative), ("missing-direct", negative),
                 ("omitted-record", faulty), ("corrupt-resource", faulty), ("foreign-runtime", positive),
                 ("deleted-runtime", positive))
        for mode, extraction in cases:
            if mode == "omitted-record":
                damaged = dict(manifest)
                damaged["libraries"] = [entry for entry in manifest["libraries"] if entry != required]
                (faulty / "blendercad/runtime-manifest.json").write_text(json.dumps(damaged))
            elif mode == "corrupt-resource":
                (faulty / "blendercad/runtime-manifest.json").write_text(json.dumps(manifest))
                with (faulty / "blendercad/resources/XSTEPResource/STEP").open("ab") as stream:
                    stream.write(b"\nmodified resource\n")
            child_result = work / f"{mode}.json"
            command = [*namespace, "--", str(options.blender.resolve()), "--background", "--factory-startup",
                "--python-exit-code", "1", "--python", str(options.script.resolve()), "--",
                "--package-dir", str(extraction / "blendercad"), "--work-dir", str(work),
                "--mode", mode, "--result", str(child_result), "--missing-library", required["soname"],
                "--masked-library", str(masked_libraries[0]), "--foreign-library", str(foreign)]
            completed = subprocess.run(command, env=environment, cwd=work, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, timeout=60, check=False)
            log_path = output / f"{mode}.log"
            log_path.write_text(completed.stdout)
            if completed.returncode or not child_result.is_file():
                raise AssertionError(f"Relocation {mode} failed ({completed.returncode}); see {log_path}\n{completed.stdout[-4000:]}")
            report = json.loads(child_result.read_text())
            assert report["passed"], report
            reports[mode] = report
            (output / f"{mode}.json").write_text(json.dumps(report, indent=2))
        summary = {"passed": True, "method": "bwrap host-library/resource masks",
            "masked_libraries": [str(path) for path in masked_libraries],
            "masked_resource_directories": [str(path) for path in masked_resources],
            "missing_library": required["soname"], "checks": reports}
        result_path.write_text(json.dumps(summary, indent=2))
        print("Runtime relocation: bundled OCCT maps, solver/fillet/STEP/save-reopen, masked host resources and missing-library bootstrap/direct-import controls passed")


if __name__ == "__main__":
    main()
