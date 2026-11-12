"""Headless policy checks for the packaged Solid-operation guards."""

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
        from blendercad import capabilities

        assert capabilities.INCOMPATIBLE_MESSAGE == (
            "Incompatible operation - Convert to regular mesh first"
        )
        ordinary_mesh = bpy.data.objects.new("Ordinary", bpy.data.meshes.new("OrdinaryMesh"))
        bpy.context.scene.collection.objects.link(ordinary_mesh)
        host = bpy.data.objects.new("CAD Solid", None)
        bpy.context.scene.collection.objects.link(host)
        host["blendercad_role"] = "SOLID"
        host["blendercad_document_id"] = "document-test"
        host["blendercad_body_id"] = "body-test"
        display_mesh = bpy.data.meshes.new("CAD display mesh")
        display_mesh.from_pydata([(0.0, 0.0, 0.0)], [], [])
        display = bpy.data.objects.new("CAD display", display_mesh)
        bpy.context.scene.collection.objects.link(display)
        display.parent = host
        display["blendercad_role"] = "DISPLAY"
        display["blendercad_document_id"] = "document-test"
        display["blendercad_body_id"] = "body-test"
        display.hide_select = True

        assert capabilities.host_for(host) is host
        assert capabilities.host_for(display) is host
        for operation in ("translate", "rotate", "duplicate", "delete", "convert",
                          "edit_definition", "recompute", "pick"):
            capabilities.require_capability([host], operation)
        capabilities.require_capability([ordinary_mesh], "scale")
        for operation in ("scale", "apply", "edit_mode", "mesh_edit", "modifier"):
            try:
                capabilities.require_capability([host], operation)
            except ValueError as error:
                assert str(error) == capabilities.INCOMPATIBLE_MESSAGE
            else:
                raise AssertionError(f"Unsupported {operation} was allowed")

        scene = bpy.context.scene
        scene.blendercad_document_snapshot = json.dumps({"stable": "snapshot"})
        snapshot_before = scene.blendercad_document_snapshot
        host.location = (1.25, -2.0, 3.5)
        host.rotation_euler = (0.1, 0.2, 0.3)
        transform_before = (tuple(host.location), tuple(host.rotation_euler), tuple(host.scale))
        mesh_before = tuple(tuple(vertex.co) for vertex in display_mesh.vertices)

        def call_guard(action):
            try:
                return bpy.ops.blendercad.capability_guard("EXEC_DEFAULT", action=action)
            except RuntimeError as error:
                assert capabilities.INCOMPATIBLE_MESSAGE in str(error), str(error)
                return {"CANCELLED"}

        bpy.ops.object.select_all(action="DESELECT")
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        for action in ("scale", "apply_menu", "edit_mode", "ctrl_tab"):
            result = call_guard(action)
            assert result == {"CANCELLED"}, (action, result)
            assert (tuple(host.location), tuple(host.rotation_euler), tuple(host.scale)) == transform_before
            assert tuple(tuple(vertex.co) for vertex in display_mesh.vertices) == mesh_before
            assert scene.blendercad_document_snapshot == snapshot_before

        # Mixed CAD/ordinary selections are rejected before native duplicate/delete.
        ordinary_mesh.select_set(True)
        for action in ("duplicate", "delete_x", "delete_del"):
            object_count = len(bpy.data.objects)
            result = call_guard(action)
            assert result == {"CANCELLED"}, (action, result)
            assert len(bpy.data.objects) == object_count
            assert (tuple(host.location), tuple(host.rotation_euler), tuple(host.scale)) == transform_before
            assert tuple(tuple(vertex.co) for vertex in display_mesh.vertices) == mesh_before
            assert scene.blendercad_document_snapshot == snapshot_before

        # Ordinary selections fall through to native keymap entries. Invoking
        # a native modal operator inside the guard would alter undo semantics.
        bpy.ops.object.select_all(action="DESELECT")
        ordinary_mesh.select_set(True)
        result = bpy.ops.blendercad.capability_guard("EXEC_DEFAULT", action="scale")
        assert result == {"PASS_THROUGH"}, result

        keyconfig = bpy.context.window_manager.keyconfigs.active
        if keyconfig is not None:
            capability_items = [
                item for km in keyconfig.keymaps for item in km.keymap_items
                if item.idname == "blendercad.capability_guard"
            ]
            # Some background builds expose no active keyconfig; registration
            # remains valid and the guard operator is still testable directly.
            if capability_items:
                actions = {item.properties.action for item in capability_items}
                assert {"edit_mode", "ctrl_tab", "scale", "apply_menu", "duplicate",
                        "duplicate_linked", "delete_x", "delete_del"} <= actions
    finally:
        addon.unregister()

print("BlenderCAD capability guard checks passed")
