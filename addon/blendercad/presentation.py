"""Replaceable managed-mesh presentation; document definitions remain native-owned.

All functions in this module run on Blender's main thread. Object hierarchy is
an implementation detail: associations are recovered using explicit identities.
Evaluated-local picks are disposable and must never be saved as design inputs.
"""

from dataclasses import dataclass, field
import copy
import json
import math

import bpy
from bpy.app.handlers import persistent
from mathutils import Vector
from mathutils.bvhtree import BVHTree
import numpy as np

from . import _native

ROLE = "blendercad_role"
DOC = "blendercad_document_id"
BODY = "blendercad_body_id"
TOKEN = "blendercad_display_token"
FACE = "blendercad_face"
EDGE = "blendercad_edge"
VERTEX = "blendercad_vertex"


def host_for(obj):
    if obj is None:
        return None
    if obj.get(ROLE) == "SOLID" and obj.type == "EMPTY":
        return obj
    if obj.get(ROLE) == "DISPLAY" and obj.parent is not None:
        host = obj.parent
        if host.get(ROLE) == "SOLID" and obj.get(DOC) == host.get(DOC) and obj.get(BODY) == host.get(BODY):
            return host
    return None


def selected_hosts(context):
    result = []
    for obj in context.selected_objects:
        host = host_for(obj)
        if host is not None and host not in result:
            result.append(host)
    return result


def hosts(scene, document_id):
    return [obj for obj in scene.objects if obj.get(ROLE) == "SOLID" and obj.get(DOC) == document_id]


def display_for(scene, host):
    candidates = [obj for obj in scene.objects if obj.get(ROLE) == "DISPLAY"
                  and obj.get(DOC) == host.get(DOC) and obj.get(BODY) == host.get(BODY)]
    if len(candidates) > 1:
        raise ValueError("Ambiguous managed displays; repair the object association before rebuilding")
    if candidates and (candidates[0].type != "MESH" or candidates[0].parent != host):
        raise ValueError("Invalid managed display type/parent; repair the object association before rebuilding")
    return candidates[0] if candidates else None


def _validate_associations(scene, document_id):
    existing = hosts(scene, document_id)
    if len({host.get(BODY) for host in existing}) != len(existing):
        raise ValueError("Duplicate Solid host identities; use CAD Duplicate Solid instead of native duplication")
    for host in existing:
        if host.type != "EMPTY":
            raise ValueError("Solid host must have non-mesh Empty type")
        display = display_for(scene, host)
        for obj in (host, display):
            if obj is not None and (obj.library is not None or len(obj.users_scene) > 1):
                raise ValueError("Linked or multi-scene CAD objects are not supported by this presentation adapter")
    body_ids = {host.get(BODY) for host in existing}
    if any(obj.get(ROLE) == "DISPLAY" and obj.get(DOC) == document_id and obj.get(BODY) not in body_ids for obj in scene.objects):
        raise ValueError("Managed display has no Solid host; repair the object association before rebuilding")


def read_document(scene):
    if _native is None:
        raise ValueError("Native module is not installed")
    raw = scene.blendercad_document_snapshot
    if not raw:
        raise ValueError("Create a CAD document first")
    return _native.Document.from_snapshot(json.loads(raw))


def serialize(document):
    return json.dumps(document.to_snapshot(), separators=(",", ":"), sort_keys=True)


def unit_scale(scene):
    scale = scene.unit_settings.scale_length
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("Scene unit scale must be finite and positive")
    return 0.001 / scale


@dataclass
class Packet:
    arrays: dict
    token: str
    bvh: object
    mesh: object
    overlays: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Pick:
    document_id: str
    body_id: str
    feature_id: str
    revision: int
    token: str
    kind: str
    index: int
    point_mm: tuple | None = None
    normal: tuple | None = None


@dataclass
class Prepared:
    report: object
    scale: float
    packets: dict
    evaluator: object
    conversions: list
    new_materials: list = field(default_factory=list)


def discard_prepared(prepared):
    for mesh in [packet.mesh for packet in prepared.packets.values()] + [mesh for _display, mesh in prepared.conversions]:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for material in prepared.new_materials:
        if material.users == 0:
            bpy.data.materials.remove(material)


class Runtime:
    def __init__(self, evaluator=None):
        if _native is None:
            raise ValueError("Native module is not installed")
        self.evaluator = evaluator if evaluator is not None else _native.FeatureEvaluator()
        self.document = None
        self.report = None
        self.serialized = None
        self.scale = None
        self.packets = {}
        self.error = ""
        self.last_pick = None
        self.selected_picks = []
        self.last_measure = None
        self.reference_error = ""


_runtimes = {}


def runtime(scene):
    key = scene.as_pointer()
    if key not in _runtimes:
        _runtimes[key] = Runtime()
    return _runtimes[key]


def invalidate_snapshot(scene, _context):
    state = _runtimes.get(scene.as_pointer())
    if state:
        invalid = copy.copy(state)
        invalid.serialized = None
        invalid.packets = {}
        invalid.last_pick = None
        invalid.selected_picks = []
        invalid.last_measure = None
        invalid.reference_error = ""
        _runtimes[scene.as_pointer()] = invalid


def restore_runtime(scene, state):
    _runtimes[scene.as_pointer()] = state


def _make_packet(arrays, scale):
    """Bulk transfer, with separate semantic CAD edges/vertices in one mesh."""
    surface = arrays["positions"].reshape((-1, 3))
    samples = arrays["edge_positions"].reshape((-1, 3))
    vertices = arrays["cad_vertices"].reshape((-1, 3))
    offsets = arrays["edge_offsets"].astype(np.int64)
    positions = np.concatenate((surface, samples, vertices)) * scale
    triangles = arrays["triangles"].astype(np.int32)
    triangle_count = len(triangles) // 3
    segment_starts = np.concatenate([np.arange(start, end - 1, dtype=np.int32)
                                     for start, end in zip(offsets[:-1], offsets[1:])]) if len(offsets) > 1 else np.empty(0, np.int32)
    segments = np.column_stack((segment_starts + len(surface), segment_starts + len(surface) + 1))
    mesh = bpy.data.meshes.new("CAD managed display")
    try:
        mesh.vertices.add(len(positions))
        mesh.vertices.foreach_set("co", positions.astype(np.float32).ravel())
        mesh.loops.add(len(triangles))
        mesh.loops.foreach_set("vertex_index", triangles)
        mesh.polygons.add(triangle_count)
        mesh.polygons.foreach_set("loop_start", np.arange(triangle_count, dtype=np.int32) * 3)
        mesh.polygons.foreach_set("loop_total", np.full(triangle_count, 3, np.int32))
        mesh.edges.add(len(segments))
        mesh.edges.foreach_set("vertices", segments.ravel())
        mesh.update(calc_edges=True)
        mesh.attributes.new(FACE, "INT", "FACE").data.foreach_set("value", arrays["triangle_faces"].astype(np.int32))
        # Surface triangulation edges are not CAD edges. Only sampled loose
        # segments get an evaluated CAD-edge identity.
        mesh_edges = np.empty(len(mesh.edges) * 2, np.int32)
        mesh.edges.foreach_get("vertices", mesh_edges)
        endpoints = mesh_edges.reshape((-1, 2)) - len(surface)
        edge_ids = np.full(len(mesh.edges), -1, np.int32)
        if len(offsets) > 1:
            first_ids = np.searchsorted(offsets, endpoints[:, 0], side="right") - 1
            second_ids = np.searchsorted(offsets, endpoints[:, 1], side="right") - 1
            valid = ((endpoints >= 0).all(axis=1) & (endpoints < len(samples)).all(axis=1)
                     & (first_ids == second_ids))
            edge_ids[valid] = first_ids[valid]
        mesh.attributes.new(EDGE, "INT", "EDGE").data.foreach_set("value", edge_ids)
        vertex_ids = np.full(len(positions), -1, np.int32)
        vertex_ids[len(surface) + len(samples):] = np.arange(len(vertices), dtype=np.int32)
        mesh.attributes.new(VERTEX, "INT", "POINT").data.foreach_set("value", vertex_ids)
        token = _native.new_document_id()
        bvh = BVHTree.FromPolygons(surface * scale, triangles.reshape((-1, 3)), all_triangles=True)
        return Packet(arrays, token, bvh, mesh)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise


def _regular_mesh(display):
    import bmesh
    result = display.data.copy()
    result.use_fake_user = False
    try:
        for attribute in list(result.attributes):
            if attribute.name.startswith("blendercad_"):
                result.attributes.remove(attribute)
        mesh = bmesh.new()
        try:
            mesh.from_mesh(result)
            bmesh.ops.delete(mesh, geom=[vertex for vertex in mesh.verts if not vertex.link_faces], context="VERTS")
            mesh.to_mesh(result)
        finally:
            mesh.free()
        return result
    except Exception:
        bpy.data.meshes.remove(result)
        raise


def prepare(scene, document, convert_hosts=()):
    _validate_associations(scene, document.id)
    evaluator = runtime(scene).evaluator.fork()
    report = evaluator.evaluate(document)
    scale = unit_scale(scene)
    packets = {}
    conversions = []
    try:
        for body in report.bodies:
            if body.has_display:
                packets[body.body_id] = _make_packet(evaluator.tessellate(document, body.body_id), scale)
        for host in convert_hosts:
            display = display_for(scene, host)
            conversions.append((display, _regular_mesh(display)))
    except Exception:
        discard_prepared(Prepared(report, scale, packets, evaluator, conversions))
        raise
    return Prepared(report, scale, packets, evaluator, conversions)


def _remove_object(obj):
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def remove_host(scene, host, journal):
    display = display_for(scene, host)
    if display is not None:
        journal.retire(display)
    journal.retire(host)


class _Journal:
    """Rollback scene publication without relying on Blender undo on CANCELLED.

    Old objects/meshes are retained until success; deletion is first reversible
    unlinking. New geometry and conversion copies are prepared before mutation.
    """
    def __init__(self, scene):
        self.created = []
        self.records = {}
        self.retired = []
        self.obsolete_meshes = []
        self.view_layer = bpy.context.view_layer if bpy.context.scene == scene else None
        self.selected = tuple(bpy.context.selected_objects) if self.view_layer else ()
        self.active = self.view_layer.objects.active if self.view_layer else None

    def remember(self, obj):
        if obj in self.created or obj.as_pointer() in self.records:
            return
        attributes = ("name", "hide_viewport", "hide_render", "hide_select")
        saved = {key: getattr(obj, key) for key in attributes}
        for key in ("lock_location", "lock_rotation", "lock_scale", "color"):
            saved[key] = tuple(getattr(obj, key))
        saved.update(parent=obj.parent, matrix_parent_inverse=obj.matrix_parent_inverse.copy(), matrix_basis=obj.matrix_basis.copy())
        if obj.type == "MESH":
            saved["data"] = obj.data
        properties = {key: obj[key] for key in obj.keys() if key.startswith("blendercad_")}
        self.records[obj.as_pointer()] = obj, saved, properties

    def retire_mesh(self, mesh):
        if mesh not in self.obsolete_meshes:
            self.obsolete_meshes.append(mesh)

    def retire(self, obj):
        collections = tuple(obj.users_collection)
        self.retired.append((obj, collections))
        for collection in collections:
            collection.objects.unlink(obj)

    def rollback(self):
        for obj in reversed(self.created):
            # Free names before restoring existing objects and their names.
            bpy.data.objects.remove(obj, do_unlink=True)
        for obj, collections in reversed(self.retired):
            for collection in collections:
                if obj.name not in collection.objects:
                    collection.objects.link(obj)
        for obj, saved, properties in reversed(tuple(self.records.values())):
            for key, value in saved.items():
                setattr(obj, key, value)
            for key in list(obj.keys()):
                if key.startswith("blendercad_"):
                    del obj[key]
            for key, value in properties.items():
                obj[key] = value
        if self.view_layer:
            self.view_layer.update()
            for obj in self.view_layer.objects:
                obj.select_set(obj in self.selected)
            self.view_layer.objects.active = self.active

    def finish(self):
        # No fallible modelling work remains. Garbage collection after logical
        # commit must not turn an applied transaction into CANCELLED.
        for obj, _collections in self.retired:
            try:
                _remove_object(obj)
            except (ReferenceError, RuntimeError):
                pass
        for mesh in self.obsolete_meshes:
            try:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except (ReferenceError, RuntimeError):
                pass


def _material(prepared):
    name = "BlenderCAD default"
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
        prepared.new_materials.append(material)
        material.diffuse_color = (0.36, 0.48, 0.62, 1)
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = material.diffuse_color
        shader.inputs["Metallic"].default_value = 0.15
        shader.inputs["Roughness"].default_value = 0.35
    return material


def _publish(scene, document, prepared, journal):
    report, scale, packets = prepared.report, prepared.scale, prepared.packets
    for display, mesh in prepared.conversions:
        journal.remember(display)
        journal.retire_mesh(display.data)
        world = display.matrix_world.copy()
        display.data = mesh
        name = display.parent.name
        display.parent = None
        display.matrix_world = world
        display.name = name + " mesh"
        display.hide_select = False
        display.lock_location = display.lock_rotation = display.lock_scale = (False, False, False)
        for key in list(display.keys()):
            if key.startswith("blendercad_"):
                del display[key]
    existing = hosts(scene, document.id)
    if len({obj.get(BODY) for obj in existing}) != len(existing):
        raise ValueError("Duplicate Solid host identities; use CAD Duplicate Solid instead of native duplication")
    by_body = {obj[BODY]: obj for obj in existing}
    definitions = dict.fromkeys(feature.body_id for feature in document.features)
    names = {feature.body_id: feature.name for feature in document.features}
    evaluated = {body.body_id: body for body in report.bodies}
    diagnostics = {feature.feature_id: feature.diagnostic for feature in report.features}
    for body_id in definitions:
        host = by_body.get(body_id)
        if host is None:
            host = bpy.data.objects.new(names[body_id], None)
            journal.created.append(host)
            scene.collection.objects.link(host)
            host[ROLE], host[DOC], host[BODY] = "SOLID", document.id, body_id
            host.empty_display_type = "PLAIN_AXES"
            host.empty_display_size = 5 * scale
            host.lock_scale = (True, True, True)
        journal.remember(host)
        body = evaluated.get(body_id)
        display = display_for(scene, host)
        if display is not None:
            journal.remember(display)
        packet = packets.get(body_id)
        host["blendercad_state"] = body.state.name if body else "ROLLED_BACK"
        host["blendercad_diagnostic"] = diagnostics.get(body.feature_id, "") if body else "Before this body's first feature"
        if packet is not None:
            if display is None:
                display = bpy.data.objects.new("CAD display", packet.mesh)
                journal.created.append(display)
                scene.collection.objects.link(display)
                display.parent = host
                display[ROLE], display[DOC], display[BODY] = "DISPLAY", document.id, body_id
                packet.mesh.materials.append(_material(prepared))
            else:
                previous = display.data
                # Whole-body material only; never copy transient per-face slots
                # onto unrelated topology after a feature changes.
                packet.mesh.materials.append(previous.materials[0] if previous.materials and previous.materials[0] else _material(prepared))
                display.data = packet.mesh
                journal.retire_mesh(previous)
            display[TOKEN] = packet.token
            display["blendercad_revision"] = str(document.revision)
            display["blendercad_current"] = packet.arrays["current"]
            display.hide_select = True
            display.lock_location = display.lock_rotation = display.lock_scale = (True, True, True)
            display.color = (0.36, 0.48, 0.62, 1) if packet.arrays["current"] else (1, 0.08, 0.04, 1)
        elif display is not None:
            # Reopening a failed model cannot reconstruct process-local last
            # good B-reps. Keep the saved display, but never republish its picks.
            display.pop(TOKEN, None)
            display["blendercad_current"] = False
            display.color = (1, 0.08, 0.04, 1)
        if display is not None:
            display.hide_viewport = body is None
            display.hide_render = body is None
    for host in existing:
        if host[BODY] not in definitions:
            remove_host(scene, host, journal)
    state = Runtime(prepared.evaluator)
    state.document, state.report = document, report
    state.serialized, state.scale, state.packets = scene.blendercad_document_snapshot, scale, packets
    state.error = ""
    _runtimes[scene.as_pointer()] = state
    return report


def publish(scene, document, prepared):
    journal = _Journal(scene)
    previous = runtime(scene)
    try:
        report = _publish(scene, document, prepared, journal)
    except Exception:
        journal.rollback()
        restore_runtime(scene, previous)
        raise
    journal.finish()
    return report


def sync(scene):
    document = read_document(scene)
    prepared = prepare(scene, document)
    try:
        return publish(scene, document, prepared)
    except Exception:
        discard_prepared(prepared)
        raise


def current_packet(scene, host, token=None):
    state = runtime(scene)
    if (state.serialized != scene.blendercad_document_snapshot or state.scale != unit_scale(scene)
            or host.get(DOC) != (state.document.id if state.document else None)):
        raise ValueError("Stale display mapping; recompute the CAD document")
    packet = state.packets.get(host.get(BODY))
    display = display_for(scene, host)
    if (packet is None or not packet.arrays["current"] or display is None
            or display.data != packet.mesh or display.get(TOKEN) != packet.token
            or (token is not None and token != packet.token)):
        raise ValueError("Stale display mapping; recompute the CAD document")
    return packet


def resolve(scene, host, kind, index, token, point_mm=None, normal=None):
    packet = current_packet(scene, host, token)
    arrays = packet.arrays
    counts = {"FACE": arrays["face_count"], "EDGE": len(arrays["edge_offsets"]) - 1,
              "VERTEX": len(arrays["cad_vertices"]) // 3}
    if kind not in counts or isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < counts[kind]:
        raise ValueError("Invalid evaluated CAD topology index")
    return Pick(arrays["document_id"], arrays["body_id"], arrays["feature_id"], arrays["revision"], token, kind, index, point_mm, normal)


def persistent_target(scene, host, pick):
    if pick is None or pick.body_id != host.get(BODY) or pick.document_id != host.get(DOC):
        raise ValueError("Select a CAD face, edge or vertex on the active Solid first")
    current_packet(scene, host, pick.token)
    state = runtime(scene)
    return state.evaluator.capture_reference(state.document, pick.body_id,
                                            getattr(_native.TopologyKind, pick.kind), pick.index)


def pick_ray(scene, origin, direction, depsgraph=None):
    from .selection import pick_ray as exact_pick
    return exact_pick(scene, origin, direction, depsgraph)


def pick_key(pick):
    return pick.document_id, pick.body_id, pick.token, pick.kind, pick.index


def visible_selection(scene):
    """Yield only live, selected, visible geometry for the overlay adapter."""
    if _native is None:
        return
    state = runtime(scene)
    if state.document is None:
        return
    by_body = {host.get(BODY): host for host in hosts(scene, state.document.id)}
    for pick in state.selected_picks:
        host = by_body.get(pick.body_id)
        if host is None or not host.select_get() or not host.visible_get():
            continue
        try:
            packet = current_packet(scene, host, pick.token)
            from .selection import _rigid
            _rigid(host)
        except ValueError:
            continue
        display = display_for(scene, host)
        if display is None or not display.visible_get():
            continue
        yield host, pick, packet


def measure_pick(scene, host, pick):
    from .selection import _rigid
    _rigid(host)
    if pick.body_id != host.get(BODY) or pick.document_id != host.get(DOC):
        raise ValueError("CAD selection belongs to another Solid")
    current_packet(scene, host, pick.token)
    state = runtime(scene)
    return state.evaluator.measure_element(state.document, pick.body_id,
                                          _native.TopologyElement(getattr(_native.TopologyKind, pick.kind), pick.index))


def current_measure(scene, host):
    state = runtime(scene)
    pick, measure = state.last_pick, state.last_measure
    if pick is None or measure is None or pick.body_id != host.get(BODY) or not host.select_get():
        return None
    try:
        current_packet(scene, host, pick.token)
        from .selection import _rigid
        _rigid(host)
    except ValueError:
        return None
    if measure.element.kind.name != pick.kind or measure.element.index != pick.index:
        return None
    return measure


def apply_pick(context, host, pick, extend=False):
    """Select CAD subelements without altering design definitions or display."""
    from .capabilities import require_capability
    require_capability([host], "pick")
    scene = context.scene
    measure = measure_pick(scene, host, pick)
    state = runtime(scene)
    previous = [item for _host, item, _packet in visible_selection(scene)]
    key = pick_key(pick)
    if not extend:
        selected = [pick]
        for obj in context.selected_objects:
            obj.select_set(False)
    elif any(pick_key(item) == key for item in previous):
        selected = [item for item in previous if pick_key(item) != key]
    else:
        selected = previous + [pick]
    state.selected_picks = selected
    host.select_set(any(item.body_id == pick.body_id for item in selected))
    context.view_layer.objects.active = host
    if any(pick_key(item) == key for item in selected):
        state.last_pick, state.last_measure = pick, measure
    else:
        remaining = [item for item in selected if item.body_id == pick.body_id]
        state.last_pick = remaining[-1] if remaining else None
        state.last_measure = measure_pick(scene, host, state.last_pick) if state.last_pick else None
    state.reference_error = ""
    if state.last_pick:
        try:
            persistent_target(scene, host, state.last_pick)
        except ValueError as error:
            state.reference_error = str(error)
    for area in context.screen.areas if context.screen else ():
        if area.type == "VIEW_3D":
            area.tag_redraw()


@persistent
def rebuild_after_history(_unused):
    _runtimes.clear()  # No native handles or pick indices survive load/undo.
    if _native is None:
        return
    for scene in bpy.data.scenes:
        if scene.blendercad_document_snapshot:
            try:
                sync(scene)
            except Exception as error:
                runtime(scene).error = str(error)


def register():
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        handlers.append(rebuild_after_history)
    rebuild_after_history(None)


def unregister():
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if rebuild_after_history in handlers:
            handlers.remove(rebuild_after_history)
    _runtimes.clear()
