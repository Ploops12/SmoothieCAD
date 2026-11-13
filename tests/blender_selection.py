"""Packaged CAD face/edge/vertex picking, native measurements and invalidation."""

import argparse
import importlib
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import zipfile

import bpy
import numpy as np
from mathutils import Matrix, Vector

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def view_at(cx=0.015, cy=0.01, width=0.08, height=0.06, near=0.1, far=-0.1):
    # Deterministic orthographic clip transform. Real RegionView3D event and GPU
    # integration are exercised separately by check_solid_ui.
    matrix = Matrix(((2 / width, 0, 0, -2 * cx / width),
                     (0, 2 / height, 0, -2 * cy / height),
                     (0, 0, -2 / (near - far), (near + far) / (near - far)),
                     (0, 0, 0, 1)))
    return SimpleNamespace(perspective_matrix=matrix, is_perspective=False)


with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, highlight, presentation as p, selection
        n = addon._native
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.blendercad.new_document()
        bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5)
        scene = bpy.context.scene
        host = bpy.context.active_object
        feature = p.read_document(scene).features[0].id
        region = SimpleNamespace(width=800, height=600)
        view = view_at()

        def screen(point):
            clip = view.perspective_matrix @ Vector((*point, 1))
            return Vector(((clip.x / clip.w + 1) * region.width / 2,
                           (clip.y / clip.w + 1) * region.height / 2))

        def pick(point, mode, radius=8):
            bpy.context.view_layer.update()
            return selection.pick_screen(scene, region, view, screen(point), mode, radius,
                                         bpy.context.evaluated_depsgraph_get())

        surface = pick((0.015, 0.01, 0.005), "FACE")
        assert surface and surface[1].kind == "FACE"
        assert abs(surface[1].point_mm[2] - 5) < 1e-6
        p.apply_pick(bpy.context, *surface)
        assert abs(p.current_measure(scene, host).area_mm2 - 600) < 1e-6
        edge = pick((0.015, 0, 0.005), "EDGE")
        assert edge and edge[1].kind == "EDGE" and abs(edge[1].point_mm[2] - 5) < 1e-5
        p.apply_pick(bpy.context, *edge)
        assert abs(p.current_measure(scene, host).length_mm - 30) < 1e-6
        assert p.persistent_target(scene, host, edge[1]).kind == n.TopologyKind.EDGE
        assert bpy.ops.blendercad.save_reference(name="Front top edge") == {"FINISHED"}
        assert p.read_document(scene).references[0].target.kind == n.TopologyKind.EDGE
        assert p.runtime(scene).selected_picks == [] and p.current_measure(scene, host) is None
        edge = pick((0.015, 0, 0.005), "EDGE")
        p.apply_pick(bpy.context, *edge)
        vertex = pick((0, 0, 0.005), "VERTEX")
        assert vertex and vertex[1].kind == "VERTEX" and abs(vertex[1].point_mm[2] - 5) < 1e-5
        p.apply_pick(bpy.context, *vertex, extend=True)
        assert len(p.runtime(scene).selected_picks) == 2 and len(tuple(p.visible_selection(scene))) == 2
        assert p.persistent_target(scene, host, vertex[1]).kind == n.TopologyKind.VERTEX
        p.apply_pick(bpy.context, *vertex, extend=True)
        assert len(p.runtime(scene).selected_picks) == 1 and host.select_get()
        assert p.runtime(scene).last_pick.kind == "EDGE" and p.current_measure(scene, host).length_mm is not None
        p.apply_pick(bpy.context, *edge, extend=True)
        assert p.runtime(scene).selected_picks == [] and not host.select_get()
        assert pick((0.015, 0.01, 0.005), "EDGE") is None, "Interior face pixels selected a distant CAD edge"
        assert pick((0.015, 0.01, 0.005), "VERTEX") is None
        # Screen tolerance is pixels, independent of zoom and scene units.
        view = view_at(width=0.16, height=0.12)
        edge = pick((0.015, 0, 0.005), "EDGE")
        assert edge and abs(p.measure_pick(scene, host, edge[1]).length_mm - 30) < 1e-6
        scene.unit_settings.scale_length = 0.01
        assert p.current_measure(scene, host) is None
        assert pick((0.015, 0, 0.005), "EDGE") is None
        p.sync(scene)
        view = view_at(cx=1.5, cy=1, width=8, height=6, near=10, far=-10)
        edge = pick((1.5, 0, 0.5), "EDGE")
        assert edge and abs(edge[1].point_mm[2] - 5) < 1e-5
        assert abs(p.measure_pick(scene, host, edge[1]).length_mm - 30) < 1e-6
        scene.unit_settings.scale_length = 1
        p.sync(scene)
        host.location = (0.1, 0.2, 0)
        host.rotation_euler.z = math.pi / 2
        view = view_at(cx=0.09, cy=0.215)
        bpy.context.view_layer.update()
        world = host.matrix_world @ Vector((0.015, 0, 0.005))
        edge = pick(world, "EDGE")
        assert edge and abs(p.measure_pick(scene, host, edge[1]).length_mm - 30) < 1e-6
        host.location = (0, 0, 0)
        host.rotation_euler = (0, 0, 0)
        view = view_at()
        # Ordinary render geometry in front blocks all CAD domains.
        bpy.ops.mesh.primitive_cube_add(size=0.05, location=(0.015, 0.01, 0.05))
        blocker = bpy.context.active_object
        for point, mode in (((0.015, 0.01, 0.005), "FACE"), ((0.015, 0, 0.005), "EDGE"), ((0, 0, 0.005), "VERTEX")):
            assert pick(point, mode) is None, mode
        bpy.data.objects.remove(blocker, do_unlink=True)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        edge = pick((0.015, 0, 0.005), "EDGE")
        p.apply_pick(bpy.context, *edge)
        previous = edge[1]
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=7) == {"FINISHED"}
        assert p.runtime(scene).selected_picks == [] and p.current_measure(scene, host) is None
        assert tuple(p.visible_selection(scene)) == ()
        try:
            p.measure_pick(scene, host, previous)
        except ValueError:
            pass
        else:
            raise AssertionError("Measured an expired subelement selection")
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=0) == {"FINISHED"}
        assert pick((0.015, 0.01, 0.007), "FACE") is None
        assert pick((0.015, 0, 0.007), "EDGE") is None
        assert bpy.ops.blendercad.set_feature_depth(feature_id=feature, depth_mm=5) == {"FINISHED"}
        # An unsupported display scaling gap cannot provide a valid CAD query.
        host.scale = (2, 1, 1)
        bpy.context.view_layer.update()
        assert p.pick_ray(scene, (0.015, 0.01, 0.1), (0, 0, -1)) is None
        host.scale = (1, 1, 1)
        # Clipped geometry is not selected, even though a view-independent ray
        # query could hit it. Near-plane clipping starts at z=0.004 here.
        view = view_at(near=0.004, far=-0.1)
        clipped_edge = pick((0.015, 0, 0.005), "EDGE")
        clipped_vertex = pick((0, 0, 0.005), "VERTEX")
        assert clipped_edge and abs(clipped_edge[1].point_mm[2]) < 1e-5
        assert clipped_vertex and abs(clipped_vertex[1].point_mm[2]) < 1e-5
        partial_edge = pick((0, 0, 0.004), "EDGE")
        assert partial_edge and abs(partial_edge[1].point_mm[2] - 4) < 1e-4
        assert abs(p.measure_pick(scene, host, partial_edge[1]).length_mm - 5) < 1e-6
        # Deliberately coarse display facets must not determine face-ray hits
        # or reported areas at a curved silhouette.
        document = p.read_document(scene)
        circle = n.SketchProblem()
        circle.plane.origin = n.Vector3(100, 0, 0)
        circle.entities = [n.SketchEntity(n.Circle2(n.Point2(0, 0), 10))]
        circle_id = document.add_sketch("Curved selection", circle)
        cylinder = document.add_extrusion("Cylinder", circle_id, 5)
        cylinder_body = document.get_feature(cylinder).body_id
        prepared = p.prepare(scene, document)
        coarse_arrays = prepared.evaluator.tessellate(document, cylinder_body, 2, 0.9)
        unused = prepared.packets[cylinder_body].mesh
        prepared.packets[cylinder_body] = p._make_packet(coarse_arrays, p.unit_scale(scene))
        bpy.data.meshes.remove(unused)
        scene.blendercad_document_snapshot = p.serialize(document)
        p.publish(scene, document, prepared)
        cylinder_host = next(obj for obj in p.hosts(scene, document.id) if obj[p.BODY] == cylinder_body)
        packet = p.current_packet(scene, cylinder_host)
        positions = coarse_arrays["positions"].reshape((-1, 3))
        rim = positions[np.abs(positions[:, 2] - 5) < 1e-6]
        angles = np.unique(np.arctan2(rim[:, 1], rim[:, 0] - 100))
        extended = np.append(angles, angles[0] + 2 * math.pi)
        gap_index = int(np.argmax(np.diff(extended)))
        gap = extended[gap_index + 1] - extended[gap_index]
        angle = extended[gap_index] + gap / 2
        radius = 10 * (1 + math.cos(gap / 2)) / 2
        ray_origin = Vector(((100 + radius * math.cos(angle)) * 0.001, radius * math.sin(angle) * 0.001, 0.1))
        assert packet.bvh.ray_cast(ray_origin, Vector((0, 0, -1)))[0] is None
        exact = p.pick_ray(scene, ray_origin, (0, 0, -1), bpy.context.evaluated_depsgraph_get())
        assert exact and exact[0] == cylinder_host, "Curved CAD silhouette was limited to display triangles"
        assert abs(p.measure_pick(scene, cylinder_host, exact[1]).area_mm2 - math.pi * 100) < 1e-6
        # In an edge-on view the same circle projects its front/back arcs onto
        # identical pixels. An occluded first segment cannot hide the visible
        # arc of that very same CAD edge.
        view = SimpleNamespace(is_perspective=False, perspective_matrix=Matrix(
            ((50, 0, 0, -5), (0, 0, 100, -0.5), (0, 10, 0, 0), (0, 0, 0, 1))))
        arc = pick((0.1, 0, 0.005), "EDGE")
        assert arc and arc[0] == cylinder_host and arc[1].point_mm[1] < -9
        assert abs(p.measure_pick(scene, cylinder_host, arc[1]).length_mm - 20 * math.pi) < 1e-5
        # A failed body's last-good mesh remains a visible occluder, not a
        # source of current CAD hits and not a way to select through it.
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        host.select_set(True)
        bpy.context.view_layer.objects.active = host
        commands.duplicate(bpy.context)
        front = bpy.context.active_object
        front.location.z = 0.02
        document = p.read_document(scene)
        front_feature = next(item.id for item in document.features if item.body_id == front[p.BODY])
        commands.set_depth(bpy.context, front_feature, 0)
        bpy.context.view_layer.update()
        assert p.pick_ray(scene, (0.015, 0.01, 0.1), (0, 0, -1), bpy.context.evaluated_depsgraph_get()) is None
        p.display_for(scene, front).hide_set(True)
        bpy.context.view_layer.update()
        unblocked = p.pick_ray(scene, (0.015, 0.01, 0.1), (0, 0, -1), bpy.context.evaluated_depsgraph_get())
        assert unblocked and unblocked[0] == host
        original_native = p._native
        p._native = None
        try:
            from blendercad.operators import BLENDERCAD_OT_pick_solid
            fake_context = SimpleNamespace(scene=scene, area=SimpleNamespace(type="VIEW_3D"),
                                           region=SimpleNamespace(type="WINDOW"), mode="OBJECT")
            assert not BLENDERCAD_OT_pick_solid.poll(fake_context)
            missing_scene = bpy.data.scenes.new("Unavailable native selection")
            try:
                assert tuple(p.visible_selection(missing_scene)) == ()
            finally:
                bpy.data.scenes.remove(missing_scene)
            try:
                p.Runtime()
            except ValueError as error:
                assert "not installed" in str(error)
            else:
                raise AssertionError("Missing native module did not fail before allocating runtime state")
        finally:
            p._native = original_native
        # Headless registration never touches GPU context resources.
        assert highlight._handler is None and not highlight.last_error
        print("CAD selection: precise faces, edges/vertices, measurements, reference capture, occlusion, units/placement, toggling and stale rejection passed")
    finally:
        addon.unregister()
