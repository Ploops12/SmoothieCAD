"""CAD commands shared by numeric UI and subsequent tools.

Commands mutate a detached native document, then evaluate and stage display
meshes before publishing the snapshot. Blender operators supply undo boundaries;
construction history remains independent of Blender's undo log.
"""

import copy
import os
from pathlib import Path
import tempfile

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


def _step_export_source(context):
    """Validate a current analytic source without recomputing or editing it."""
    from . import sketch_editor, feature_editor
    sketch_editor.authorize_commit(context.scene)
    feature_editor.authorize_commit(context.scene)
    document, hosts = selected(context, "export")
    if len(hosts) != 1 or p.host_for(context.active_object) != hosts[0]:
        raise ValueError("Select exactly one active CAD Solid for STEP export")
    host = hosts[0]
    p._validate_associations(context.scene, document.id)
    from .selection import _rigid
    _rigid(host)
    state = p.runtime(context.scene)
    if not state.evaluator.matches_document(document):
        raise ValueError("CAD document changed; recompute before exporting STEP")
    body = next((item for item in state.report.bodies if item.body_id == host[p.BODY]), None) if state.report else None
    if body is None or body.state != n.EvaluationState.UP_TO_DATE:
        raise ValueError("Selected Solid has no current valid CAD geometry; repair or recompute before exporting STEP")
    return document, host[p.BODY], state.evaluator


def export_step(context, filepath):
    """Publish one current part-local millimetre B-rep as an atomic STEP file.

    Placement and display tessellation are presentation inputs, not alternate
    export geometry. No document, Blender object or undo state is changed here.
    """
    document, body, evaluator = _step_export_source(context)
    raw = os.fspath(filepath)
    if not isinstance(raw, str) or not raw:
        raise ValueError("Choose a STEP export file path")
    destination = Path(bpy.path.abspath(raw)).absolute()
    if not destination.parent.is_dir():
        raise ValueError("STEP export directory does not exist: " + str(destination.parent))
    if destination.exists() and not destination.is_file():
        raise ValueError("Choose a STEP file path, not a directory or special file")
    snapshot = context.scene.blendercad_document_snapshot
    temporary = None
    try:
        # A fixed short prefix also supports destination names near NAME_MAX.
        descriptor, name = tempfile.mkstemp(prefix=".blendercad-step-", suffix=".step", dir=destination.parent)
        temporary = Path(name)
        os.close(descriptor)
        evaluator.write_step(document, body, str(temporary))
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("The CAD writer did not produce a STEP file")
        if (context.scene.blendercad_document_snapshot != snapshot
                or not evaluator.matches_document(document)):
            raise ValueError("CAD document changed during STEP export; recompute and export again")
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def commit(scene, document, convert_hosts=(), *, sketch_session=None, feature_session=None, placements=None):
    from . import sketch_editor, feature_editor
    sketch_editor.authorize_commit(scene, sketch_session)
    feature_editor.authorize_commit(scene, feature_session)
    previous_runtime = p.runtime(scene)
    prepared = p.prepare(scene, document, convert_hosts)
    prepared.host_placements = placements or {}
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


def rectangle_profile(width, height, width_parameter="", height_parameter=""):
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
    constraints += [n.SketchConstraint(kind.LENGTH, [ref(0, element.GEOMETRY)], width, parameter=width_parameter or None),
                    n.SketchConstraint(kind.LENGTH, [ref(1, element.GEOMETRY)], height, parameter=height_parameter or None),
                    n.SketchConstraint(kind.COINCIDENT, [ref(0, element.LINE_START), n.SketchReference()])]
    problem.entities, problem.constraints = lines, constraints
    return problem


def add_rectangle(context, width, height, depth, width_parameter="", height_parameter=""):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    if depth <= 0:
        raise ValueError("Extrusion depth must be positive")
    width = document.length_value(width_parameter) if width_parameter else width
    height = document.length_value(height_parameter) if height_parameter else height
    sketch = document.add_sketch("Rectangle", rectangle_profile(width, height, width_parameter, height_parameter))
    feature = document.add_extrusion("Rectangle extrusion", sketch, depth)
    body = document.get_feature(feature).body_id
    commit(context.scene, document)
    _select(context, [body])
    return feature


def set_depth(context, feature_id, depth):
    from .feature_commands import set_feature_input
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    if document.get_feature(feature_id).type == "fillet":
        raise ValueError("Select an extrusion feature")
    set_feature_input(document, feature_id, depth)
    return commit(context.scene, document)


def _fillet_picks(context, document, hosts):
    if len(hosts) != 1 or p.host_for(context.active_object) != hosts[0]:
        raise ValueError("Select CAD edges on exactly one active Solid")
    host = hosts[0]
    picks = [(owner, pick) for owner, pick, _packet in p.visible_selection(context.scene) if owner == host]
    if not picks or any(pick.kind != "EDGE" for _owner, pick in picks):
        raise ValueError("Select only CAD edges for the fillet")
    return host, [p.persistent_target(context.scene, owner, pick) for owner, pick in picks]


def add_fillet(context, radius):
    from .feature_commands import Operation, create_feature
    document, hosts = selected(context, "edit_definition")
    if document.rollback_before is not None:
        raise ValueError("Return to the end of history before creating a fillet")
    host, edges = _fillet_picks(context, document, hosts)
    tip = next(item.id for item in reversed(document.features) if item.body_id == host[p.BODY])
    feature = create_feature(document, Operation("fillet", input_id=tip, edges=tuple(edges)), radius)
    commit(context.scene, document)
    _select(context, [host[p.BODY]])
    return feature


def set_fillet_radius(context, feature_id, radius):
    from .feature_commands import set_feature_input
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    if document.get_feature(feature_id).type != "fillet":
        raise ValueError("Select a fillet feature")
    set_feature_input(document, feature_id, radius)
    return commit(context.scene, document)


def repair_fillet_edges(context, feature_id):
    document, hosts = selected(context, "edit_definition")
    feature = document.get_feature(feature_id)
    if feature.type != "fillet" or document.rollback_before != feature_id:
        raise ValueError("Roll back immediately before this fillet, then select its replacement input edges")
    host, edges = _fillet_picks(context, document, hosts)
    if host[p.BODY] != feature.body_id:
        raise ValueError("Replacement edges must belong to the fillet's input Solid")
    packet = p.current_packet(context.scene, host)
    if packet.arrays["feature_id"] != feature.input_id:
        raise ValueError("Selected edges are not from this fillet's input feature")
    document.set_fillet_edges(feature_id, edges)
    document.set_rollback_before(None)
    return commit(context.scene, document)


def suppress_feature(context, feature_id, suppressed):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    document.set_feature_suppressed(feature_id, suppressed)
    return commit(context.scene, document)


def rollback(context, feature_id=""):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    document.set_rollback_before(feature_id or None)
    return commit(context.scene, document)


def move_feature(context, feature_id, offset):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    if isinstance(offset, bool) or offset not in (-1, 1):
        raise ValueError("Move a feature one history position at a time")
    order = [item.id for item in document.features]
    index = order.index(feature_id)
    destination = index + offset
    if destination < 0 or destination >= len(order):
        raise ValueError("Feature is already at this end of history")
    order[index], order[destination] = order[destination], order[index]
    document.reorder_features(order)
    return commit(context.scene, document)


def delete_feature(context, feature_id, cascade=False):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    document.erase_feature(feature_id, cascade=cascade)
    return commit(context.scene, document)


def set_sketch_dimension(context, sketch_id, constraint_id, value):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    document.set_sketch_dimension(sketch_id, constraint_id, value)
    return commit(context.scene, document)


def selected_face_support(context, reversed=True):
    """Capture a planar face at its explicit current upstream feature result."""
    from . import feature_editor, sketch_editor
    from .selection import _rigid
    sketch_editor.authorize_commit(context.scene)
    feature_editor.authorize_commit(context.scene)
    document, hosts = selected(context, "edit_definition")
    if len(hosts) != 1 or p.host_for(context.active_object) != hosts[0]:
        raise ValueError("Select one CAD face on exactly one active Solid")
    host = hosts[0]
    context.view_layer.update()
    _rigid(host)
    picks = [(pick, packet) for owner, pick, packet in p.visible_selection(context.scene) if owner == host]
    if len(picks) != 1 or picks[0][0].kind != "FACE":
        raise ValueError("Select exactly one planar CAD face for sketch support")
    pick, packet = picks[0]
    evaluator = p.runtime(context.scene).evaluator
    plane = evaluator.planar_face(document, host[p.BODY], pick.index)
    if plane is None:
        raise ValueError("Sketch support requires an analytic planar CAD face; curved faces are not supported")
    support = n.SketchSupport(packet.arrays["feature_id"], p.persistent_target(context.scene, host, pick),
                              x_direction=plane.x_axis, reversed=reversed)
    evaluator.support_plane(document, support)
    return support


def _sketch_support(document, sketch_id):
    definition = next((item for item in document.sketches if item.id == sketch_id), None)
    if definition is None or definition.support is None:
        raise ValueError("Select a face-attached sketch")
    return definition.support


def edit_sketch_support(context, sketch_id, offset_u, offset_v, normal_offset, reversed, x_direction=None):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    support = _sketch_support(document, sketch_id)
    support.offset_u_mm, support.offset_v_mm = offset_u, offset_v
    support.normal_offset_mm, support.reversed = normal_offset, reversed
    if x_direction is not None:
        support.x_direction = x_direction
    document.set_sketch_support(sketch_id, support)
    return commit(context.scene, document)


def repair_sketch_support(context, sketch_id):
    document = p.read_document(context.scene)
    previous = _sketch_support(document, sketch_id)
    support = selected_face_support(context, previous.reversed)
    support.x_direction = previous.x_direction
    support.offset_u_mm, support.offset_v_mm = previous.offset_u_mm, previous.offset_v_mm
    support.normal_offset_mm = previous.normal_offset_mm
    # Validate the explicit replacement frame before publication; do not invent
    # another tangent if the saved direction becomes parallel to its normal.
    p.runtime(context.scene).evaluator.support_plane(document, support)
    document.set_sketch_support(sketch_id, support)
    document.set_rollback_before(None)
    return commit(context.scene, document)


def attach_sketch_support(context, sketch_id, reversed=True):
    document = p.read_document(context.scene)
    document.set_sketch_support(sketch_id, selected_face_support(context, reversed))
    document.set_rollback_before(None)
    return commit(context.scene, document)


def detach_sketch_support(context, sketch_id):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    support = _sketch_support(document, sketch_id)
    plane = p.runtime(context.scene).evaluator.support_plane(document, support)
    document.detach_sketch(sketch_id, plane)
    return commit(context.scene, document)


def rollback_sketch_support(context, sketch_id):
    document = p.read_document(context.scene)
    consumer = next((item for item in document.features if item.sketch_id == sketch_id), None)
    if consumer is None:
        raise ValueError("This sketch has no consuming feature; select a valid support face directly")
    return rollback(context, consumer.id)


def delete_sketch(context, sketch_id, cascade=False):
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    document.erase_sketch(sketch_id, cascade=cascade)
    return commit(context.scene, document)


def sketch_placement(context, document, sketch_id=""):
    """Explicit edit/creation frame, not ownership stored in a second model.

    Unused sketches can be worked in the active Solid's local frame. A consumed
    sketch uses that frame only if the active Solid is one of its consumers.
    With no matching active Solid, the document's origin frame is used.
    """
    from mathutils import Matrix
    host = p.host_for(context.active_object)
    consumers = [item for item in document.features if item.sketch_id == sketch_id] if sketch_id else []
    definition = next((item for item in document.sketches if item.id == sketch_id), None)
    if definition is not None and definition.support is not None:
        bodies = {item.body_id for item in consumers}
        if not bodies:
            bodies = {definition.support.face.body_id}
        if host is None or host.get(p.DOC) != document.id or host.get(p.BODY) not in bodies:
            if len(bodies) != 1:
                raise ValueError("Select the consuming Solid whose attached sketch frame should be edited")
            host = next((obj for obj in p.hosts(context.scene, document.id) if obj.get(p.BODY) in bodies), None)
        if host is None:
            raise ValueError("Attached sketch has no available Solid placement frame")
        from .selection import _rigid
        context.view_layer.update()
        _rigid(host)
        return host, host.matrix_world.copy()
    if (host is not None and host.get(p.DOC) == document.id
            and (not consumers or any(item.body_id == host.get(p.BODY) for item in consumers))):
        from .selection import _rigid
        context.view_layer.update()
        _rigid(host)
        return host, host.matrix_world.copy()
    return None, Matrix.Identity(4)


def extrude_sketch(context, sketch_id, depth, input_feature_id=""):
    from .feature_commands import Operation, create_feature
    capabilities.require_capability(context.selected_objects, "edit_definition")
    document = p.read_document(context.scene)
    if document.rollback_before is not None:
        raise ValueError("Return to the end of history before creating a feature")
    _host, placement = sketch_placement(context, document, sketch_id)
    feature = create_feature(document, Operation("cut" if input_feature_id else "extrude",
                             sketch_id=sketch_id, input_id=input_feature_id), depth)
    body = document.get_feature(feature).body_id
    commit(context.scene, document, placements={body: placement} if not input_feature_id else None)
    _select(context, [body])
    return feature


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
    for sketch in document.sketches:
        if (sketch.support is not None and sketch.support.face.body_id in bodies
                and not any(feature.sketch_id == sketch.id for feature in document.features)):
            raise ValueError("Solid supports an unused sketch; detach or delete that sketch explicitly before deleting or converting its support Solid")
    previous_bodies = {feature.body_id for feature in document.features}
    roots = [feature.id for feature in document.features if feature.body_id in bodies and feature.input_id is None]
    if len(roots) != len(bodies):
        raise ValueError("Cannot resolve the selected body's root feature")
    for root in roots:
        if any(feature.id == root for feature in document.features):
            document.erase_feature(root, cascade=True)
    removed = previous_bodies - {feature.body_id for feature in document.features}
    if removed - bodies:
        raise ValueError("Selected Solid supports another Solid; select its dependent Solids too or detach their sketch supports first")


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
    sketch_ids = {feature["sketch"] for feature in chain if "sketch" in feature}
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
        if "support" in sketch:
            support = sketch["support"]
            support["feature"] = remap.get(support["feature"], support["feature"])
            face = support["face"]
            original = n.PersistentReference(face["body"], n.TopologyKind.FACE,
                                             n.ReferenceAnchor(face["feature"], face["key"]))
            target = n.remap_reference(original, remap)
            face.update(body=target.body_id, feature=target.anchor.feature_id, key=target.anchor.key)
        snapshot["sketches"].append(sketch)
    for feature in copy.deepcopy(chain):
        for key in ("id", "body", "sketch", "input"):
            if key in feature and feature[key] is not None:
                feature[key] = remap[feature[key]]
        for edge in feature.get("edges", ()):
            original = n.PersistentReference(edge["body"], n.TopologyKind.EDGE,
                                             n.ReferenceAnchor(edge["feature"], edge["key"]))
            target = n.remap_reference(original, remap)
            edge.update(body=target.body_id, feature=target.anchor.feature_id, key=target.anchor.key)
        feature["name"] += " copy"
        snapshot["features"].append(feature)
    snapshot["revision"] += 1
    # Named references are document-level bookmarks; inline fillet inputs were
    # remapped above through the native construction-key codec.
    # Preserve their original body targets; do not silently retarget them to the
    # copy. New consuming feature types must clone/remap their explicit inputs.
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
