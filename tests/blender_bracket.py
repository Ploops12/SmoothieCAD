"""Mechanical-bracket acceptance through Blender commands and sketch sessions.

Construction deliberately uses no kernel primitives: OCCT's raw adapter is used
only to validate the STEP result written by the addon export operator.
"""

import argparse
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy
from mathutils import Vector
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
parser.add_argument("--output-dir", required=True, type=Path)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
options.output_dir.mkdir(parents=True, exist_ok=True)
(options.output_dir / "result.json").write_text('{"passed":false,"status":"running"}\n')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def close(actual, expected, message, *, relative=1e-7, absolute=1e-11):
    require(math.isclose(actual, expected, rel_tol=relative, abs_tol=absolute),
            f"{message}: {actual!r} != {expected!r}")


def parameters(document):
    return {parameter.name: parameter.value for parameter in document.parameters}


def expected_volume(document, *, holes=2, rounded=True):
    values = parameters(document)
    area = values["span"] * values["thickness"] + values["thickness"] * (values["height"] - values["thickness"])
    volume = area * values["depth"] - holes * math.pi * values["hole_radius"] ** 2 * values["thickness"]
    if rounded:
        volume -= (1 - math.pi / 4) * values["round_radius"] ** 2 * values["depth"]
    return volume


def owner(scene, body):
    document = p.read_document(scene)
    host = next(item for item in p.hosts(scene, document.id) if item[p.BODY] == body)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    host.select_set(True)
    bpy.context.view_layer.objects.active = host
    return host


def reference(entity_id, element=None):
    return n.SketchReference(entity_id, E.GEOMETRY if element is None else element)


def fully_constrained(session):
    require(session.report is not None and session.report.state == n.SketchSolveState.FULLY_CONSTRAINED,
            f"Sketch is not fully constrained: {session.error}")
    require(session.report.remaining_degrees_of_freedom == 0, "Sketch retains degrees of freedom")


def select_target(scene, target):
    document = p.read_document(scene)
    resolution = p.runtime(scene).evaluator.resolve_reference(document, target)
    require(resolution.state == n.ReferenceState.RESOLVED and len(resolution.candidates) == 1,
            f"Bracket reference did not resolve uniquely: {resolution.state}")
    element = resolution.candidates[0]
    host = owner(scene, target.body_id)
    packet = p.current_packet(scene, host)
    pick = p.resolve(scene, host, element.kind.name, element.index, packet.token)
    p.apply_pick(bpy.context, host, pick)
    return pick


def build_bracket(label, scale):
    scene = bpy.data.scenes.new(f"Bracket {label}")
    bpy.context.window.scene = scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1
    require(bpy.ops.blendercad.new_document() == {"FINISHED"}, "Could not create bracket document")
    for name, value in (("span", 50), ("thickness", 5), ("height", 40), ("depth", 30),
                        ("hole_radius", 3), ("round_radius", 1)):
        commands.set_parameter(bpy.context, name, value * scale, "mm")
    commands.set_parameter(bpy.context, "unused_factor", 1, "unitless")
    for name, expression in (("rise", "height - thickness"), ("foot_x", "span * 0.6"),
                             ("arm_y", "height / 2"), ("half_depth", "depth / 2"),
                             ("through_depth", "thickness * 3")):
        commands.set_parameter(bpy.context, name, 0, "mm", expression=expression)
    source = p.read_document(scene)
    require(source.geometric_tolerance_mm == 0.001, "Bracket construction relaxed the modelling budget")
    baseline = scene.blendercad_document_snapshot
    profile = Session(source, name="Bracket L profile")
    points = [(0, 0), (50 * scale, 0), (50 * scale, 5 * scale),
              (5 * scale, 5 * scale), (5 * scale, 40 * scale), (0, 40 * scale)]
    lines = []
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        start_ref = n.SketchReference() if index == 0 else reference(lines[-1], E.LINE_END)
        end_ref = reference(lines[0], E.LINE_START) if index == len(points) - 1 else None
        lines.append(profile.add_line(start, end, start_ref=start_ref, end_ref=end_ref,
                                      horizontal=start[1] == end[1], vertical=start[0] == end[0]))
    dimensions = {}
    for index, parameter in ((0, "span"), (1, "thickness"), (3, "rise"), (4, "thickness")):
        dimensions[str(index)] = profile.add_constraint(K.LENGTH, [reference(lines[index])], parameter=parameter)
    fully_constrained(profile)
    require(scene.blendercad_document_snapshot == baseline, "Unconfirmed bracket sketch altered the scene")
    profile_id = profile.sketch_id
    commands.commit(scene, profile.document)
    base = commands.extrude_sketch(bpy.context, profile_id, "depth")
    document = p.read_document(scene)
    body = document.get_feature(base).body_id

    def hole_sketch(name, plane, x_parameter):
        document = p.read_document(scene)
        values = parameters(document)
        draft = Session(document, name=name, plane=plane)
        circle = draft.add_circle((values[x_parameter], values["half_depth"]), values["hole_radius"])
        center = reference(circle, E.CIRCLE_CENTER)
        draft.add_constraint(K.RADIUS, [reference(circle)], parameter="hole_radius")
        draft.add_constraint(K.DISTANCE_X, [n.SketchReference(), center], parameter=x_parameter)
        draft.add_constraint(K.DISTANCE_Y, [n.SketchReference(), center], parameter="half_depth")
        fully_constrained(draft)
        commands.commit(scene, draft.document)
        return draft.sketch_id, circle

    # These explicit work planes remain part-local. Positive edits below drive
    # span/depth and hole locations, not an unimplemented parametric plane origin.
    foot_plane = n.SketchPlane()
    foot_plane.origin = n.Vector3(0, 10 * scale, 0)
    foot_plane.x_axis, foot_plane.y_axis = n.Vector3(1, 0, 0), n.Vector3(0, 0, 1)
    foot_sketch, foot_circle = hole_sketch("Foot mounting hole", foot_plane, "foot_x")
    foot_cut = commands.extrude_sketch(bpy.context, foot_sketch, "through_depth", input_feature_id=base)
    arm_plane = n.SketchPlane()
    arm_plane.origin = n.Vector3(-5 * scale, 0, 0)
    arm_plane.x_axis, arm_plane.y_axis = n.Vector3(0, 1, 0), n.Vector3(0, 0, 1)
    arm_sketch, arm_circle = hole_sketch("Arm mounting hole", arm_plane, "arm_y")
    arm_cut = commands.extrude_sketch(bpy.context, arm_sketch, "through_depth", input_feature_id=foot_cut)
    target = n.PersistentReference(body, n.TopologyKind.EDGE, n.ReferenceAnchor(base, "edge:sweep:" + lines[0] + ":end"))
    select_target(scene, target)
    require(bpy.ops.blendercad.fillet(parameter="round_radius") == {"FINISHED"}, "Bracket edge fillet failed")
    document = p.read_document(scene)
    fillet = document.features[-1].id
    require(document.get_feature(fillet).input_id == arm_cut, "Fillet did not consume the bracket's current tip")
    require([item.type for item in document.features] == ["extrusion", "subtractive_extrusion", "subtractive_extrusion", "fillet"],
            "Bracket feature history changed meaning")
    model = {"label": label, "scale": scale, "scene": scene.name, "document": document.id, "body": body,
             "profile": profile_id, "foot_sketch": foot_sketch, "arm_sketch": arm_sketch,
             "foot_circle": foot_circle, "arm_circle": arm_circle,
             "features": [base, foot_cut, arm_cut, fillet], "lines": lines, "dimensions": dimensions,
             "references": {}, "definition_ids": {
                 sketch.id: {"entities": [entity.id for entity in document.get_sketch(sketch.id).entities],
                             "constraints": [constraint.id for constraint in document.get_sketch(sketch.id).constraints]}
                 for sketch in document.sketches}}
    return model


def identities(model):
    document = p.read_document(bpy.context.scene)
    require(document.id == model["document"], "Bracket document identity changed")
    require([feature.id for feature in document.features] == model["features"], "Bracket history identities changed")
    require(all(feature.body_id == model["body"] for feature in document.features), "Bracket body lineage split")
    for sketch_id, ids in model["definition_ids"].items():
        problem = document.get_sketch(sketch_id)
        require([entity.id for entity in problem.entities] == ids["entities"], "Sketch entity identity changed")
        require([constraint.id for constraint in problem.constraints] == ids["constraints"], "Sketch constraint identity changed")
    require(document.geometric_tolerance_mm == 0.001, "Bracket operation changed the default modelling budget")
    return document


def vertex_bounds(query, count):
    points = [query(n.TopologyElement(n.TopologyKind.VERTEX, index)).center_mm for index in range(count)]
    require(bool(points), "Bracket has no authoritative CAD vertices")
    rows = [(point.x, point.y, point.z) for point in points]
    return [min(row[axis] for row in rows) for axis in range(3)], [max(row[axis] for row in rows) for axis in range(3)]


def geometry(model):
    scene = bpy.context.scene
    document = identities(model)
    state = p.runtime(scene)
    require([item.state for item in state.report.features] == [n.EvaluationState.UP_TO_DATE] * 4,
            "Bracket contains a failed, blocked or stale feature")
    values = parameters(document)
    body = model["body"]
    packet = p.current_packet(scene, owner(scene, body))
    measure = lambda element: state.evaluator.measure_element(document, body, element)
    minimum, maximum = vertex_bounds(measure, len(packet.arrays["cad_vertices"]) // 3)
    for actual, expected in zip(minimum, (0, 0, 0)):
        require(abs(actual - expected) <= document.geometric_tolerance_mm, "Bracket minimum exceeds modelling budget")
    for actual, expected in zip(maximum, (values["span"], values["height"], values["depth"])):
        require(abs(actual - expected) <= document.geometric_tolerance_mm, "Bracket dimension exceeds modelling budget")
    volume = state.evaluator.volume_mm3(document, body)
    close(volume, expected_volume(document), "Analytical bracket volume")
    curved = [(index, measure(n.TopologyElement(n.TopologyKind.FACE, index))) for index in range(packet.arrays["face_count"])]
    holes = [(index, item) for index, item in curved if item.radius_mm is not None
             and math.isclose(item.radius_mm, values["hole_radius"], rel_tol=1e-8, abs_tol=1e-10)]
    rounds = [(index, item) for index, item in curved if item.radius_mm is not None
              and math.isclose(item.radius_mm, values["round_radius"], rel_tol=1e-8, abs_tol=1e-10)]
    require(len(holes) == 2 and len(rounds) == 1, "Bracket lost its two analytic holes or cylindrical edge round")
    for _index, item in holes:
        close(item.area_mm2, 2 * math.pi * values["hole_radius"] * values["thickness"], "Hole's authoritative cylindrical area")
    close(rounds[0][1].area_mm2, math.pi / 2 * values["round_radius"] * values["depth"], "Fillet's authoritative cylindrical area")
    # Hole rays query B-rep geometry, not display triangles. Both pass completely
    # through their mounting plates; nearby rays must hit real plate material.
    for origin, direction, offset_axis in (
        ((values["foot_x"], values["thickness"] + 10 * model["scale"], values["half_depth"]), (0, -1, 0), 2),
        ((-10 * model["scale"], values["arm_y"], values["half_depth"]), (1, 0, 0), 1),
    ):
        require(state.evaluator.ray_cast(document, body, n.Vector3(*origin), n.Vector3(*direction), 200 * model["scale"]) is None,
                "A mounting hole is not through the plate")
        nearby = list(origin)
        nearby[offset_axis] += 1.5 * values["hole_radius"]
        require(state.evaluator.ray_cast(document, body, n.Vector3(*nearby), n.Vector3(*direction), 200 * model["scale"]) is not None,
                "Hole verification ray missed neighboring bracket material")
    before = document.to_snapshot()
    for sketch in document.sketches:
        report = n.new_sketch_solver(document.geometric_tolerance_mm).solve(document.evaluated_sketch(sketch.id))
        require(report.state == n.SketchSolveState.FULLY_CONSTRAINED and report.remaining_degrees_of_freedom == 0,
                "A bracket modelling sketch became underconstrained")
    require(document.to_snapshot() == before, "Verification rewrote saved sketch guesses")
    return {"volume_mm3": volume, "analytical_volume_mm3": expected_volume(document), "minimum_mm": minimum,
            "maximum_mm": maximum, "hole_radius_mm": values["hole_radius"], "fillet_radius_mm": values["round_radius"],
            "budget_mm": document.geometric_tolerance_mm, "face_count": packet.arrays["face_count"]}, holes, rounds


def save_references(model):
    scene = bpy.context.scene
    document = p.read_document(scene)
    _report, holes, rounds = geometry(model)
    targets = []
    for index, item in holes:
        name = "Foot hole" if item.center_mm.x > parameters(document)["thickness"] * 2 else "Arm hole"
        targets.append((name, p.runtime(scene).evaluator.capture_reference(document, model["body"], n.TopologyKind.FACE, index)))
    targets.append(("Edge round", p.runtime(scene).evaluator.capture_reference(document, model["body"], n.TopologyKind.FACE, rounds[0][0])))
    for name, target in targets:
        select_target(scene, target)
        model["references"][name] = commands.save_reference(bpy.context, name)


def check_references(model):
    scene = bpy.context.scene
    document = identities(model)
    for name, identifier in model["references"].items():
        target = document.get_reference(identifier).target
        result = p.runtime(scene).evaluator.resolve_reference(document, target)
        require(result.state == n.ReferenceState.RESOLVED and len(result.candidates) == 1, f"{name} lost persistent provenance")
        measure = p.runtime(scene).evaluator.measure_reference(document, target)
        close(measure.radius_mm, parameters(document)["round_radius" if name == "Edge round" else "hole_radius"], f"{name} radius")


def exercise_history(model, output):
    scene = bpy.context.scene
    body = model["body"]
    host = owner(scene, body)
    material = bpy.data.materials.new(f"Bracket {model['label']} finish")
    material.use_nodes = True
    material.diffuse_color = (0.14, 0.32, 0.55, 1)
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = material.diffuse_color
    shader.inputs["Metallic"].default_value = 0.15
    shader.inputs["Roughness"].default_value = 0.28
    p.display_for(scene, host).data.materials[0] = material
    model["material"] = material.name
    commands.set_parameter(bpy.context, "unused_factor", 2)
    require((p.runtime(scene).report.computed_count, p.runtime(scene).report.reused_count) == (0, 4),
            "An unrelated parameter edit reevaluated bracket geometry")
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Bracket span baseline")
    require(bpy.ops.blendercad.parameter(name="span", value=60 * model["scale"]) == {"FINISHED"}, "Earlier span edit failed")
    bpy.ops.ed.undo_push(message="Bracket span edit")
    geometry(model)
    check_references(model)
    require(bpy.ops.ed.undo() == {"FINISHED"}, "Bracket undo failed")
    scene = bpy.context.scene
    close(parameters(identities(model))["span"], 50 * model["scale"], "Undo span")
    geometry(model)
    check_references(model)
    require(bpy.ops.ed.redo() == {"FINISHED"}, "Bracket redo failed")
    scene = bpy.context.scene
    geometry(model)
    check_references(model)
    commands.set_parameter(bpy.context, "depth", 36 * model["scale"])
    geometry(model)
    check_references(model)
    document = identities(model)
    close(document.get_sketch(model["profile"]).entities[0].geometry.end.x, 50 * model["scale"], "Saved profile guess")
    close(document.get_sketch(model["foot_sketch"]).entities[0].geometry.center.x, 30 * model["scale"], "Saved hole guess")
    commands.set_parameter(bpy.context, "span", 50 * model["scale"])
    commands.set_parameter(bpy.context, "depth", 30 * model["scale"])
    geometry(model)
    check_references(model)
    host = owner(scene, body)
    generation = p.current_packet(scene, host).arrays["geometry_generation"]
    commands.set_parameter(bpy.context, "thickness", 0)
    document = identities(model)
    require([item.state for item in p.runtime(scene).report.features] ==
            [n.EvaluationState.FAILED, n.EvaluationState.BLOCKED, n.EvaluationState.BLOCKED, n.EvaluationState.BLOCKED],
            "Invalid earlier dimension did not block the bracket's downstream features")
    require(not p.display_for(scene, host)["blendercad_current"], "Failed bracket retained current display status")
    require(p.runtime(scene).evaluator.tessellate(document, body)["geometry_generation"] == generation,
            "Failure discarded the last valid bracket display")
    try:
        p.runtime(scene).evaluator.volume_mm3(document, body)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Failed bracket allowed a current CAD measurement")
    sentinel = output / "failed-export.step"
    sentinel.write_bytes(b"Unchanged destination: failed CAD geometry must not replace this file.\n")
    before_bytes = sentinel.read_bytes()
    before_snapshot = scene.blendercad_document_snapshot
    before_files = set(output.iterdir())
    try:
        result = bpy.ops.blendercad.export_step(filepath=str(sentinel))
    except RuntimeError:
        pass
    else:
        require(result == {"CANCELLED"}, "Failed bracket STEP export was accepted")
    require(sentinel.read_bytes() == before_bytes and scene.blendercad_document_snapshot == before_snapshot,
            "Rejected STEP export changed its destination or document")
    require(set(output.iterdir()) == before_files, "Rejected STEP export leaked temporary files")
    commands.set_parameter(bpy.context, "thickness", 5 * model["scale"])
    geometry(model)
    check_references(model)
    require(bpy.ops.blendercad.suppress_feature(feature_id=model["features"][1], suppressed=True) == {"FINISHED"}, "Hole suppression failed")
    require([item.state for item in p.runtime(scene).report.features] ==
            [n.EvaluationState.UP_TO_DATE, n.EvaluationState.SUPPRESSED, n.EvaluationState.BLOCKED, n.EvaluationState.BLOCKED],
            "Suppression did not retain and block dependent bracket definitions")
    require(bpy.ops.blendercad.suppress_feature(feature_id=model["features"][1], suppressed=False) == {"FINISHED"}, "Hole unsuppression failed")
    require(bpy.ops.blendercad.rollback(feature_id=model["features"][1]) == {"FINISHED"}, "Bracket rollback failed")
    document = identities(model)
    close(p.runtime(scene).evaluator.volume_mm3(document, body), expected_volume(document, holes=0, rounded=False), "Rolled-back stock volume")
    require(bpy.ops.blendercad.rollback() == {"FINISHED"}, "Bracket history restoration failed")
    geometry(model)
    check_references(model)
    require(p.display_for(scene, owner(scene, body)).data.materials[0].name == model["material"],
            "Bracket recomputation lost the whole-body material")


def exchange_and_render(model, output):
    scene = bpy.context.scene
    document = identities(model)
    values = parameters(document)
    host = owner(scene, model["body"])
    host.name = f"Mechanical bracket {model['label']}"
    unit = p.unit_scale(scene)
    host.location = Vector((100, -50, 75)) * model["scale"] * unit
    host.rotation_euler = (0.15, -0.10, 0.2)
    bpy.context.view_layer.update()
    placement = [tuple(row) for row in host.matrix_world]
    before_export = scene.blendercad_document_snapshot
    step_file = output / f"bracket-{model['label']}.step"
    require(bpy.ops.blendercad.export_step(filepath=str(step_file)) == {"FINISHED"}, "Bracket STEP export failed")
    require(scene.blendercad_document_snapshot == before_export, "STEP export changed bracket intent")
    kernel = n.new_geometry_adapter()
    imported = kernel.read_step(str(step_file))
    require(kernel.is_valid(imported), "STEP bracket B-rep is invalid")
    require(kernel.max_tolerance_mm(imported) <= document.geometric_tolerance_mm, "STEP bracket exceeds modelling budget")
    close(kernel.volume_mm3(imported), expected_volume(document), "STEP round-trip bracket volume")
    count = n.geometry_topology(kernel, imported).counts[2]
    minimum, maximum = vertex_bounds(lambda element: n.geometry_measure(kernel, imported, element), count)
    for actual, expected in zip(minimum, (0, 0, 0)):
        require(abs(actual - expected) <= document.geometric_tolerance_mm, "STEP export unexpectedly used host placement")
    for actual, expected in zip(maximum, (values["span"], values["height"], values["depth"])):
        require(abs(actual - expected) <= document.geometric_tolerance_mm, "STEP dimensions are not part-local millimetres")

    # Frame the placed Solid, with every geometric camera quantity scaled.
    # SUN illumination is distance-independent, so the same material is tested
    # at micro-mechanical, ordinary and furniture-scale dimensions.
    camera_data = bpy.data.cameras.new(f"Bracket {model['label']} camera")
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    scene.collection.objects.link(camera)
    center = host.matrix_world @ (Vector((values["span"] / 2, values["height"] / 2, values["depth"] / 2)) * unit)
    offset = host.matrix_world.to_3x3() @ (Vector((90, -120, 180)) * model["scale"] * unit)
    camera.location = center + offset
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 100 * model["scale"] * unit
    camera_data.clip_start = max(1e-6, 0.1 * model["scale"] * unit)
    camera_data.clip_end = max(1, 1000 * model["scale"] * unit)
    scene.camera = camera
    sun_data = bpy.data.lights.new(f"Bracket {model['label']} sun", "SUN")
    sun_data.energy = 3
    sun = bpy.data.objects.new(sun_data.name, sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = camera.rotation_euler.copy()
    scene.world = bpy.data.worlds.new(f"Bracket {model['label']} world")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.05
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples, scene.cycles.use_denoising = "CPU", 8, False
    scene.render.resolution_x = scene.render.resolution_y = 256
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    image_file = output / f"bracket-{model['label']}.png"
    scene.render.filepath = str(image_file)
    blend_file = output / f"bracket-{model['label']}.blend"
    snapshot = scene.blendercad_document_snapshot
    old_token = p.current_packet(scene, host).token
    require(bpy.ops.wm.save_as_mainfile(filepath=str(blend_file)) == {"FINISHED"}, "Bracket save failed")
    require(bpy.ops.wm.open_mainfile(filepath=str(blend_file)) == {"FINISHED"}, "Bracket reopen failed")
    scene = bpy.context.scene
    require(scene.name == model["scene"], "Reopening changed the active bracket scene")
    require(scene.blendercad_document_snapshot == snapshot, "Reopening rewrote bracket definitions")
    host = owner(scene, model["body"])
    bpy.context.view_layer.update()
    require(p.current_packet(scene, host).token != old_token, "Reopening retained a stale display packet")
    for actual_row, expected_row in zip(host.matrix_world, placement):
        for actual, expected in zip(actual_row, expected_row):
            close(actual, expected, "Saved host placement", relative=1e-6, absolute=1e-8)
    require(p.display_for(scene, host).data.materials[0].name == model["material"], "Reopening lost bracket material")
    audit, _holes, _rounds = geometry(model)
    check_references(model)
    require(bpy.ops.render.render(write_still=True) == {"FINISHED"}, "Bracket Cycles rendering failed")
    image = bpy.data.images.load(str(image_file), check_existing=False)
    try:
        pixels = np.empty(256 * 256 * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        pixels = pixels.reshape((256, 256, 4))
        mask = pixels[:, :, 3] > 0.9
        opaque = int(np.count_nonzero(mask))
        require(opaque > 500, "Cycles did not render the managed bracket surface")
        require(float(pixels[:, :, :3].max()) > 0.05, "Bracket material rendered black")
        rows, columns = np.nonzero(mask)
        require(columns.min() > 3 and columns.max() < 252 and rows.min() > 3 and rows.max() < 252,
                "Scaled bracket render is clipped by the camera frame")
        render = {"opaque_pixels": opaque, "rgb_max": float(pixels[:, :, :3].max()),
                  "bounds_px": [int(columns.min()), int(rows.min()), int(columns.max()), int(rows.max())],
                  "samples": 8, "width": 256, "height": 256, "file": str(image_file)}
    finally:
        bpy.data.images.remove(image)
    audit.update(label=model["label"], scale=model["scale"], document_id=model["document"], body_id=model["body"],
                 feature_ids=model["features"], sketch_ids=list(model["definition_ids"]), reference_ids=model["references"],
                 blend_file=str(blend_file), step_file=str(step_file), step_valid=True, render=render)
    return audit


with tempfile.TemporaryDirectory(prefix="addon-", dir=options.output_dir) as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, presentation as p
        from blendercad.sketch_commands import Session
        n = addon._native
        require(n is not None, "The packaged bracket native module is unavailable")
        K, E = n.SketchConstraintKind, n.SketchElement
        reports = []
        for label, scale in (("micro", 0.01), ("mechanical", 1.0), ("furniture", 20.0)):
            output = options.output_dir / label
            output.mkdir(parents=True, exist_ok=True)
            model = build_bracket(label, scale)
            geometry(model)
            save_references(model)
            exercise_history(model, output)
            reports.append(exchange_and_render(model, output))
        result = {"passed": True, "occt": n.occt_version(), "native": n.blendercad_version(),
                  "blender": list(bpy.app.version), "modelling_budget_mm": 0.001, "models": reports}
        (options.output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print("Bracket acceptance: constrained L profile, orthogonal through holes, persistent fillet, expressions/history/failure, STEP, save/reopen, undo/redo and Cycles at all three scales passed")
    finally:
        addon.unregister()
