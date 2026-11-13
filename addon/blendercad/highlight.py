"""Disposable viewport overlays for current CAD subelement selections.

The presentation service supplies only visible/current pick tokens. Face indices
address CAD faces, not triangles, so a vectorized face-map mask selects all of
the face's display triangles. Edge ranges include empty degenerate entries;
they must not be interpreted as implicit links to adjacent sampled edges.

Coordinates are staged once in packet-local Blender units, then transformed by
the Solid host's placement during each draw. No overlay is a scene object or a
design edit. Analytic geometry is not approximated again here: edge curves use
the evaluated display samples, and the precise picked point is not baked into
the whole-element highlight. Replacing a packet invalidates its GPU batches.

GPU objects are created only by an interactive draw callback. Depth-tested
translucent overlays do not write depth and are not x-ray selection. The point
shader's size uniform avoids a global point-size state change for which Blender
does not expose a matching getter.
"""

import weakref

import bpy
import numpy as np

from . import presentation as p

last_error = ""
draw_count = 0

_handler = None
_shaders = {}
_packet_owners = weakref.WeakValueDictionary()

_SHADER_NAMES = {
    "FACE": "UNIFORM_COLOR",
    "EDGE": "POLYLINE_UNIFORM_COLOR",
    "VERTEX": "POINT_UNIFORM_COLOR",
}
_COLORS = {
    "FACE": (1.0, 0.48, 0.06, 0.30),
    "EDGE": (1.0, 0.55, 0.05, 1.0),
    "VERTEX": (1.0, 0.72, 0.10, 1.0),
}


def _selection_geometry(arrays, kind, index, scale):
    """Return a bulk batch specification; intentionally independent of GPU state."""
    if kind not in _SHADER_NAMES:
        raise ValueError("Unsupported CAD highlight topology kind")
    if isinstance(index, bool) or not isinstance(index, (int, np.integer)) or index < 0:
        raise ValueError("Invalid CAD highlight topology index")
    index = int(index)
    if kind == "FACE":
        if index >= arrays["face_count"]:
            raise ValueError("CAD face highlight index out of range")
        triangles = np.asarray(arrays["triangles"], dtype=np.uint32).reshape((-1, 3))
        faces = np.asarray(arrays["triangle_faces"]).reshape(-1)
        if len(faces) != len(triangles):
            raise ValueError("CAD display triangle/face mapping length mismatch")
        selected = triangles[faces == index]
        if not len(selected):
            return None
        positions = np.asarray(arrays["positions"], dtype=np.float64).reshape((-1, 3))
        return "TRIS", np.asarray(positions * scale, dtype=np.float32), selected
    if kind == "EDGE":
        offsets = np.asarray(arrays["edge_offsets"], dtype=np.int64).reshape(-1)
        if index >= len(offsets) - 1:
            raise ValueError("CAD edge highlight index out of range")
        positions = np.asarray(arrays["edge_positions"], dtype=np.float64).reshape((-1, 3))
        start, end = offsets[index:index + 2]
        if start < 0 or end < start or end > len(positions):
            raise ValueError("CAD display edge sample range is invalid")
        if end - start < 2:
            return None
        return "LINE_STRIP", np.asarray(positions[start:end] * scale, dtype=np.float32), None
    positions = np.asarray(arrays["cad_vertices"], dtype=np.float64).reshape((-1, 3))
    if index >= len(positions):
        raise ValueError("CAD vertex highlight index out of range")
    return "POINTS", np.asarray(positions[index:index + 1] * scale, dtype=np.float32), None


def _shader(kind):
    import gpu

    if kind not in _shaders:
        _shaders[kind] = gpu.shader.from_builtin(_SHADER_NAMES[kind])
    return _shaders[kind]


def _batch(packet, pick, scale):
    from gpu_extras.batch import batch_for_shader

    key = pick.kind, pick.index
    if key not in packet.overlays:
        geometry = _selection_geometry(packet.arrays, pick.kind, pick.index, scale)
        if geometry is None:
            packet.overlays[key] = None
        else:
            primitive, positions, indices = geometry
            packet.overlays[key] = batch_for_shader(_shader(pick.kind), primitive,
                                                    {"pos": positions}, indices=indices)
        # A weak owner reference lets unregister release live batches without
        # extending the lifetime of an obsolete evaluator/display packet.
        _packet_owners[id(packet)] = packet
    return packet.overlays[key]


def _draw():
    import gpu

    global last_error, draw_count
    context = bpy.context
    if (context.scene is None or context.area is None or context.area.type != "VIEW_3D"
            or context.region is None or context.region.type != "WINDOW"):
        return
    if not context.space_data.overlay.show_overlays:
        return
    saved = None
    drew = False
    try:
        selection = tuple(p.visible_selection(context.scene))
        if not selection:
            return
        scale = p.unit_scale(context.scene)
        saved = (gpu.state.blend_get(), gpu.state.depth_test_get(),
                 gpu.state.depth_mask_get(), gpu.state.line_width_get())
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.depth_mask_set(False)
        for host, pick, packet in selection:
            batch = _batch(packet, pick, scale)
            if batch is None:
                continue
            shader = _shader(pick.kind)
            shader.bind()
            shader.uniform_float("color", _COLORS[pick.kind])
            if pick.kind == "EDGE":
                shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
                shader.uniform_float("lineWidth", 3.0)
            elif pick.kind == "VERTEX":
                shader.uniform_float("size", 9.0)
            with gpu.matrix.push_pop():
                gpu.matrix.multiply_matrix(host.matrix_world)
                batch.draw(shader)
            draw_count += 1
            drew = True
        if drew:
            last_error = ""
    except Exception as error:
        # Draw handlers must not leak exceptions or repeat console messages on
        # every redraw. The integration UI/test can inspect this diagnostic.
        last_error = f"{type(error).__name__}: {error}"
    finally:
        if saved is not None:
            blend, depth_test, depth_mask, line_width = saved
            try:
                gpu.state.depth_mask_set(depth_mask)
                gpu.state.depth_test_set(depth_test)
                gpu.state.blend_set(blend)
                gpu.state.line_width_set(line_width)
            except Exception as error:
                last_error = f"GPU state restoration failed: {type(error).__name__}: {error}"


def register():
    global _handler, last_error, draw_count
    if _handler is not None or bpy.app.background:
        return
    last_error = ""
    draw_count = 0
    _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def unregister():
    global _handler, last_error, draw_count
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, "WINDOW")
        _handler = None
    for packet in tuple(_packet_owners.values()):
        packet.overlays.clear()
    _packet_owners.clear()
    _shaders.clear()
    last_error = ""
    draw_count = 0
