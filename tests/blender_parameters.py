"""Packaged parameter expression commands, persistence and Blender undo."""

import argparse
import importlib
import math
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8), (actual, expected)


with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, presentation as p
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        assert bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5) == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        host, = p.hosts(scene, document.id)
        body, feature = host[p.BODY], document.features[0].id
        assert bpy.ops.blendercad.parameter(name="base", value=2, create=True) == {"FINISHED"}
        assert bpy.ops.blendercad.parameter(name="factor", value=3, create=True, unit="unitless") == {"FINISHED"}
        assert bpy.ops.blendercad.parameter(name="thickness", create=True, use_expression=True,
                                           expression="base * factor + 1 mm") == {"FINISHED"}
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, parameter="thickness") == {"FINISHED"}
        document = p.read_document(scene)
        thickness = next(item for item in document.parameters if item.name == "thickness")
        assert thickness.expression == "base * factor + 1 mm"
        close(thickness.value, 7)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 4200)
        assert bpy.ops.blendercad.parameter(name="base", value=4) == {"FINISHED"}
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 7800)
        # Invalid directed graphs and dimensions fail before any scene/native
        # publication, preserving precise current queries and display mappings.
        baseline = scene.blendercad_document_snapshot
        mesh = p.display_for(scene, host).data
        evaluator = p.runtime(scene).evaluator
        token = p.current_packet(scene, host).token
        for name, expression in (("base", "thickness"), ("thickness", "base + factor"),
                                 ("thickness", "missing + 1 mm"), ("thickness", "base / 0")):
            try:
                commands.set_parameter(bpy.context, name, 0, expression=expression)
            except ValueError:
                pass
            else:
                raise AssertionError(f"Invalid expression accepted: {expression}")
            assert scene.blendercad_document_snapshot == baseline
            assert p.runtime(scene).evaluator is evaluator
            assert p.current_packet(scene, host).token == token
            assert p.display_for(scene, host).data == mesh
            close(evaluator.volume_mm3(p.read_document(scene), body), 7800)
        # UI operators report errors through Blender. Some background builds
        # raise RuntimeError for an ERROR report instead of returning CANCELLED.
        try:
            result = bpy.ops.blendercad.parameter(name="thickness", use_expression=True, expression="base + factor")
        except RuntimeError as error:
            assert "parameter" in str(error).lower() or "dimension" in str(error).lower(), str(error)
        else:
            assert result == {"CANCELLED"}
        assert scene.blendercad_document_snapshot == baseline
        assert p.current_packet(scene, host).token == token
        filepath = str(Path(directory) / "parameters.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        scene = bpy.context.scene
        document = p.read_document(scene)
        host, = p.hosts(scene, document.id)
        assert scene.blendercad_document_snapshot == baseline
        assert next(item for item in document.parameters if item.name == "thickness").expression == "base * factor + 1 mm"
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 7800)
        # Event-loop undo boundaries are explicit in background acceptance.
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="Expression baseline")
        assert bpy.ops.blendercad.parameter(name="thickness", use_expression=True, expression="base * factor + 2 mm") == {"FINISHED"}
        bpy.ops.ed.undo_push(message="Expression edit")
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 8400)
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 7800)
        assert next(item for item in document.parameters if item.name == "thickness").expression == "base * factor + 1 mm"
        assert bpy.ops.ed.redo() == {"FINISHED"}
        scene = bpy.context.scene
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 8400)
        # Switching an expression to an equal literal changes definition, then
        # upstream edits must no longer drive that literal parameter.
        assert bpy.ops.blendercad.parameter(name="thickness", value=14, use_expression=False) == {"FINISHED"}
        assert bpy.ops.blendercad.parameter(name="base", value=5) == {"FINISHED"}
        document = p.read_document(scene)
        assert next(item for item in document.parameters if item.name == "thickness").expression is None
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 8400)
        print("Blender parameters: operators, dimensional/cycle failure atomicity, expression-driven solids, save/reopen and undo/redo passed")
    finally:
        addon.unregister()
