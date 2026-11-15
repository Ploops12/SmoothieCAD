#!/usr/bin/env python3
"""Create a Blender-installable ZIP containing addon sources and its native module."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile


def checked_file(root, relative, digest):
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or invalid package file: {relative}")
    if path.is_symlink():
        raise ValueError(f"Package files must not be symlinks: {path}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise ValueError(f"Package file differs from its manifest: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--addon-dir", required=True, type=Path)
    parser.add_argument("--native-module", required=True, type=Path)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    parser.add_argument("--notices-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if not (args.addon_dir / "__init__.py").is_file():
        parser.error(f"addon entry point not found under {args.addon_dir}")
    if not args.native_module.is_file():
        parser.error(f"native extension not found: {args.native_module}")

    manifest_path = args.runtime_dir / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["format_version"] != 1 or manifest["module"]["filename"] != args.native_module.name:
        parser.error("Runtime manifest does not identify this native module")
    checked_file(args.native_module.parent, args.native_module.name, manifest["module"]["sha256"])
    files = {}

    def add(path, relative):
        name = str(Path("blendercad") / relative)
        if name in files:
            raise ValueError(f"Duplicate addon archive path: {name}")
        files[name] = path

    for path in sorted(args.addon_dir.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            if path.is_symlink():
                raise ValueError(f"Addon source must not be a symlink: {path}")
            add(path, path.relative_to(args.addon_dir))
    add(args.native_module, args.native_module.name)
    add(manifest_path, "runtime-manifest.json")
    expected_runtime = set()
    for field in ("libraries", "resources"):
        for record in manifest[field]:
            relative = record["path"]
            if not relative.startswith(".libs/" if field == "libraries" else "resources/"):
                raise ValueError(f"Unexpected {field} path: {relative}")
            add(checked_file(args.runtime_dir, relative, record["sha256"]), relative)
            expected_runtime.add(relative)
    actual_runtime = {str(path.relative_to(args.runtime_dir)) for directory in (".libs", "resources")
                      for path in (args.runtime_dir / directory).rglob("*")
                      if path.is_file() and path.name != ".blendercad-runtime-stage"}
    if actual_runtime != expected_runtime:
        raise ValueError("Runtime staging contains unmanifested or missing files")
    notices_path = args.notices_dir / "notice-index.json"
    notices = json.loads(notices_path.read_text())
    if notices["schema_version"] != 1:
        raise ValueError("Unsupported notice index")
    add(notices_path, "notices/notice-index.json")
    for record in notices["files"]:
        relative = record["path"]
        add(checked_file(args.notices_dir, relative, record["sha256"]), Path("notices") / relative)
    if {item["soname"] for item in notices["libraries"]} != {item["soname"] for item in manifest["libraries"]}:
        raise ValueError("Notices do not cover the bundled native runtime")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, suffix=".zip", delete=False) as temporary:
        candidate = Path(temporary.name)
    try:
        with zipfile.ZipFile(candidate, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                with path.open("rb") as source, archive.open(info, "w") as destination:
                    shutil.copyfileobj(source, destination)
        candidate.chmod(0o644)
        candidate.replace(args.output)
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
