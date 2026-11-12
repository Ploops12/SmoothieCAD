"""Verify the packaged addon, scene storage and schema protection in Blender."""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])

with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        assert bpy.app.version[:3] == (5, 2, 3), bpy.app.version
        assert addon._native is not None, "Packaged native extension did not load"
        assert bpy.ops.blendercad.new_document.poll()
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        original = bpy.context.scene.blendercad_document_snapshot
        assert not bpy.ops.blendercad.new_document.poll(), "Existing document could be overwritten"
        document = addon._native.Document.from_snapshot(json.loads(original))
        document.add_parameter("width", 24, "mm")
        bpy.context.scene.blendercad_document_snapshot = json.dumps(document.to_snapshot())
        stored = bpy.context.scene.blendercad_document_snapshot
        filepath = str(Path(directory) / "foundation.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        assert bpy.context.scene.blendercad_document_snapshot == stored
        restored = addon._native.Document.from_snapshot(json.loads(stored))
        assert restored.id == document.id
        assert restored.parameters[0].value == 24
        addon.unregister()
        addon.register()
        assert bpy.context.scene.blendercad_document_snapshot == stored
        future = document.to_snapshot()
        future["schema_version"] += 1
        serialized = json.dumps(future)
        bpy.context.scene.blendercad_document_snapshot = serialized
        assert not bpy.ops.blendercad.new_document.poll()
        try:
            addon._native.Document.from_snapshot(json.loads(serialized))
        except ValueError:
            pass
        else:
            raise AssertionError("Future document schema was accepted")
        assert bpy.context.scene.blendercad_document_snapshot == serialized
        print("Blender foundation: package import, registration, save/reopen, and schema protection passed")
    finally:
        addon.unregister()
