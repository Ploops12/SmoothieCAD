"""Verify the installed addon, without sys.path injection or manual extraction."""

import argparse
import json
import math
import os
from pathlib import Path
import sys

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--phase", choices=("install", "restart"), required=True)
for name in ("profile", "addon-zip", "fixture-blend", "output-dir", "result"):
    parser.add_argument("--" + name, required=True, type=Path)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
profile = options.profile.resolve()
scripts = Path(bpy.utils.user_resource("SCRIPTS")).resolve()
config = Path(bpy.utils.user_resource("CONFIG")).resolve()
assert scripts.is_relative_to(profile) and config.is_relative_to(profile), (scripts, config)
assert bpy.app.online_access is False
if options.phase == "install":
    assert "blendercad" not in bpy.context.preferences.addons
    assert bpy.ops.preferences.addon_install(filepath=str(options.addon_zip), target="DEFAULT",
        overwrite=True, enable_on_install=False) == {"FINISHED"}
    assert bpy.ops.preferences.addon_enable(module="blendercad") == {"FINISHED"}
else:
    assert "blendercad" in sys.modules, "Saved addon enablement did not run on process restart"
assert "blendercad" in bpy.context.preferences.addons
addon = sys.modules["blendercad"]
root = Path(addon.__file__).resolve().parent
assert root.is_relative_to(scripts / "addons"), root
assert addon._native is not None, addon._native_error
n = addon._native
assert Path(n.__file__).resolve().parent == root
assert Path(n.exchange_resource_directory()).resolve() == root / "resources"
assert addon._runtime.occt_paths() and all(path.is_relative_to(root / ".libs") for path in addon._runtime.occt_paths())
from blendercad import presentation as p

assert bpy.ops.wm.open_mainfile(filepath=str(options.fixture_blend)) == {"FINISHED"}
scene = bpy.context.scene
document = p.read_document(scene)
assert document.geometric_tolerance_mm == 0.001
assert [feature.type for feature in document.features] == ["extrusion", "subtractive_extrusion", "subtractive_extrusion", "fillet"]
assert len(document.sketches) == 3
body = document.features[0].body_id
host = next(obj for obj in p.hosts(scene, document.id) if obj[p.BODY] == body)
for obj in bpy.context.selected_objects:
    obj.select_set(False)
host.select_set(True)
bpy.context.view_layer.objects.active = host
assert all(item.state == n.EvaluationState.UP_TO_DATE for item in p.runtime(scene).report.features)
identities = [feature.id for feature in document.features]
span = 60 if options.phase == "install" else 65
assert bpy.ops.blendercad.parameter(name="span", value=span) == {"FINISHED"}
document = p.read_document(scene)
assert [feature.id for feature in document.features] == identities
values = {parameter.name: parameter.value for parameter in document.parameters}
expected = (values["span"] * values["thickness"] + values["thickness"] * (values["height"] - values["thickness"])) * values["depth"]
expected -= 2 * math.pi * values["hole_radius"] ** 2 * values["thickness"]
expected -= (1 - math.pi / 4) * values["round_radius"] ** 2 * values["depth"]
actual = p.runtime(scene).evaluator.volume_mm3(document, body)
assert math.isclose(actual, expected, rel_tol=1e-9), (actual, expected)
assert all(entry.resolution.state == n.ReferenceState.RESOLVED for entry in p.runtime(scene).report.references)
step = options.output_dir / f"installed-{options.phase}.step"
assert bpy.ops.blendercad.export_step(filepath=str(step)) == {"FINISHED"}
kernel = n.new_geometry_adapter()
shape = kernel.read_step(str(step))
assert kernel.is_valid(shape) and kernel.max_tolerance_mm(shape) <= 0.001
assert math.isclose(kernel.volume_mm3(shape), expected, rel_tol=1e-9)
kernel.release(shape)
if options.phase == "install":
    snapshot = scene.blendercad_document_snapshot
    assert bpy.ops.wm.save_as_mainfile(filepath=str(options.output_dir / "installed-bracket.blend")) == {"FINISHED"}
    assert bpy.ops.wm.save_userpref() == {"FINISHED"}
    assert (config / "userpref.blend").is_file()
    assert scene.blendercad_document_snapshot == snapshot
options.result.write_text(json.dumps({"passed": True, "phase": options.phase, "volume_mm3": actual,
    "document_id": document.id, "body_id": body, "feature_ids": identities,
    "installed_module": str(root), "native_module": str(Path(n.__file__).resolve()),
    "occt_paths": [str(path) for path in sorted(addon._runtime.occt_paths())],
    "scripts": str(scripts), "config": str(config), "step": str(step)}, indent=2))
