"""Disposable feature-result ghosts and scalar-handle presentation.

The editor supplies already-evaluated bulk arrays. These X-ray overlays are
neither scene objects nor selectable CAD results, and are never serialized.
Each overlay owns its arrays/batches; replacing it releases the previous GPU
resources without a global cache retaining obsolete candidate geometry.
"""

from contextlib import contextmanager
import math

import numpy as np
from mathutils import Vector


def _scale(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Feature display scale must be finite and positive")
    return value


def _vector(value):
    result = Vector(value)
    if len(result) != 3 or not all(math.isfinite(item) for item in result):
        raise ValueError("Feature handle requires a finite world point or axis")
    return result


def project(region, region_3d, point):
    """Return region-local pixels, or None for a point behind the camera."""
    from bpy_extras import view3d_utils

    value = view3d_utils.location_3d_to_region_2d(region, region_3d, _vector(point))
    if value is None or not all(math.isfinite(item) for item in value):
        return None
    return value


def drag_projection(region, region_3d, anchor_world, direction_world, scale):
    """Map scalar millimetres to screen displacement at drag start.

    Use a local finite difference sized in screen pixels, not a fixed world
    length that can exceed a tiny part's view. An axis viewed end-on has no
    stable screen derivative; horizontal view-plane motion then adjusts the
    scalar at the anchor's units-per-pixel scale instead.
    """
    from bpy_extras import view3d_utils

    scale = _scale(scale)
    anchor = _vector(anchor_world)
    axis = _vector(direction_world)
    if axis.length_squared == 0:
        raise ValueError("Feature handle axis is degenerate")
    axis.normalize()
    screen = project(region, region_3d, anchor)
    if screen is None:
        raise ValueError("Feature handle is behind the viewport")
    first = view3d_utils.region_2d_to_location_3d(region, region_3d, screen, anchor)
    next_point = view3d_utils.region_2d_to_location_3d(region, region_3d,
        screen + Vector((1, 0)), anchor)
    world_per_pixel = (next_point - first).length
    if not math.isfinite(world_per_pixel) or world_per_pixel <= 0:
        raise ValueError("Feature handle has no finite viewport scale")
    step_world = world_per_pixel * 16
    next_screen = project(region, region_3d, anchor + axis * step_world)
    if next_screen is not None:
        delta = next_screen - screen
        if delta.length >= 0.5:
            return delta * (scale / step_world)
    return Vector((scale / world_per_pixel, 0))


@contextmanager
def _xray():
    import gpu

    saved = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(False)
        yield
    finally:
        blend, depth, mask = saved
        gpu.state.depth_mask_set(mask)
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


def _line_uniforms(shader, color, width):
    import gpu

    shader.bind()
    shader.uniform_float("color", color)
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", width)


class Overlay:
    """One immutable candidate display, allocated on the first actual draw."""

    def __init__(self, arrays, placement, scale):
        self.arrays = arrays
        self.placement = placement.copy()
        self.scale = _scale(scale)
        self._batches = None
        self.draw_count = 0

    def _prepare(self):
        import gpu
        from gpu_extras.batch import batch_for_shader

        # No kernel calls or per-triangle Python transfer: native arrays remain
        # bulk buffers and semantic edge ranges remain separate line segments.
        positions = np.asarray(self.arrays["positions"], dtype=np.float64).reshape((-1, 3)) * self.scale
        triangles = np.asarray(self.arrays["triangles"], dtype=np.int32).reshape((-1, 3))
        edge_positions = np.asarray(self.arrays["edge_positions"], dtype=np.float64).reshape((-1, 3)) * self.scale
        offsets = np.asarray(self.arrays["edge_offsets"], dtype=np.int64).reshape(-1)
        starts = [np.arange(start, end - 1, dtype=np.int32)
                  for start, end in zip(offsets[:-1], offsets[1:]) if end - start >= 2]
        segments = np.concatenate(starts) if starts else np.empty(0, dtype=np.int32)
        line_indices = np.column_stack((segments, segments + 1))
        surface_shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        edge_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        surface = (batch_for_shader(surface_shader, "TRIS", {"pos": positions.astype(np.float32)},
                                   indices=triangles) if len(triangles) else None)
        edges = (batch_for_shader(edge_shader, "LINES", {"pos": edge_positions.astype(np.float32)},
                                 indices=line_indices) if len(line_indices) else None)
        self._batches = surface_shader, surface, edge_shader, edges

    def draw(self, valid=True):
        import gpu

        if self._batches is None:
            self._prepare()
        surface_shader, surface, edge_shader, edges = self._batches
        fill = (0.03, 0.65, 1.0, 0.16) if valid else (1.0, 0.42, 0.03, 0.18)
        stroke = (0.05, 0.85, 1.0, 0.95) if valid else (1.0, 0.62, 0.08, 1.0)
        with _xray(), gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(self.placement)
            if surface is not None:
                surface_shader.bind()
                surface_shader.uniform_float("color", fill)
                surface.draw(surface_shader)
            if edges is not None:
                _line_uniforms(edge_shader, stroke, 2.0)
                edges.draw(edge_shader)
        self.draw_count += 1


def draw_handle(anchor_world, direction_world, value_mm, scale, valid=True):
    """Draw an axis stem and scalar endpoint; return the endpoint in world units."""
    import gpu
    from gpu_extras.batch import batch_for_shader

    anchor = _vector(anchor_world)
    axis = _vector(direction_world)
    value = float(value_mm)
    if axis.length_squared == 0 or not math.isfinite(value):
        raise ValueError("Feature handle requires a finite value and nonzero axis")
    tip = anchor + axis.normalized() * value * _scale(scale)
    color = (0.25, 0.95, 1.0, 1.0) if valid else (1.0, 0.62, 0.08, 1.0)
    line_shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    point_shader = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
    stem = batch_for_shader(line_shader, "LINES", {"pos": (anchor, tip)})
    base = batch_for_shader(point_shader, "POINTS", {"pos": (anchor,)})
    endpoint = batch_for_shader(point_shader, "POINTS", {"pos": (tip,)})
    with _xray():
        _line_uniforms(line_shader, color, 3.0)
        stem.draw(line_shader)
        point_shader.bind()
        point_shader.uniform_float("color", color)
        point_shader.uniform_float("size", 6.0)
        base.draw(point_shader)
        point_shader.uniform_float("size", 13.0)
        endpoint.draw(point_shader)
    return tip


def draw_text(region, lines, error=False):
    """Draw region-local status lines above the viewport, not in scene data."""
    import blf

    color = (1.0, 0.65, 0.12, 1.0) if error else (0.72, 0.95, 1.0, 1.0)
    blf.size(0, 13)
    blf.color(0, *color)
    # Leave room for Blender's tool header, navigation labels and left toolbar.
    x, y = 72, region.height - 138
    available = max(40, region.width - x - 32)
    rows = []
    for line in lines:
        for text in str(line).splitlines() or ("",):
            while text:
                count = len(text)
                while count > 1 and blf.dimensions(0, text[:count])[0] > available:
                    count -= 1
                split = text.rfind(" ", 0, count + 1) if count < len(text) else count
                count = split if split > 0 else count
                rows.append(text[:count])
                text = text[count:].lstrip()
    rows = rows[:max(0, int((y - 18) // 20))]
    if rows:
        width = max(blf.dimensions(0, row)[0] for row in rows)
        _screen_fill(((x - 10, y + 18), (x + width + 10, y + 18),
                      (x + width + 10, y - 20 * (len(rows) - 1) - 8),
                      (x - 10, y - 20 * (len(rows) - 1) - 8)), (0.02, 0.03, 0.04, 0.82))
    for row in rows:
        blf.position(0, x, y, 0)
        blf.draw(0, row)
        y -= 20


def _screen_fill(points, color):
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": [(x, y, 0) for x, y in points]})
    with _xray():
        shader.bind()
        shader.uniform_float("color", color)
        batch.draw(shader)


def draw_knob(point, valid=True):
    """A pixel-sized hit affordance independent of backend point-size state."""
    if point is None:
        return
    for radius, color in ((10, (0.02, 0.03, 0.04, 1)),
                          (7, (0.25, 0.95, 1, 1) if valid else (1, 0.62, 0.08, 1))):
        points = [(point.x + radius * math.cos(index * math.tau / 24),
                   point.y + radius * math.sin(index * math.tau / 24)) for index in range(24)]
        _screen_fill(points, color)
