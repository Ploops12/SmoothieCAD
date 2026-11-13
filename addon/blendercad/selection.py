"""Current CAD selection: screen candidates, exact B-rep queries and occlusion.

Display arrays find candidates only. Snapped coordinates, areas, lengths and
curved-face intersections come from the native evaluated geometry. Indices and
screen hits stay disposable; persistent_target performs the naming translation.
"""

import math

from bpy_extras import view3d_utils
from mathutils import Vector

from . import _native as n
from . import presentation as p
from .capabilities import INCOMPATIBLE_MESSAGE


def _vec3(value):
    return n.Vector3(float(value[0]), float(value[1]), float(value[2]))


def _tuple3(value):
    return value.x, value.y, value.z


def _ray(origin, direction):
    origin, direction = Vector(origin), Vector(direction)
    if not all(math.isfinite(value) for value in (*origin, *direction)) or direction.length_squared == 0:
        raise ValueError("Selection ray must have finite coordinates and a nonzero direction")
    return origin, direction.normalized()


def _rigid(host):
    rotation = host.matrix_world.to_3x3()
    columns = [rotation.col[index] for index in range(3)]
    if (not all(math.isfinite(value) for row in host.matrix_world for value in row)
            or any(abs(column.length_squared - 1) > 1e-5 for column in columns)
            or any(abs(columns[a].dot(columns[b])) > 1e-5 for a, b in ((0, 1), (0, 2), (1, 2)))
            or abs(rotation.determinant() - 1) > 1e-5):
        raise ValueError(INCOMPATIBLE_MESSAGE)
    return host.matrix_world.inverted()


def _bodies(scene):
    state = p.runtime(scene)
    if state.document is None:
        return
    for host in p.hosts(scene, state.document.id):
        if not p.visible_in_view(host):
            continue
        try:
            packet = p.current_packet(scene, host)
            inverse = _rigid(host)
        except ValueError:
            continue
        display = p.display_for(scene, host)
        if display is not None and p.visible_in_view(display):
            yield host, packet, inverse


def _face_hit(scene, origin, direction, max_distance=math.inf):
    state = p.runtime(scene)
    scale = p.unit_scale(scene)
    best, distance = None, math.inf
    for host, packet, inverse in _bodies(scene):
        local_origin = (inverse @ origin) / scale
        local_direction = (inverse.to_3x3() @ direction).normalized()
        limit = min(1e12, max_distance / scale)
        hit = state.evaluator.ray_cast(state.document, host[p.BODY], _vec3(local_origin), _vec3(local_direction), limit)
        if hit is None:
            continue
        world_point = host.matrix_world @ (Vector(_tuple3(hit.point_mm)) * scale)
        hit_distance = (world_point - origin).length
        if hit_distance < distance:
            distance = hit_distance
            best = host, p.resolve(scene, host, "FACE", hit.face_index, packet.token,
                                   _tuple3(hit.point_mm), _tuple3(hit.normal) if hit.normal else None)
    return best, distance


def _ordinary_occluder(scene, origin, direction, distance, depsgraph):
    if depsgraph is None:
        return False
    hit, point, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, origin, direction)
    tolerance = p.runtime(scene).document.geometric_tolerance_mm * p.unit_scale(scene)
    if not hit or (point - origin).length >= distance - tolerance:
        return False
    owner = p.host_for(obj.original)
    if owner is not None:
        try:
            p.current_packet(scene, owner)
            _rigid(owner)
            return False  # Current CAD bodies were tested analytically above.
        except ValueError:
            pass  # An unavailable but visible last-good display still occludes.
    return True


def pick_ray(scene, origin, direction, depsgraph=None):
    origin, direction = _ray(origin, direction)
    best, distance = _face_hit(scene, origin, direction)
    if best and _ordinary_occluder(scene, origin, direction, distance, depsgraph):
        return None
    return best


def _project(region, view, point):
    clip = view.perspective_matrix @ point.to_4d()
    if clip.w == 0 or (view.is_perspective and clip.w <= 0) or not -1.000001 <= clip.z / clip.w <= 1.000001:
        return None
    location = view3d_utils.location_3d_to_region_2d(region, view, point)
    return (location, clip.w) if location is not None else None


def _view_ray(region, view, position):
    """Use the viewport's near/far segment, including orthographic clipping."""
    if region.width <= 0 or region.height <= 0:
        raise ValueError("CAD picking requires a nonempty viewport")
    inverse = view.perspective_matrix.inverted()
    x, y = 2 * position[0] / region.width - 1, 2 * position[1] / region.height - 1
    first, last = inverse @ Vector((x, y, -1, 1)), inverse @ Vector((x, y, 1, 1))
    if first.w == 0 or last.w == 0:
        raise ValueError("Viewport clipping planes cannot define a finite picking ray")
    origin, far = first.xyz / first.w, last.xyz / last.w
    direction = far - origin
    distance = direction.length
    if distance == 0:
        raise ValueError("Viewport clipping planes coincide")
    return origin, direction / distance, distance


def _clip_segment(view, first, last):
    """Clip in homogeneous space before projection (including near plane)."""
    a, b = view.perspective_matrix @ first.to_4d(), view.perspective_matrix @ last.to_4d()
    low, high = 0.0, 1.0
    for axis in range(3):
        for sign in (-1, 1):
            start, end = a.w + sign * a[axis], b.w + sign * b[axis]
            if start < 0 and end < 0:
                return None
            if start < 0:
                low = max(low, start / (start - end))
            elif end < 0:
                high = min(high, start / (start - end))
            if low > high:
                return None
    return first.lerp(last, low), first.lerp(last, high)


def _visible_point(scene, region, view, point, depsgraph):
    projected = _project(region, view, point)
    if projected is None:
        return False
    origin, _direction, _limit = _view_ray(region, view, projected[0])
    direction = point - origin
    distance = direction.length
    if distance == 0:
        return True
    direction.normalize()
    _hit, front = _face_hit(scene, origin, direction)
    tolerance = p.runtime(scene).document.geometric_tolerance_mm * p.unit_scale(scene)
    return front >= distance - tolerance and not _ordinary_occluder(scene, origin, direction, distance, depsgraph)


def pick_screen(scene, region, view, position, mode="FACE", radius_px=10, depsgraph=None):
    if mode not in {"FACE", "EDGE", "VERTEX"}:
        raise ValueError("Unknown CAD selection mode")
    if not math.isfinite(radius_px) or radius_px <= 0:
        raise ValueError("CAD selection radius must be finite and positive")
    position = Vector(position)
    origin, direction, limit = _view_ray(region, view, position)
    if mode == "FACE":
        hit, distance = _face_hit(scene, origin, direction, limit)
        if hit and _ordinary_occluder(scene, origin, direction, distance, depsgraph):
            return None
        if hit:
            host, pick = hit
            point = host.matrix_world @ (Vector(pick.point_mm) * p.unit_scale(scene))
            if _project(region, view, point) is not None:
                return hit
        return None
    state = p.runtime(scene)
    scale = p.unit_scale(scene)
    candidates = []
    for host, packet, inverse in _bodies(scene):
        arrays = packet.arrays
        if mode == "VERTEX":
            coordinates = arrays["cad_vertices"].reshape((-1, 3))
            raw = [(index, host.matrix_world @ (Vector(point) * scale)) for index, point in enumerate(coordinates)]
        else:
            coordinates = arrays["edge_positions"].reshape((-1, 3))
            raw = []
            for index, (start, end) in enumerate(zip(arrays["edge_offsets"][:-1], arrays["edge_offsets"][1:])):
                world = [host.matrix_world @ (Vector(point) * scale) for point in coordinates[int(start):int(end)]]
                for first, last in zip(world, world[1:]):
                    clipped = _clip_segment(view, first, last)
                    if clipped is None:
                        continue
                    first, last = clipped
                    a, b = _project(region, view, first), _project(region, view, last)
                    if a is None or b is None:
                        continue
                    delta = b[0] - a[0]
                    if delta.length_squared:
                        alpha = max(0.0, min(1.0, (position - a[0]).dot(delta) / delta.length_squared))
                    else:
                        alpha = float((last - origin).length_squared < (first - origin).length_squared)
                    projected = a[0] + alpha * delta
                    score = (position - projected).length_squared
                    # Correct perspective screen interpolation before kernel
                    # projection; orthographic clip weights are equal.
                    denominator = (1 - alpha) * b[1] + alpha * a[1]
                    weight = alpha * a[1] / denominator if denominator else alpha
                    # One curved edge can project several front/back arcs onto
                    # the same pixels. Test every nearby segment, not just the
                    # first screen minimum, before rejecting the whole edge.
                    if score <= radius_px * radius_px:
                        raw.append((index, first.lerp(last, weight)))
        for index, approximate in raw:
            projected = _project(region, view, approximate)
            if projected is None or (position - projected[0]).length_squared > radius_px * radius_px:
                continue
            local = (inverse @ approximate) / scale
            element = n.TopologyElement(getattr(n.TopologyKind, mode), index)
            exact = state.evaluator.closest_point_element(state.document, host[p.BODY], element, _vec3(local))
            point = host.matrix_world @ (Vector(_tuple3(exact.point_mm)) * scale)
            projected = _project(region, view, point)
            if projected is None:
                continue
            score = (position - projected[0]).length_squared
            if score <= radius_px * radius_px and (point - origin).dot(direction) >= 0:
                candidates.append((score, (point - origin).length, host, packet, index, point, _tuple3(exact.point_mm)))
    candidates.sort(key=lambda item: item[:2])
    for _score, _depth, host, packet, index, world, local in candidates:
        if _visible_point(scene, region, view, world, depsgraph):
            return host, p.resolve(scene, host, mode, index, packet.token, local)
    return None
