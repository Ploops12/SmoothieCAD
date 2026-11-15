"""Blender child for namespace-isolated relocation and loader controls."""

import argparse
import ctypes
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--package-dir", required=True, type=Path)
parser.add_argument("--work-dir", required=True, type=Path)
parser.add_argument("--mode", required=True, choices=("positive", "missing-bootstrap", "missing-direct", "omitted-record", "corrupt-resource", "foreign-runtime", "deleted-runtime"))
parser.add_argument("--result", required=True, type=Path)
parser.add_argument("--missing-library", required=True)
parser.add_argument("--masked-library", required=True, type=Path)
parser.add_argument("--foreign-library", required=True, type=Path)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
package = options.package_dir.resolve()
manifest = json.loads((package / "runtime-manifest.json").read_text())
assert stat.S_ISCHR(options.masked_library.stat().st_mode), "Host OCCT namespace mask is not active"
assert "LD_LIBRARY_PATH" not in os.environ and "CASROOT" not in os.environ
assert not any(key.startswith("CSF_") for key in os.environ)


def mapped_occt():
    paths = set()
    for line in Path("/proc/self/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6:
            continue
        pathname = fields[5].removesuffix(" (deleted)")
        if pathname.startswith("/") and re.match(r"libTK[^/]*\.so(?:\.|$)", Path(pathname).name):
            paths.add(Path(pathname).resolve())
    return sorted(paths)


def assert_bundled_maps(required=True):
    paths = mapped_occt()
    if required:
        assert paths, "Native OCCT code did not map any runtime libraries"
    for path in paths:
        assert path.is_relative_to(package / ".libs"), f"OCCT escaped relocated package: {path}"
    return [str(path) for path in paths]


def exchange_environment():
    # os.environ is Python's cached mapping and cannot detect a leaked native
    # setenv(). Inspect the actual C process environment instead.
    values = ctypes.POINTER(ctypes.c_char_p).in_dll(ctypes.CDLL(None), "environ")
    result = {}
    index = 0
    while values[index] is not None:
        key, value = os.fsdecode(values[index]).split("=", 1)
        if key == "CASROOT" or key.startswith("CSF_"):
            result[key] = value
        index += 1
    return result


result = {"passed": True, "mode": options.mode}
if options.mode == "missing-direct":
    # Bypass __init__/runtime.prepare entirely: the platform loader itself must
    # reject the missing DT_NEEDED library rather than find a host installation.
    native_path = package / manifest["module"]["filename"]
    try:
        specification = importlib.util.spec_from_file_location("_native", native_path)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
    except (ImportError, OSError) as error:
        message = str(error)
        assert options.missing_library in message, message
        result["loader_error"] = message
    else:
        raise AssertionError("Direct native import fell back after removing a required bundled OCCT library")
    result["occt_maps"] = assert_bundled_maps(required=False)
else:
    if options.mode in {"foreign-runtime", "deleted-runtime"}:
        ctypes.CDLL(str(options.foreign_library), mode=ctypes.RTLD_GLOBAL)
        if options.mode == "deleted-runtime":
            options.foreign_library.unlink()
    sys.path.insert(0, str(package.parent))
    addon = importlib.import_module("blendercad")
    assert Path(addon.__file__).resolve().parent == package
    if options.mode != "positive":
        assert addon._native is None, "Manifest loader accepted a missing required runtime library"
        message = str(addon._native_error)
        expected = {"missing-bootstrap": options.missing_library,
                    "omitted-record": "dependency manifest", "corrupt-resource": "checksum mismatch",
                    "foreign-runtime": "Another OCCT runtime", "deleted-runtime": "replaced while Blender"}[options.mode]
        assert expected in message, message
        result["bootstrap_error"] = message
        if options.mode in {"foreign-runtime", "deleted-runtime"}:
            assert mapped_occt() == [options.foreign_library.resolve()]
            result["occt_maps"] = [str(path) for path in mapped_occt()]
        else:
            result["occt_maps"] = assert_bundled_maps(required=False)
        addon.register()
        try:
            assert bpy.ops.wm.open_mainfile(filepath=str(options.work_dir / "relocated.blend")) == {"FINISHED"}
            snapshot = bpy.context.scene.blendercad_document_snapshot
            assert snapshot, "Failed-runtime registration discarded saved CAD definitions"
            for operator in (bpy.ops.blendercad.new_document, bpy.ops.blendercad.recompute,
                             bpy.ops.blendercad.fillet, bpy.ops.blendercad.add_rectangle_solid):
                assert not operator.poll(), "CAD editing remained enabled without a usable native runtime"
            assert bpy.context.scene.blendercad_document_snapshot == snapshot
            result["failed_runtime_registration"] = True
        finally:
            addon.unregister()
    else:
        assert addon._native is not None, getattr(addon, "_native_error", "Native module unavailable")
        n = addon._native
        assert bpy.app.version[:3] == (5, 2, 3), bpy.app.version
        assert sys.version_info[:2] == (3, 14), sys.version
        assert Path(n.__file__).resolve().parent == package
        assert Path(n.exchange_resource_directory()).resolve() == package / "resources"
        addon.register()
        try:
            from blendercad import commands, presentation as p
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)
            assert bpy.ops.blendercad.new_document() == {"FINISHED"}
            assert bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=10) == {"FINISHED"}
            scene = bpy.context.scene
            document = p.read_document(scene)
            base = document.features[0]
            problem = document.evaluated_sketch(base.sketch_id)
            assert n.new_sketch_solver(document.geometric_tolerance_mm).solve(problem).state == n.SketchSolveState.FULLY_CONSTRAINED
            line = problem.entities[0]
            target = n.PersistentReference(base.body_id, n.TopologyKind.EDGE,
                n.ReferenceAnchor(base.id, "edge:sweep:" + line.id + ":start"))
            fillet = document.add_fillet("Relocated runtime fillet", base.id, [target], 1)
            commands.commit(scene, document)
            assert p.runtime(scene).report.features[-1].state == n.EvaluationState.UP_TO_DATE
            expected = 6000 - (1 - math.pi / 4) * 10
            evaluator = p.runtime(scene).evaluator
            assert math.isclose(evaluator.volume_mm3(document, base.body_id), expected, rel_tol=1e-10)
            owner, = p.hosts(scene, document.id)
            packet = p.current_packet(scene, owner)
            assert len(packet.arrays["positions"]) and len(packet.arrays["triangle_faces"])
            # Host overrides must not choose unavailable external resource
            # paths, but must be restored for other process users afterwards.
            os.environ["CSF_STEPDefaults"] = str(options.work_dir / "absent external resources")
            os.environ["CSF_STEPUserDefaults"] = str(options.work_dir / "absent user overrides")
            baseline_environment = exchange_environment()
            step_path = str(options.work_dir / "relocated.step")
            evaluator.write_step(document, base.body_id, step_path)
            kernel = n.new_geometry_adapter(document.geometric_tolerance_mm)
            exchanged = kernel.read_step(step_path)
            assert kernel.is_valid(exchanged)
            assert math.isclose(kernel.volume_mm3(exchanged), expected, rel_tol=1e-9)
            kernel.release(exchanged)
            assert exchange_environment() == baseline_environment, "STEP leaked process-global resource environment"
            try:
                kernel.read_step(str(options.work_dir / "missing.step"))
            except RuntimeError:
                pass
            else:
                raise AssertionError("Missing STEP file unexpectedly imported")
            assert exchange_environment() == baseline_environment, "Failed STEP import leaked resource environment"
            try:
                n.configure_exchange_resources(str(options.work_dir))
            except ValueError:
                pass
            else:
                raise AssertionError("Incomplete exchange resources were accepted")
            assert Path(n.exchange_resource_directory()).resolve() == package / "resources"
            snapshot = scene.blendercad_document_snapshot
            blend_path = str(options.work_dir / "relocated.blend")
            assert bpy.ops.wm.save_as_mainfile(filepath=blend_path) == {"FINISHED"}
            assert bpy.ops.wm.open_mainfile(filepath=blend_path) == {"FINISHED"}
            scene = bpy.context.scene
            document = p.read_document(scene)
            assert scene.blendercad_document_snapshot == snapshot and document.features[-1].id == fillet
            assert math.isclose(p.runtime(scene).evaluator.volume_mm3(document, base.body_id), expected, rel_tol=1e-10)
            result.update(volume_mm3=expected, resources=n.exchange_resource_directory(),
                native_module=str(Path(n.__file__).resolve()), occt_maps=assert_bundled_maps(),
                solver=True, fillet=True, tessellation=True, step_round_trip=True, save_reopen=True)
        finally:
            addon.unregister()
options.result.write_text(json.dumps(result, indent=2))
print("Relocation child passed: " + options.mode)
