#!/usr/bin/env python3
"""Package intentional project sources and offline native rebuild inputs."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


PROJECT_DIRS = {"addon", "bindings", "cmake", "docs", "src", "tests", "third_party", "tools"}
PROJECT_FILES = {"CMakeLists.txt", "README.md"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    for name in ("source-dir", "binary-dir", "output", "eigen-source-dir", "boost-source-dir", "pybind11-source-dir", "occt-source-dir", "runtime-manifest"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--occt-resource-dir", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--include-project-path", action="append", default=[])
    args = parser.parse_args()
    source = args.source_dir.resolve()
    if not (args.binary_dir / ".blendercad-build").is_file():
        parser.error("Source companions require a configured BlenderCAD build")
    files = {}

    def add(path, archive_path, root):
        path = path.resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Missing or external source input: {path}")
        if archive_path in files and files[archive_path] != path:
            raise ValueError(f"Duplicate source archive entry: {archive_path}")
        files[archive_path] = path

    if (source / ".git").exists():
        raw = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z"])
        project_paths = raw.decode().split("\0")[:-1]
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    else:
        previous = json.loads((source.parent / "SOURCE-MANIFEST.json").read_text())
        project_paths = [name.removeprefix("project/") for name in previous["files"] if name.startswith("project/")]
        revision = previous["project_revision"]
    for name in sorted(set(project_paths + args.include_project_path)):
        relative = Path(name)
        if relative.parts[0] in PROJECT_DIRS or name in PROJECT_FILES or name.startswith("LICENSE"):
            add(source / relative, "project/" + relative.as_posix(), source)

    def tree(root, prefix, selections=None):
        if not root.is_dir():
            raise ValueError(f"Required dependency source tree is absent: {root}")
        candidates = [root / item for item in selections] if selections else [root]
        for candidate in candidates:
            if not candidate.exists():
                raise ValueError(f"Required dependency source input is absent: {candidate}")
            paths = candidate.rglob("*") if candidate.is_dir() else [candidate]
            for path in sorted(paths):
                relative = path.relative_to(root)
                if path.is_file() and not any(part in {".git", "__pycache__"} for part in relative.parts) and path.suffix != ".pyc":
                    add(path, prefix + "/" + relative.as_posix(), root)

    tree(args.occt_source_dir, "dependencies/occt")
    tree(args.eigen_source_dir, "dependencies/eigen")
    # PlaneGCS uses Boost headers only: retain the complete header tree and its
    # actual license, not unrelated compiled-library build/test distributions.
    tree(args.boost_source_dir, "dependencies/boost", ["boost", "LICENSE_1_0.txt"])
    tree(args.pybind11_source_dir, "dependencies/pybind11")
    tree(args.binary_dir / "package/blendercad/notices", "notices")
    add(args.runtime_manifest, "binary-runtime-manifest.json", args.binary_dir)
    dependencies = {}
    for name, root, pin in (("OCCT", args.occt_source_dir, "a016080bf6738d6aeae020badee4e888ad1540a5"),
                            ("Eigen", args.eigen_source_dir, "3147391d946bb4b6c68edd901f2add6ac1f31f8c"),
                            ("pybind11", args.pybind11_source_dir, "97bf890db679505a14dfe547a5e77bb2bd05dc90")):
        actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() if (root / ".git").exists() else None
        dependencies[name] = {"recorded_pin": pin, "source_git_revision": actual}
    dependencies["Boost"] = {"version": "1.88.0", "upstream_archive_sha256": "46d9d2c06637b219270877c9e16155cbd015b6dc84349af064c088e9b5b12f7b"}
    dependencies["PlaneGCS"] = json.loads((source / "third_party/planegcs/manifest.json").read_text())
    manifest = {
        "format_version": 1, "version": args.version, "project_revision": revision,
        "project_revision_note": "Repository HEAD at packaging; the included file hashes define the actual working-tree snapshot",
        "dependencies": dependencies,
        "files": {name: digest(path) for name, path in sorted(files.items())},
        "dependency_scope": {"OCCT": "complete cached pinned upstream source baseline; installed distribution binaries may have patches",
                             "Eigen": "complete cached source tree", "Boost": "complete header tree and license",
                             "pybind11": "complete cached source tree", "PlaneGCS": "vendored upstream and local compatibility files in project"},
        "platform_toolchain": "CMake 3.25+, C++20 compiler, Python 3.14 SDK and Blender 5.2.3 are host prerequisites; not included",
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    instructions = b'''# Rebuild and relink the native module

Normal addon installation uses the binary ZIP: it needs no downloads or compiler.
This source companion is for inspection, modification and relinking, including
the statically linked PlaneGCS solver and the local FreeCAD compatibility shims.
It does not assign a new license to the project; preserve individual notices.

From this archive's root, with the documented host toolchain installed:

```sh
cmake -S project -B rebuild -DCMAKE_BUILD_TYPE=Release \\
  -DBLENDERCAD_OFFLINE=ON -DBLENDERCAD_USE_SYSTEM_DEPENDENCIES=OFF \\
  -DFETCHCONTENT_SOURCE_DIR_BLENDERCAD_OCCT="$PWD/dependencies/occt" \\
  -DFETCHCONTENT_SOURCE_DIR_BLENDERCAD_EIGEN="$PWD/dependencies/eigen" \\
  -DFETCHCONTENT_SOURCE_DIR_BLENDERCAD_BOOST="$PWD/dependencies/boost" \\
  -DFETCHCONTENT_SOURCE_DIR_BLENDERCAD_PYBIND11="$PWD/dependencies/pybind11"
cmake --build rebuild --target _native -j2
```

This builds the selected OCCT toolkits and relinks the extension; no external
CAD application is required. To modify a vendored PlaneGCS algorithm, update its
SHA256 in project/third_party/planegcs/manifest.json after the intentional edit:
the normal build rejects unrecorded vendor changes. Rebuild _native and retain
the changed sources/manifest. Use package_addon to regenerate a matching runtime
ZIP and notices. Runtime notice mappings can be supplied using
BLENDERCAD_RUNTIME_LICENSE_ROOT or BLENDERCAD_NOTICES_OVERRIDE_DIR on other hosts.

SOURCE-MANIFEST.json records every included source hash; binary-runtime-manifest.json
records the original module/runtime hashes, ABI requirements and binary paths.
The original binary may use an installed distribution OCCT package: the cached
upstream source is a reproducible rebuild baseline, not evidence that distribution
patches were absent. Rebuilding from these pinned sources need not produce the
same binary bytes. Compiler/SDK/platform components are host prerequisites.
'''
    generated = {"SOURCE-MANIFEST.json": manifest_bytes, "BUILDING-AND-RELINKING.md": instructions}
    fingerprint = hashlib.sha256(manifest_bytes + instructions).hexdigest()
    stamp = args.output.with_suffix(args.output.suffix + ".json")
    if args.output.is_file() and stamp.is_file():
        previous = json.loads(stamp.read_text())
        if previous.get("source_fingerprint") == fingerprint and previous.get("archive_sha256") == digest(args.output):
            print("Source companion unchanged: " + str(args.output))
            return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as temporary:
        candidate = Path(temporary.name)
    try:
        with candidate.open("wb") as stream, gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w|") as archive:
            for name in sorted(set(files) | set(generated)):
                info = tarfile.TarInfo(name)
                info.mode = 0o755 if name in files and files[name].stat().st_mode & 0o111 else 0o644
                if name in generated:
                    data = generated[name]
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
                else:
                    info.size = files[name].stat().st_size
                    with files[name].open("rb") as source_stream:
                        archive.addfile(info, source_stream)
        candidate.chmod(0o644)
        candidate.replace(args.output)
        stamp.write_text(json.dumps({"source_fingerprint": fingerprint, "archive_sha256": digest(args.output)}, indent=2))
    finally:
        candidate.unlink(missing_ok=True)
    print(f"Packaged {len(files)} source/rebuild files: {args.output}")


if __name__ == "__main__":
    main()
