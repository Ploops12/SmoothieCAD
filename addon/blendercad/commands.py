"""CAD commands shared by numeric UI and subsequent tools.

Commands mutate a detached native document, then evaluate and stage display
meshes before publishing the snapshot. Blender operators supply undo boundaries;
construction history remains independent of Blender's undo log.
"""

import copy

import bpy

from . import _native as n
from . import capabilities, presentation as p


def selected(context, operation):
    objects = list(context.selected_objects)
    capabilities.require_capability(objects, operation)
    hosts = p.selected_hosts(context)
    if not hosts or any(p.host_for(obj) is None for obj in objects):
        raise ValueError("Select only CAD Solid hosts for this command")
    document = p.read_document(context.scene)
    if any(host.get(p.DOC) != document.id for host in hosts):
        raise ValueError("Selected Solid does not belong to this scene's CAD document")
    return document, hosts


def commit(scene, document, convert_hosts=()):
    previous_runtime = p.runtime(scene)
    prepared = p.prepare(scene, document, convert_hosts)
    previous = scene.blendercad_document_snapshot
    try:
        scene.blendercad_document_snapshot = p.serialize(document)
        return p.publish(scene, document, prepared)
    except Exception:
        scene.blendercad_document_snapshot = previous
        p.discard_prepared(prepared)
        p.restore_runtime(scene, previous_runtime)
        raise


def _select(context, body_ids):
    document = p.read_document(context.scene)
    for obj in context.selected_objects:
        obj.select_set(False)
    for host in p.hosts(context.scene, document.id):
        if host[p.BODY] in body_ids:
            host.select_set(True)
            context.view_layer.objects.active = host


def rectangle_profile(width, height):
    if width <= 0 or height <= 0:
        raise ValueError("Rectangle dimensions must be positive")
    problem = n.SketchProblem()
    points = [(0, 0), (width, 0), (width, height), (0, height)]
    lines = [n.SketchEntity(n.Line2(n.Point2(*points[i]), n.Point2(*points[(i + 1) % 4]))) for i in range(4)]
    element = n.SketchElement
    kind = n.SketchConstraintKind
    ref = lambda i, part: n.SketchReference(lines[i].id, part)
    constraints = [n.SketchConstraint(kind.COINCIDENT, [ref(i, element.LINE_END), ref((i + 1) % 4, element.LINE_START)]) for i in range(4)]
    constraints += [n.SketchConstraint(kind.HORIZONTAL if i % 2 == 0 else kind.VERTICAL, [ref(i, element.GEOMETRY)]) for i in range(4)]
    constraints += [n.SketchConstraint(kind.LENGTH, [ref(0, element.GEOMETRY)], width),
                    n.SketchConstraint(kind.LENGTH, [ref(1, element.GEOMETRY)], height),
                    n.SketchConstraint(kind.COINCIDENT, [ref(0, element.LINE_START), n.SketchReference()])]
    problem.entities, problem.constraints = lines, constraints
    return problem


def add_rectangle(context, width, height, depth):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    if depth <= 0:
        raise ValueError("Extrusion depth must be positive")
    sketch = document.add_sketch("Rectangle", rectangle_profile(width, height))
    feature = document.add_extrusion("Rectangle extrusion", sketch, depth)
    body = document.get_feature(feature).body_id
    commit(context.scene, document)
    _select(context, [body])
    return feature


def set_depth(context, feature_id, depth):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    document.set_feature_depth(feature_id, depth)
    return commit(context.scene, document)


def set_parameter(context, name, value, unit=None, expression=None):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    if expression is not None:
        if unit is None:
            document.set_parameter_expression(name, expression)
        else:
            document.add_parameter_expression(name, expression, unit)
    elif unit is None:
        document.set_parameter_value(name, value)
    else:
        document.add_parameter(name, value, unit)
    return commit(context.scene, document)


def save_reference(context, name, reference_id=""):
    document, hosts = selected(context, "edit_definition")
    host = p.host_for(context.active_object)
    if host not in hosts:
        raise ValueError("Select the CAD Solid containing the picked region")
    state = p.runtime(context.scene)
    target = p.persistent_target(context.scene, host, state.last_pick)
    if reference_id:
        document.repair_reference(reference_id, target)
        result = reference_id
    else:
        result = document.add_reference(name, target)
    commit(context.scene, document)
    return result


def erase_reference(context, reference_id):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    document.erase_reference(reference_id)
    return commit(context.scene, document)


def _delete_definitions(document, bodies):
    roots = [feature.id for feature in document.features if feature.body_id in bodies and feature.input_id is None]
    if len(roots) != len(bodies):
        raise ValueError("Cannot resolve the selected body's root feature")
    for root in roots:
        document.erase_feature(root, cascade=True)


def delete(context):
    document, hosts = selected(context, "delete")
    _delete_definitions(document, {host[p.BODY] for host in hosts})
    return commit(context.scene, document)


def _clone_body(document, body_id):
    """Clone design intent, never a Blender display mesh or a kernel handle."""
    snapshot = document.to_snapshot()
    chain = [feature for feature in snapshot["features"] if feature["body"] == body_id]
    if not chain:
        raise ValueError("Selected body is not defined in this document")
    sketch_ids = {feature["sketch"] for feature in chain}
    sketches = [sketch for sketch in snapshot["sketches"] if sketch["id"] in sketch_ids]
    identifiers = [body_id] + [feature["id"] for feature in chain]
    for sketch in sketches:
        identifiers += [sketch["id"]] + [item["id"] for key in ("entities", "constraints") for item in sketch[key]]
    remap = {identifier: n.new_document_id() for identifier in identifiers}
    for sketch in copy.deepcopy(sketches):
        sketch["id"] = remap[sketch["id"]]
        sketch["name"] += " copy"
        for entity in sketch["entities"]:
            entity["id"] = remap[entity["id"]]
        for constraint in sketch["constraints"]:
            constraint["id"] = remap[constraint["id"]]
            for target in constraint["targets"]:
                if target["entity"] is not None:
                    target["entity"] = remap[target["entity"]]
        snapshot["sketches"].append(sketch)
    for feature in copy.deepcopy(chain):
        for key in ("id", "body", "sketch", "input"):
            if feature[key] is not None:
                feature[key] = remap[feature[key]]
        feature["name"] += " copy"
        snapshot["features"].append(feature)
    snapshot["revision"] += 1
    # Named references are document-level bookmarks, not feature inputs yet.
    # Preserve their original body targets; do not silently retarget them to the
    # copy. Future consuming features must clone/remap their explicit inputs.
    # The native document validates all global identities, units and dependencies
    # before the cloned definitions can be published. Named parameters deliberately
    # remain shared document-level inputs, not copied Blender object properties.
    return n.Document.from_snapshot(snapshot), remap[body_id]


def duplicate(context):
    document, hosts = selected(context, "duplicate")
    context.view_layer.update()
    if document.rollback_before is not None:
        raise ValueError("Return to the end of history before duplicating a Solid")
    placements = []
    for host in hosts:
        document, body_id = _clone_body(document, host[p.BODY])
        display = p.display_for(context.scene, host)
        placements.append((body_id, host.matrix_world.copy(), display.data.materials[0] if display and display.data.materials else None))
    commit(context.scene, document)
    by_body = {host[p.BODY]: host for host in p.hosts(context.scene, document.id)}
    for body_id, matrix, material in placements:
        by_body[body_id].matrix_world = matrix
        if material:
            p.display_for(context.scene, by_body[body_id]).data.materials[0] = material
    _select(context, [body_id for body_id, _, _ in placements])


def convert(context):
    document, hosts = selected(context, "convert")
    context.view_layer.update()
    state = p.runtime(context.scene)
    for host in hosts:
        # Requires a current mapping/current B-rep. Conversion must not silently
        # turn the last-good display of a failed design into its requested result.
        p.current_packet(context.scene, host)
        state.evaluator.volume_mm3(document, host[p.BODY])
    bodies = {host[p.BODY] for host in hosts}
    converted = [p.display_for(context.scene, host) for host in hosts]
    _delete_definitions(document, bodies)

    commit(context.scene, document, convert_hosts=hosts)
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in converted:
        obj.select_set(True)
        context.view_layer.objects.active = obj
    return converted
