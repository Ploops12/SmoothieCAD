"""Packaged-addon acceptance at the document / managed Solid boundary."""

import argparse
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy
import numpy as np
from mathutils import Vector

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def close(value, expected):
    assert math.isclose(value, expected, rel_tol=1e-6, abs_tol=1e-8), (value, expected)


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
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        assert bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5) == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        host, = p.hosts(scene, document.id)
        body = host[p.BODY]
        feature = document.features[0].id
        sketch = document.features[0].sketch_id
        assert n.new_sketch_solver().solve(document.get_sketch(sketch)).state == n.SketchSolveState.FULLY_CONSTRAINED
        display = p.display_for(scene, host)
        assert host.type == "EMPTY" and display.type == "MESH" and display.hide_select
        assert host.select_get() and bpy.context.active_object == host
        assert all(host.lock_scale) and display.parent == host
        assert p.host_for(display) == host
        assert len(display.data.polygons) == 12
        assert len(display.data.materials) == 1
        state = p.runtime(scene)
        packet = p.current_packet(scene, host)
        surface_vertex_count = len(packet.arrays["positions"]) // 3
        close(state.evaluator.volume_mm3(document, body), 3000)
        positions = np.empty(len(display.data.vertices) * 3, np.float32)
        display.data.vertices.foreach_get("co", positions)
        close(positions.reshape((-1, 3))[:, 0].max(), 0.03)
        close(positions.reshape((-1, 3))[:, 2].max(), 0.005)
        face_ids = np.empty(len(display.data.polygons), np.int32)
        display.data.attributes[p.FACE].data.foreach_get("value", face_ids)
        assert np.array_equal(face_ids, packet.arrays["triangle_faces"])
        edge_ids = np.empty(len(display.data.edges), np.int32)
        display.data.attributes[p.EDGE].data.foreach_get("value", edge_ids)
        assert set(edge_ids) == {-1, *range(len(packet.arrays["edge_offsets"]) - 1)}
        vertex_ids = np.empty(len(display.data.vertices), np.int32)
        display.data.attributes[p.VERTEX].data.foreach_get("value", vertex_ids)
        assert set(vertex_ids) == {-1, *range(len(packet.arrays["cad_vertices"]) // 3)}
        for kind, limit in (("FACE", 6), ("EDGE", 12), ("VERTEX", 8)):
            pick = p.resolve(scene, host, kind, limit - 1, packet.token)
            assert pick.kind == kind and pick.index == limit - 1 and pick.body_id == body
        bpy.context.view_layer.update()
        hit = p.pick_ray(scene, (0.015, 0.01, 0.1), (0, 0, -1), bpy.context.evaluated_depsgraph_get())
        assert hit and hit[0] == host and hit[1].kind == "FACE"
        # The managed mesh participates in ordinary Cycles rendering without a
        # second editable model or a separate CAD renderer.
        camera_data = bpy.data.cameras.new("Solid render camera")
        camera = bpy.data.objects.new("Solid render camera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = (0.015, 0.01, 0.1)
        camera_data.type, camera_data.ortho_scale = "ORTHO", 0.05
        camera_data.clip_start = 0.001
        scene.camera = camera
        light_data = bpy.data.lights.new("Solid render light", "AREA")
        light_data.energy, light_data.size = 1, 0.05
        light = bpy.data.objects.new("Solid render light", light_data)
        scene.collection.objects.link(light)
        light.location = (0.015, 0.01, 0.05)
        if not scene.world.use_nodes:
            scene.world.use_nodes = True
        scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0
        scene.render.engine = "CYCLES"
        scene.cycles.device, scene.cycles.samples, scene.cycles.use_denoising = "CPU", 1, False
        scene.render.resolution_x = scene.render.resolution_y = 32
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.filepath = str(Path(directory) / "solid.png")
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(scene.render.filepath, check_existing=False)
        pixels = np.array(image.pixels[:]).reshape((-1, 4))
        assert pixels[:, :3].max() > 0.05, "Managed display did not render in Cycles"
        bpy.data.images.remove(image)
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.objects.remove(light, do_unlink=True)
        # An ordinary Blender object in front of a CAD body must not be selected
        # through by the CAD picker.
        bpy.ops.mesh.primitive_cube_add(size=0.01, location=(0.015, 0.01, 0.05))
        blocker = bpy.context.object
        bpy.context.view_layer.update()
        assert p.pick_ray(scene, (0.015, 0.01, 0.1), (0, 0, -1), bpy.context.evaluated_depsgraph_get()) is None
        bpy.data.objects.remove(blocker, do_unlink=True)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        # Placement and whole-body materials survive recomputation.
        host.location = (0.1, 0.2, 0.3)
        host.rotation_euler.z = math.pi / 2
        material = bpy.data.materials.new("Acceptance material")
        material_name = material.name
        display.data.materials[0] = material
        old_token = packet.token
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=7) == {"FINISHED"}
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 4200)
        close(host.location.x, 0.1)
        assert p.display_for(scene, host).data.materials[0] == material
        # Reparented/copied tags must not cause adoption of another object's
        # mesh. A rejected rebuild preserves the prior mapping and definition.
        display = p.display_for(scene, host)
        display.parent = None
        try:
            p.sync(scene)
        except ValueError as error:
            assert "parent" in str(error)
        else:
            raise AssertionError("Rebuilt an orphan/misparented tagged display")
        display.parent = host
        # Inject failure after conversion has changed parent/data/tags, and
        # after host unlinking. CANCELLED must restore scene and native state,
        # not just restore the snapshot string.
        prior_snapshot = scene.blendercad_document_snapshot
        prior_mesh = display.data
        prior_token = p.current_packet(scene, host).token
        prior_name = display.name
        prior_mesh_count = len(bpy.data.meshes)
        prior_object_count = len(bpy.data.objects)
        original_retire = p._Journal.retire

        def fail_after_retire(journal, obj):
            original_retire(journal, obj)
            raise RuntimeError("Injected late publication failure")

        p._Journal.retire = fail_after_retire
        try:
            try:
                commands.convert(bpy.context)
            except RuntimeError as error:
                assert "Injected" in str(error)
            else:
                raise AssertionError("Injected transaction failure was ignored")
        finally:
            p._Journal.retire = original_retire
        assert scene.blendercad_document_snapshot == prior_snapshot
        assert host.name in scene.objects and host.select_get() and bpy.context.active_object == host
        assert display.parent == host and display.data == prior_mesh and display.name == prior_name
        assert display.hide_select and display[p.ROLE] == "DISPLAY" and display.data.attributes.get(p.FACE)
        assert p.current_packet(scene, host).token == prior_token
        assert len(bpy.data.meshes) == prior_mesh_count and len(bpy.data.objects) == prior_object_count
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 4200)
        # A normal edit can also fail after swapping a display mesh. The old
        # evaluator and picking packet remain usable after scene rollback.
        original_retire_mesh = p._Journal.retire_mesh

        def fail_after_swap(journal, mesh):
            original_retire_mesh(journal, mesh)
            raise RuntimeError("Injected mesh publication failure")

        p._Journal.retire_mesh = fail_after_swap
        try:
            try:
                commands.set_depth(bpy.context, feature, 8)
            except RuntimeError as error:
                assert "Injected" in str(error)
            else:
                raise AssertionError("Injected mesh publication failure was ignored")
        finally:
            p._Journal.retire_mesh = original_retire_mesh
        assert scene.blendercad_document_snapshot == prior_snapshot and display.data == prior_mesh
        assert p.current_packet(scene, host).token == prior_token
        assert len(bpy.data.meshes) == prior_mesh_count
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 4200)
        try:
            p.resolve(scene, host, "FACE", 0, old_token)
        except ValueError:
            pass
        else:
            raise AssertionError("A republished triangle mapping accepted an expired token")
        # Failed intent retains geometry but cannot supply current picks/export.
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=-1) == {"FINISHED"}
        assert host["blendercad_state"] == "FAILED" and host["blendercad_diagnostic"]
        assert len(p.display_for(scene, host).data.polygons) == 12
        try:
            p.current_packet(scene, host)
        except ValueError:
            pass
        else:
            raise AssertionError("Failed body supplied current picking data")
        try:
            commands.convert(bpy.context)
        except (ValueError, RuntimeError):
            pass
        else:
            raise AssertionError("Converted stale geometry as a current result")
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=7) == {"FINISHED"}
        document = p.read_document(scene)
        # Clone definitions, not object tags. Local entity/constraint identities
        # and body identities are disjoint; named parameters remain shared.
        assert bpy.ops.blendercad.duplicate_solid() == {"FINISHED"}
        clone = bpy.context.active_object
        assert clone != host and clone[p.BODY] != body
        clone_body = clone[p.BODY]
        close(clone.location.x, 0.1)
        document = p.read_document(scene)
        assert len(document.features) == 2 and len(document.sketches) == 2
        snapshots = document.to_snapshot()["sketches"]
        ids = lambda sketch: {sketch["id"], *(entity["id"] for entity in sketch["entities"]), *(constraint["id"] for constraint in sketch["constraints"])}
        assert not ids(snapshots[0]) & ids(snapshots[1])
        assert p.display_for(scene, clone).data.materials[0] == material
        assert bpy.ops.blendercad.convert_to_regular_mesh() == {"FINISHED"}
        converted = bpy.context.active_object
        assert converted.type == "MESH" and p.host_for(converted) is None and not converted.hide_select
        assert not any(key.startswith("blendercad_") for key in converted.keys())
        assert len(converted.data.vertices) == surface_vertex_count
        assert len(converted.data.polygons) == 12
        assert converted.data.materials[0] == material
        assert len(p.read_document(scene).features) == 1
        assert all(obj.get(p.BODY) != clone_body for obj in scene.objects)
        # A snapshot assigned by an external caller invalidates mapping before
        # any queued recomputation; raw unsupported versions remain untouched.
        host.select_set(True)
        converted.select_set(False)
        bpy.context.view_layer.objects.active = host
        stored = scene.blendercad_document_snapshot
        future = json.loads(stored)
        future["schema_version"] += 1
        scene.blendercad_document_snapshot = json.dumps(future)
        try:
            p.current_packet(scene, host)
        except ValueError:
            pass
        else:
            raise AssertionError("Changed snapshot retained a pick cache")
        p.rebuild_after_history(None)
        assert p.runtime(scene).error and json.loads(scene.blendercad_document_snapshot) == future
        scene.blendercad_document_snapshot = stored
        p.sync(scene)
        # Unit changes invalidate picks and rebuild presentation, not CAD sizes.
        old_token = p.current_packet(scene, host).token
        scene.unit_settings.scale_length = 0.01
        try:
            p.current_packet(scene, host, old_token)
        except ValueError:
            pass
        else:
            raise AssertionError("Changed scene units retained stale display scale")
        p.sync(scene)
        close(max(vertex.co.x for vertex in p.display_for(scene, host).data.vertices), 3)
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 4200)
        scene.unit_settings.scale_length = 1
        p.sync(scene)
        # Save/reopen: saved meshes survive, native state and tokens do not.
        old_token = p.current_packet(scene, host).token
        filepath = str(Path(directory) / "solids.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        scene = bpy.context.scene
        document = p.read_document(scene)
        host, = p.hosts(scene, document.id)
        assert host[p.BODY] == body and p.current_packet(scene, host).token != old_token
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 4200)
        assert p.display_for(scene, host).data.materials[0].name == material_name
        # Background scripts explicitly push undo states; UI operators carry
        # UNDO and get this boundary from Blender's event loop.
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="CAD baseline")
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=9) == {"FINISHED"}
        bpy.ops.ed.undo_push(message="CAD depth")
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        host, = p.hosts(scene, document.id)
        close(document.get_feature(feature).depth, 7)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 4200)
        assert bpy.ops.ed.redo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        close(document.get_feature(feature).depth, 9)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 5400)
        # Conversion is one undoable interaction, restoring both intent and
        # managed host/display associations on undo.
        host, = p.hosts(scene, document.id)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        bpy.ops.ed.undo_push(message="Before CAD conversion")
        assert bpy.ops.blendercad.convert_to_regular_mesh() == {"FINISHED"}
        bpy.ops.ed.undo_push(message="CAD conversion")
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        host, = p.hosts(scene, document.id)
        assert p.display_for(scene, host).hide_select
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 5400)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        assert bpy.ops.blendercad.delete_solid() == {"FINISHED"}
        assert not p.hosts(scene, document.id) and not p.read_document(scene).features
        print("Solid integration: display mappings, picking, Cycles, failure, materials, placement, clone/convert/delete, units, load and undo/redo passed")
    finally:
        addon.unregister()
