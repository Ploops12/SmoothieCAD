"""Packaged-addon regression checks for host-owned Blender presentation state."""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading
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
        from blendercad import commands, lifecycle, presentation as p
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        assert bpy.ops.blendercad.new_document() == {"FINISHED"}
        assert bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5) == {"FINISHED"}
        scene = bpy.context.scene
        host = bpy.context.active_object
        display = p.display_for(scene, host)
        feature = p.read_document(scene).features[0].id
        first = scene.view_layers[0]
        second = scene.view_layers.new("Lifecycle alternate layer")

        def settle():
            # Refresh Bases after collection changes, outside the addon handler.
            for layer in scene.view_layers:
                layer.update()
            lifecycle.synchronize(scene)
            for layer in scene.view_layers:
                layer.update()

        def fingerprint():
            state = p.runtime(scene)
            packet = p.current_packet(scene, host)
            return (scene.blendercad_document_snapshot, state.document.revision,
                    id(state.evaluator), id(packet), packet.token,
                    display.data.as_pointer(),
                    tuple(tuple(vertex.co) for vertex in display.data.vertices))

        settle()
        prior = fingerprint()
        # Exercise registration itself, not just the explicit sync helper.
        host.hide_set(True, view_layer=first)
        first.update()
        assert display.hide_get(view_layer=first), "Depsgraph hook missed per-layer hiding"
        host.hide_render = True
        first.update()
        assert display.hide_render, "Depsgraph hook missed render visibility"
        host.hide_set(False, view_layer=first)
        host.hide_render = False
        first.update()
        assert not display.hide_get(view_layer=first) and not display.hide_render

        # Worker callbacks must return before touching any Blender identifier.
        errors = []

        class NoBlenderAccess:
            def __getattribute__(self, name):
                raise AssertionError(f"Worker accessed Blender scene property {name}")

        def worker_callback():
            try:
                lifecycle.after_depsgraph(NoBlenderAccess(), None)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=worker_callback)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive() and not errors, errors
        assert fingerprint() == prior
        host.hide_set(True, view_layer=first)
        settle()
        assert display.hide_get(view_layer=first)
        assert not display.hide_get(view_layer=second)
        assert not display.visible_get(view_layer=first)
        assert display.visible_get(view_layer=second)
        host.hide_set(False, view_layer=first)
        host.hide_set(True, view_layer=second)
        host.hide_viewport = True
        host.hide_render = True
        settle()
        assert display.hide_viewport and display.hide_render
        host.hide_viewport = False
        host.hide_render = False
        settle()
        assert not display.hide_viewport and not display.hide_render
        assert not display.hide_get(view_layer=first) and display.hide_get(view_layer=second)

        collection = bpy.data.collections.new("Host-owned collection")
        scene.collection.children.link(collection)
        with lifecycle.suspended():
            collection.objects.link(host)
            for old in tuple(host.users_collection):
                if old != collection:
                    old.objects.unlink(host)
        settle()
        assert set(display.users_collection) == {collection}
        collection.hide_viewport = True
        settle()
        assert not host.visible_get(view_layer=first) and not display.visible_get(view_layer=first)
        collection.hide_viewport = False
        layer_collection = first.layer_collection.children[collection.name]
        layer_collection.exclude = True
        settle()  # Must not call hide_set on objects with no Base in this layer.
        assert not p.runtime(scene).lifecycle_error
        assert host.name not in first.objects and display.name not in first.objects
        assert display.hide_get(view_layer=second)
        layer_collection.exclude = False
        settle()
        host.hide_set(False, view_layer=second)
        settle()
        assert display.visible_get(view_layer=first) and display.visible_get(view_layer=second)
        assert fingerprint() == prior, "Visibility rebuilt geometry or changed design intent"

        # Local-view membership is per viewport, not inherited by parenting.
        area = next(area for area in bpy.context.screen.areas if area.type == "VIEW_3D")
        region = next(region for region in area.regions if region.type == "WINDOW")
        space = area.spaces.active
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        with bpy.context.temp_override(area=area, region=region):
            assert bpy.ops.view3d.localview(frame_selected=False) == {"FINISHED"}
        settle()
        assert space.local_view is not None and host.local_view_get(space)
        assert display.local_view_get(space) and display.visible_get(viewport=space)
        with bpy.context.temp_override(area=area, region=region):
            assert bpy.ops.view3d.localview(frame_selected=False) == {"FINISHED"}
        settle()
        assert fingerprint() == prior

        # Do not parse/adopt a snapshot not validated by the current runtime,
        # particularly a future schema that must remain byte-for-byte intact.
        stored = scene.blendercad_document_snapshot
        future = json.loads(stored)
        future["schema_version"] += 1
        serialized = json.dumps(future)
        with lifecycle.suspended():
            scene.blendercad_document_snapshot = serialized
            host.hide_render = True
            display.hide_render = False
        lifecycle.synchronize(scene)
        assert scene.blendercad_document_snapshot == serialized
        assert not display.hide_render, "Visibility adopted an unvalidated document"
        scene.blendercad_document_snapshot = stored
        host.hide_render = False
        # External snapshot assignment deliberately invalidates native state,
        # even if the original bytes are restored. Revalidate explicitly.
        p.sync(scene)
        display = p.display_for(scene, host)
        settle()
        prior = fingerprint()

        # Shared Blender datablocks cannot represent two independent scene
        # documents. Refuse before changing any child association or flags.
        other = bpy.data.scenes.new("Unsupported shared host scene")
        with lifecycle.suspended():
            other.collection.objects.link(host)
            host.hide_render = True
            display.hide_render = False
        other.view_layers[0].update()
        settle()
        assert "multi-scene" in p.runtime(scene).lifecycle_error, (
            p.runtime(scene).lifecycle_error, [item.name for item in host.users_scene])
        assert not display.hide_render and set(display.users_collection) == {collection}
        other.collection.objects.unlink(host)
        bpy.data.scenes.remove(other)
        host.hide_render = False
        settle()
        assert not p.runtime(scene).lifecycle_error
        assert fingerprint() == prior

        # Unavailable geometry and host visibility compose: exposing history
        # again must not clear the user's independent render/viewport setting.
        document = p.read_document(scene)
        document.set_rollback_before(feature)
        commands.commit(scene, document)
        display = p.display_for(scene, host)
        assert not display[lifecycle.AVAILABLE]
        assert display.hide_viewport and display.hide_render
        host.hide_render = True
        settle()
        document = p.read_document(scene)
        document.set_rollback_before(None)
        commands.commit(scene, document)
        display = p.display_for(scene, host)
        assert display[lifecycle.AVAILABLE]
        assert not display.hide_viewport and display.hide_render
        host.hide_render = False
        settle()

        # Inject failure after lifecycle has moved collections and changed
        # per-layer/global flags. Journal rollback must restore those Blender
        # properties as well as the native cache, packet and snapshot.
        target = bpy.data.collections.new("Uncommitted host destination")
        scene.collection.children.link(target)
        second.layer_collection.children[target.name].exclude = True
        second.update()
        original_sync = lifecycle.sync_pair
        with lifecycle.suspended():
            target.objects.link(host)
            collection.objects.unlink(host)
            first.update()
            host.hide_set(True, view_layer=first)
            display.hide_set(False, view_layer=first)
            display.hide_set(True, view_layer=second)
            host.hide_render = True
            display.hide_render = False
            before = fingerprint()
            previous_available = display[lifecycle.AVAILABLE]

            def fail_after_sync(*args):
                original_sync(*args)
                raise RuntimeError("Injected lifecycle publication failure")

            lifecycle.sync_pair = fail_after_sync
            try:
                document = p.read_document(scene)
                document.set_feature_depth(feature, 8)
                try:
                    commands.commit(scene, document)
                except RuntimeError as error:
                    assert "Injected lifecycle" in str(error)
                else:
                    raise AssertionError("Injected lifecycle failure was ignored")
            finally:
                lifecycle.sync_pair = original_sync
            assert fingerprint() == before
            assert set(display.users_collection) == {collection}
            assert not display.hide_render and not display.hide_get(view_layer=first)
            assert display.hide_get(view_layer=second), "Rollback lost a recreated ViewLayer Base's hidden state"
            assert display[lifecycle.AVAILABLE] == previous_available
        second.layer_collection.children[target.name].exclude = False
        second.update()
        host.hide_set(False, view_layer=first)
        host.hide_render = False
        settle()
        assert set(display.users_collection) == {target}

        # Save_pre synchronizes without requiring a viewport redraw. Loading
        # reconstructs native state while preserving Blender visibility/layers.
        with lifecycle.suspended():
            host.hide_render = True
            host.hide_set(True, view_layer=second)
            display.hide_render = False
            display.hide_set(False, view_layer=second)
        filepath = str(Path(directory) / "lifecycle.blend")
        snapshot = scene.blendercad_document_snapshot
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        assert display.hide_render and display.hide_get(view_layer=second)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        scene = bpy.context.scene
        document = p.read_document(scene)
        host, = p.hosts(scene, document.id)
        display = p.display_for(scene, host)
        first, second = scene.view_layers
        assert scene.blendercad_document_snapshot == snapshot
        assert host.hide_render and display.hide_render
        assert host.hide_get(view_layer=second) and display.hide_get(view_layer=second)
        assert set(display.users_collection) == set(host.users_collection)
        host.hide_set(False, view_layer=second)

        # Static render_init repair must affect the very first render. Use
        # transparent PNG alpha so illumination cannot disguise visibility.
        camera_data = bpy.data.cameras.new("Lifecycle camera")
        camera = bpy.data.objects.new("Lifecycle camera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = (0.015, 0.01, 0.1)
        camera_data.type, camera_data.ortho_scale = "ORTHO", 0.05
        camera_data.clip_start = 0.001
        scene.camera = camera
        scene.render.engine = "CYCLES"
        scene.cycles.device, scene.cycles.samples, scene.cycles.use_denoising = "CPU", 1, False
        scene.render.resolution_x = scene.render.resolution_y = 16
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"
        render_baseline = fingerprint()
        # Isolate render_init from ordinary depsgraph repair; no redraw or
        # background timer is needed to make this first render consistent.
        bpy.app.handlers.depsgraph_update_post.remove(lifecycle.after_depsgraph)
        try:
            for hidden in (True, False):
                with lifecycle.suspended():
                    host.hide_render = hidden
                    display.hide_render = not hidden
                scene.render.filepath = str(Path(directory) / f"lifecycle-{hidden}.png")
                assert bpy.ops.render.render(write_still=True) == {"FINISHED"}
                image = bpy.data.images.load(scene.render.filepath, check_existing=False)
                alpha = max(image.pixels[3::4])
                bpy.data.images.remove(image)
                assert (alpha == 0) if hidden else (alpha > 0.99), (hidden, alpha)
                assert display.hide_render == hidden
                assert fingerprint() == render_baseline
        finally:
            bpy.app.handlers.depsgraph_update_post.append(lifecycle.after_depsgraph)
        print("Blender lifecycle: host visibility, ViewLayers, collections, local view, ownership, future schemas, history, journal rollback, save/reopen and first-render consistency passed")
    finally:
        addon.unregister()
