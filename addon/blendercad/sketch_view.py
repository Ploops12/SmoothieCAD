"""Disposable sketch-edit projection, screen picking and viewport overlays.

The editor owns its detached definition and solve lifecycle. This module only
reads its last-valid numeric sketch and current constraint definitions; it never
creates Blender objects or changes parameters, constraints or saved geometry.
Circle samples are presentation/picking candidates, not CAD measurements.
"""

import math

from mathutils import Vector

from . import _native as n
from .selection import _clip_segment, _project, _view_ray

last_error = ""
draw_count = 0
_draw_errors = {"3D": "", "2D": ""}
_shaders = {}
_CIRCLE_SEGMENTS = 96
_LABEL_SIZE = 12


def _vector3(value):
    return Vector((value.x, value.y, value.z))


def _uv(value):
    result = (float(value[0]), float(value[1]))
    if not all(math.isfinite(item) for item in result):
        raise ValueError("Sketch coordinates must be finite")
    return result


def _scale(value):
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Sketch display scale must be finite and positive")
    return value


def world_point(plane, uv, placement, scale):
    """Map part-local millimetres on a sketch plane into Blender world units."""
    u, v = _uv(uv)
    point = _vector3(plane.origin) + _vector3(plane.x_axis) * u + _vector3(plane.y_axis) * v
    result = placement @ (point * _scale(scale))
    if not all(math.isfinite(item) for item in result):
        raise ValueError("Sketch placement produced a nonfinite point")
    return result


def plane_point(region, view, mouse, plane, placement, scale):
    """Intersect a finite clipped viewport ray with the editing plane.

    The Gram solve also avoids assuming that a supplied display transform is
    exactly orthonormal. Capability checks for Solid placement remain upstream.
    """
    scale = _scale(scale)
    origin, direction, limit = _view_ray(region, view, mouse)
    frame = placement.to_3x3()
    x_axis, y_axis = frame @ _vector3(plane.x_axis), frame @ _vector3(plane.y_axis)
    normal = x_axis.cross(y_axis)
    if normal.length_squared == 0 or not all(math.isfinite(item) for item in normal):
        raise ValueError("Sketch plane has a degenerate display frame")
    normal.normalize()
    denominator = direction.dot(normal)
    if abs(denominator) <= 1e-10:
        return None
    plane_origin = world_point(plane, (0, 0), placement, scale)
    distance = (plane_origin - origin).dot(normal) / denominator
    if not math.isfinite(distance) or distance < 0 or distance > limit:
        return None
    relative = (origin + direction * distance - plane_origin) / scale
    xx, xy, yy = x_axis.dot(x_axis), x_axis.dot(y_axis), y_axis.dot(y_axis)
    determinant = xx * yy - xy * xy
    if not math.isfinite(determinant) or determinant <= 0:
        raise ValueError("Sketch plane has a degenerate display frame")
    rx, ry = relative.dot(x_axis), relative.dot(y_axis)
    return _uv(((rx * yy - ry * xy) / determinant, (ry * xx - rx * xy) / determinant))


def _ref(entity="", element=None):
    if n is None:
        raise ValueError("Native module is not installed")
    return n.SketchReference(entity, n.SketchElement.ORIGIN if element is None else element)


def _key(reference):
    return reference.entity, reference.element.name


def _curves(problem):
    for entity in problem.entities:
        geometry = entity.geometry
        if isinstance(geometry, n.Line2):
            points = [(geometry.start.x, geometry.start.y), (geometry.end.x, geometry.end.y)]
        else:
            points = [(geometry.center.x + geometry.radius * math.cos(index * math.tau / _CIRCLE_SEGMENTS),
                       geometry.center.y + geometry.radius * math.sin(index * math.tau / _CIRCLE_SEGMENTS))
                      for index in range(_CIRCLE_SEGMENTS + 1)]
        yield entity.id, points


def _points(problem):
    for entity in problem.entities:
        geometry = entity.geometry
        if isinstance(geometry, n.Line2):
            yield _ref(entity.id, n.SketchElement.LINE_START), (geometry.start.x, geometry.start.y)
            yield _ref(entity.id, n.SketchElement.LINE_END), (geometry.end.x, geometry.end.y)
        else:
            yield _ref(entity.id, n.SketchElement.CIRCLE_CENTER), (geometry.center.x, geometry.center.y)
    yield _ref(), (0, 0)


def _target_point(reference, entities):
    if not reference.entity:
        return (0, 0) if reference.element == n.SketchElement.ORIGIN else None
    geometry = entities.get(reference.entity)
    if geometry is None:
        return None
    if isinstance(geometry, n.Line2):
        if reference.element == n.SketchElement.LINE_START:
            return geometry.start.x, geometry.start.y
        if reference.element == n.SketchElement.LINE_END:
            return geometry.end.x, geometry.end.y
    elif reference.element == n.SketchElement.CIRCLE_CENTER:
        return geometry.center.x, geometry.center.y
    return None


def _midpoint(first, last):
    return (first[0] + last[0]) / 2, (first[1] + last[1]) / 2


def _labels(problem, raw_problem=None):
    """Yield shared drawing/picking labels on existing last-valid geometry."""
    entities = {entity.id: entity.geometry for entity in problem.entities}
    raw = raw_problem if raw_problem is not None else problem
    names = {"LENGTH": "L", "RADIUS": "R", "DIAMETER": "Ø", "DISTANCE": "D",
             "DISTANCE_X": "ΔX", "DISTANCE_Y": "ΔY"}
    for constraint in raw.constraints:
        kind = constraint.kind.name
        if kind not in names:
            continue
        targets = constraint.targets
        offset = (9, 12)
        if kind == "LENGTH":
            geometry = entities.get(targets[0].entity)
            if not isinstance(geometry, n.Line2):
                continue
            anchor = _midpoint((geometry.start.x, geometry.start.y), (geometry.end.x, geometry.end.y))
        elif kind in {"RADIUS", "DIAMETER"}:
            geometry = entities.get(targets[0].entity)
            if not isinstance(geometry, n.Circle2):
                continue
            angle = math.pi / 4 if kind == "RADIUS" else math.pi / 2
            anchor = (geometry.center.x + geometry.radius * math.cos(angle),
                      geometry.center.y + geometry.radius * math.sin(angle))
            if kind == "DIAMETER":
                offset = (9, -17)
        else:
            first, last = (_target_point(target, entities) for target in targets)
            if first is None or last is None:
                continue
            anchor = _midpoint(first, last)
            if kind == "DISTANCE_X":
                offset = (9, -20)
            elif kind == "DISTANCE_Y":
                offset = (9, 17)
        value = f"= {constraint.parameter}" if constraint.parameter is not None else f"{constraint.value:.6g} mm"
        yield constraint.id, anchor, offset, f"{names[kind]} {value}"


def _project_uv(problem, uv, placement, scale, region, view):
    result = _project(region, view, world_point(problem.plane, uv, placement, scale))
    return result[0] if result is not None else None


def _screen_labels(problem, raw_problem, placement, scale, region, view):
    for identifier, anchor, offset, text in _labels(problem, raw_problem):
        projected = _project_uv(problem, anchor, placement, scale, region, view)
        if projected is not None:
            yield identifier, projected + Vector(offset), text


def _segment_distance(mouse, first, last):
    segment = last - first
    factor = 0 if segment.length_squared == 0 else max(0, min(1, (mouse - first).dot(segment) / segment.length_squared))
    return (mouse - (first + segment * factor)).length


def hit(problem, placement, scale, region, view, mouse, raw_problem=None, points_only=False, radius_px=10):
    """Pick disposable sketch targets; returned references still use UUIDs."""
    if not math.isfinite(radius_px) or radius_px <= 0:
        raise ValueError("Sketch picking radius must be finite and positive")
    mouse = Vector(_uv(mouse))
    best = None
    distance = radius_px
    for reference, uv in _points(problem):
        projected = _project_uv(problem, uv, placement, scale, region, view)
        if projected is None:
            continue
        candidate = (mouse - projected).length
        if candidate <= distance and (best is None or candidate < distance):
            distance, best = candidate, ("POINT", reference, uv)
    if best is not None or points_only:
        return best
    # Labels use the same offset/extent convention as draw_2d. A label is a
    # viewport control, not a projected CAD face or an independently saved node.
    for identifier, location, text in _screen_labels(problem, raw_problem, placement, scale, region, view):
        width = max(14, len(text) * _LABEL_SIZE * 0.65)
        dx = max(location.x - mouse.x, 0, mouse.x - location.x - width)
        dy = max(location.y - 3 - mouse.y, 0, mouse.y - location.y - _LABEL_SIZE)
        candidate = math.hypot(dx, dy)
        if candidate <= distance and (best is None or candidate < distance):
            distance, best = candidate, ("DIMENSION", identifier, None)
    if best is not None:
        return best
    for identifier, points in _curves(problem):
        for first, last in zip(points, points[1:]):
            clipped = _clip_segment(view, world_point(problem.plane, first, placement, scale),
                                    world_point(problem.plane, last, placement, scale))
            if clipped is None:
                continue
            projected = [_project(region, view, point) for point in clipped]
            if any(point is None for point in projected):
                continue
            candidate = _segment_distance(mouse, projected[0][0], projected[1][0])
            if candidate <= distance and (best is None or candidate < distance):
                distance, best = candidate, ("GEOMETRY", _ref(identifier, n.SketchElement.GEOMETRY), None)
    return best


def _shader(kind):
    import gpu
    if kind not in _shaders:
        _shaders[kind] = gpu.shader.from_builtin("POINT_UNIFORM_COLOR" if kind == "POINTS" else "POLYLINE_UNIFORM_COLOR")
    return _shaders[kind]


def _record_error(kind, error=None):
    global last_error
    _draw_errors[kind] = "" if error is None else f"{kind}: {type(error).__name__}: {error}"
    last_error = "; ".join(message for message in _draw_errors.values() if message)


def draw_3d(session, placement, scale, selected=(), selected_constraint="", rubber=()):
    """Draw an explicit sketch-edit overlay; no geometry is published to Blender."""
    import gpu
    from gpu_extras.batch import batch_for_shader

    global draw_count
    saved = None
    try:
        problem = session.display_problem
        selected_keys = {_key(reference) for reference in selected}
        if selected_constraint:
            raw = session.document.get_sketch(session.sketch_id)
            constraint = next((item for item in raw.constraints if item.id == selected_constraint), None)
            if constraint is not None:
                selected_keys.update(_key(reference) for reference in constraint.targets)
        base, highlighted = [], []
        for identifier, points in _curves(problem):
            output = highlighted if (identifier, "GEOMETRY") in selected_keys else base
            world = [world_point(problem.plane, point, placement, scale) for point in points]
            output.extend(point for pair in zip(world, world[1:]) for point in pair)
        points, selected_points = [], []
        for reference, uv in _points(problem):
            output = selected_points if _key(reference) in selected_keys else points
            output.append(world_point(problem.plane, uv, placement, scale))
        preview = []
        for curve in rubber:
            world = [world_point(problem.plane, point, placement, scale) for point in curve]
            preview.extend(point for pair in zip(world, world[1:]) for point in pair)
        saved = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get(), gpu.state.line_width_get())
        gpu.state.blend_set("ALPHA")
        # Editing-plane geometry is intentionally readable above the committed
        # Solid display. This is not the CAD subelement occlusion/picking path.
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(False)
        base_color = (1, 0.23, 0.17, 1) if session.error else (0.15, 0.8, 1, 1)
        batches = (("LINES", base, base_color, 2), ("LINES", highlighted, (1, 0.6, 0.1, 1), 3),
                   ("LINES", preview, (0.9, 0.9, 0.4, 0.95), 2),
                   ("POINTS", points, base_color, 6), ("POINTS", selected_points, (1, 0.6, 0.1, 1), 9))
        for primitive, positions, color, size in batches:
            if not positions:
                continue
            shader = _shader(primitive)
            shader.bind()
            shader.uniform_float("color", color)
            if primitive == "POINTS":
                shader.uniform_float("size", size)
            else:
                shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
                shader.uniform_float("lineWidth", size)
            batch_for_shader(shader, primitive, {"pos": positions}).draw(shader)
        draw_count += 1
        _record_error("3D")
    except Exception as error:
        _record_error("3D", error)
    finally:
        if saved is not None:
            try:
                blend, depth, mask, width = saved
                gpu.state.depth_mask_set(mask)
                gpu.state.depth_test_set(depth)
                gpu.state.blend_set(blend)
                gpu.state.line_width_set(width)
            except Exception as error:
                _record_error("3D", error)


def draw_2d(session, placement, scale, region, view, selected_constraint="", lines=()):
    """Draw dimension controls and editor-supplied status/help text."""
    import blf

    global draw_count
    try:
        problem = session.display_problem
        raw = session.document.get_sketch(session.sketch_id)
        blf.size(0, _LABEL_SIZE)
        for identifier, position, text in _screen_labels(problem, raw, placement, scale, region, view):
            if identifier == selected_constraint:
                color = (1, 0.65, 0.15, 1)
            elif session.error:
                color = (1, 0.4, 0.3, 1)
            else:
                color = (0.85, 0.96, 1, 1)
            blf.color(0, *color)
            blf.position(0, position.x, position.y, 0)
            blf.draw(0, text)
        blf.color(0, 1, 0.92, 0.65, 1)
        for index, text in enumerate(lines):
            # Keep editor help away from Blender's top-left view/collection
            # overlays; the bottom margin is shared with modal-style prompts.
            blf.position(0, 18, 18 + (len(lines) - 1 - index) * 18, 0)
            blf.draw(0, str(text))
        draw_count += 1
        _record_error("2D")
    except Exception as error:
        _record_error("2D", error)


def clear():
    """Release disposable shader references from the editor's unregister path."""
    global last_error, draw_count
    _shaders.clear()
    _draw_errors.update({"3D": "", "2D": ""})
    last_error = ""
    draw_count = 0
