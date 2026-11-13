"""Named-reference commands, repair, reload and undo in the packaged addon."""

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
        from blendercad import commands, presentation as p
        n = addon._native
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.blendercad.new_document()
        bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5)
        scene = bpy.context.scene
        host = bpy.context.active_object
        body = host[p.BODY]
        bpy.context.view_layer.update()
        hit = p.pick_ray(scene, (0.015, 0.01, 0.1), (0, 0, -1), bpy.context.evaluated_depsgraph_get())
        assert hit and hit[0] == host
        original_pick = hit[1]
        p.runtime(scene).last_pick = original_pick
        assert bpy.ops.blendercad.save_reference(name="Mounting face") == {"FINISHED"}
        document = p.read_document(scene)
        saved, = document.references
        saved_id = saved.id
        assert saved.target.body_id == body and saved.target.anchor.key == "cap:end"
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.RESOLVED
        baseline = scene.blendercad_document_snapshot
        # Publication invalidates the picked triangle even if CAD geometry was
        # reused. A stale picked face cannot create/repair a saved reference.
        p.runtime(scene).last_pick = original_pick
        try:
            commands.save_reference(bpy.context, "Stale reference")
        except ValueError:
            pass
        else:
            raise AssertionError("Saved an expired picked-face mapping")
        assert scene.blendercad_document_snapshot == baseline and len(p.read_document(scene).references) == 1
        unknown = n.PersistentReference(body, n.TopologyKind.FACE, n.ReferenceAnchor(saved.target.anchor.feature_id, "unknown:repairable"))
        document.repair_reference(saved_id, unknown)
        commands.commit(scene, document)
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.AMBIGUOUS
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="Unresolved CAD reference")
        bpy.context.view_layer.update()
        hit = p.pick_ray(scene, (0.015, 0.01, 0.1), (0, 0, -1), bpy.context.evaluated_depsgraph_get())
        p.runtime(scene).last_pick = hit[1]
        generation = p.current_packet(scene, host).arrays["geometry_generation"]
        assert bpy.ops.blendercad.save_reference(reference_id=saved_id) == {"FINISHED"}
        document = p.read_document(scene)
        repaired = document.get_reference(saved_id)
        assert repaired.name == "Mounting face" and repaired.target.anchor.key == "cap:end"
        assert p.runtime(scene).report.computed_count == 0
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.RESOLVED
        assert p.current_packet(scene, host).arrays["geometry_generation"] == generation
        bpy.ops.ed.undo_push(message="Repair CAD reference")
        bpy.ops.ed.undo()
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert document.get_reference(saved_id).target.anchor.key == "unknown:repairable"
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.AMBIGUOUS
        assert p.runtime(scene).last_pick is None
        bpy.ops.ed.redo()
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert document.get_reference(saved_id).target.anchor.key == "cap:end"
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.RESOLVED
        host, = p.hosts(scene, document.id)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        feature = document.features[0].id
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=8) == {"FINISHED"}
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.RESOLVED
        # Named references are document-level bookmarks. Independent body
        # duplication preserves the original bookmark, not an index-based copy.
        assert bpy.ops.blendercad.duplicate_solid() == {"FINISHED"}
        duplicate = bpy.context.active_object
        assert duplicate[p.BODY] != body
        document = p.read_document(scene)
        assert len(document.references) == 1 and document.references[0].target.body_id == body
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.RESOLVED
        filepath = str(Path(directory) / "references.blend")
        snapshot = scene.blendercad_document_snapshot
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        scene = bpy.context.scene
        assert scene.blendercad_document_snapshot == snapshot
        document = p.read_document(scene)
        assert document.references[0].id == saved_id
        assert p.runtime(scene).report.references[0].resolution.state == n.ReferenceState.RESOLVED
        assert p.runtime(scene).last_pick is None
        # Explicit body deletion includes references whose creators it deletes.
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        original = next(host for host in p.hosts(scene, document.id) if host[p.BODY] == body)
        original.select_set(True)
        bpy.context.view_layer.objects.active = original
        assert bpy.ops.blendercad.delete_solid() == {"FINISHED"}
        assert p.read_document(scene).references == []
        assert len(p.read_document(scene).features) == 1
        print("Blender references: picked construction identities, stale rejection, explicit repair, undo/redo, duplication scope, load and deletion passed")
    finally:
        addon.unregister()
