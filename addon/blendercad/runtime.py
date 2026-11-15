"""Validate an installed native bundle without downloads or host path lookup."""

import hashlib
import json
from pathlib import Path
import sys


def _file(root, relative):
    if not isinstance(relative, str) or not relative:
        raise ImportError("Invalid BlenderCAD runtime manifest path")
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root):
        raise ImportError("Invalid BlenderCAD runtime manifest path")
    if not path.is_file() or path.is_symlink():
        raise ImportError(f"Incomplete BlenderCAD package: missing {relative}; reinstall the addon")
    return path


def _check_file(root, relative, expected):
    path = _file(root, relative)
    if not isinstance(expected, str) or len(expected) != 64:
        raise ImportError("Invalid BlenderCAD runtime manifest checksum")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ImportError(f"BlenderCAD package checksum mismatch: {relative}; reinstall the addon")


def occt_paths():
    """Linux loader evidence, not a mechanism for loading another CAD runtime."""
    paths = set()
    try:
        lines = Path("/proc/self/maps").read_text().splitlines()
    except OSError as error:
        raise ImportError("BlenderCAD cannot verify the Linux native runtime") from error
    for line in lines:
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].startswith("/"):
            continue
        path = Path(fields[5].removesuffix(" (deleted)"))
        if path.name.startswith("libTK") and ".so" in path.name:
            if fields[5].endswith(" (deleted)"):
                raise ImportError("The OCCT runtime was replaced while Blender was running; restart Blender")
            paths.add(path.resolve())
    return paths


def _check_loaded(root):
    private = root / ".libs"
    if any(not path.is_relative_to(private) for path in occt_paths()):
        raise ImportError("Another OCCT runtime is already loaded; restart Blender without the other CAD addon")


def prepare():
    root = Path(__file__).resolve().parent
    if sys.platform != "linux" or sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 14):
        raise ImportError("This BlenderCAD package requires Linux and Blender's CPython 3.14")
    try:
        manifest = json.loads(_file(root, "runtime-manifest.json").read_text())
        if manifest["format_version"] != 1:
            raise ImportError("Unsupported BlenderCAD runtime manifest version")
        module = manifest["module"]
        if not isinstance(module["filename"], str) or not module["filename"].startswith("_native.") or "/" in module["filename"]:
            raise ImportError("Invalid BlenderCAD native module manifest")
        _check_file(root, module["filename"], module["sha256"])
        if not manifest["libraries"] or not manifest["resources"]:
            raise ImportError("Incomplete BlenderCAD runtime manifest")
        sonames = set()
        needed = list(module["needed"])
        expected_libraries = set()
        for library in manifest["libraries"]:
            if library["path"] != ".libs/" + library["soname"] or "/" in library["soname"]:
                raise ImportError("Invalid BlenderCAD library manifest")
            if library["soname"] in sonames:
                raise ImportError("Duplicate BlenderCAD runtime library")
            sonames.add(library["soname"])
            needed.extend(library["needed"])
            expected_libraries.add(library["path"])
            _check_file(root, library["path"], library["sha256"])
        import re
        for library in manifest["platform_libraries"]:
            name = library["soname"]
            if not isinstance(name, str) or not re.fullmatch(r"lib(c|m|pthread|dl|rt|resolv|util)\.so\.\d+|libstdc\+\+\.so\.6|libgcc_s\.so\.1|ld-linux[^/]*\.so\.\d+", name):
                raise ImportError("Invalid BlenderCAD platform runtime exclusion")
            if name in sonames:
                raise ImportError("Duplicate BlenderCAD platform runtime library")
            sonames.add(name)
            needed.extend(library["needed"])
        if any(not isinstance(name, str) or name not in sonames for name in needed):
            raise ImportError("Incomplete BlenderCAD runtime dependency manifest; reinstall the addon")
        expected_resources = set()
        for resource in manifest["resources"]:
            if not isinstance(resource["path"], str) or not resource["path"].startswith("resources/"):
                raise ImportError("Invalid BlenderCAD resource manifest")
            _check_file(root, resource["path"], resource["sha256"])
            if resource["path"] in expected_resources:
                raise ImportError("Duplicate BlenderCAD runtime resource")
            expected_resources.add(resource["path"])
        for directory, expected in ((".libs", expected_libraries), ("resources", expected_resources)):
            actual = {path.relative_to(root).as_posix() for path in (root / directory).rglob("*") if path.is_file()}
            if actual != expected:
                raise ImportError("BlenderCAD package has unmanifested or missing runtime files; reinstall the addon")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise ImportError("Invalid BlenderCAD runtime package; reinstall the addon") from error
    _check_loaded(root)
    return root


def configure(native, root):
    _check_loaded(root)
    native.configure_exchange_resources(str(root / "resources"))
