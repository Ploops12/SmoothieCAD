"""Detached interactive-sketch commands against the packaged native module."""

import argparse
import copy
import importlib
import math
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def geometry_values(problem):
    values = []
    for entity in problem.entities:
        geometry = entity.geometry
        if isinstance(geometry, n.Line2):
            values.extend((geometry.start.x, geometry.start.y, geometry.end.x, geometry.end.y))
        else:
            values.extend((geometry.center.x, geometry.center.y, geometry.radius))
    return values


def entity(problem, identifier):
    return next(item for item in problem.entities if item.id == identifier)


def constraint(problem, identifier):
    return next(item for item in problem.constraints if item.id == identifier)


def ref(identifier, element=None):
    return n.SketchReference(identifier, E.GEOMETRY if element is None else element)


def fingerprint():
    state = p.runtime(bpy.context.scene)
    return (bpy.context.scene.blendercad_document_snapshot, id(state.evaluator),
            tuple((host[p.BODY], p.current_packet(bpy.context.scene, host).token,
                   p.display_for(bpy.context.scene, host).data.as_pointer())
                  for host in p.hosts(bpy.context.scene, state.document.id)),
            len(bpy.data.objects), len(bpy.data.meshes))


class SketchEditorChecks(unittest.TestCase):
    def setUp(self):
        self.source = p.read_document(bpy.context.scene)
        self.source_snapshot = copy.deepcopy(self.source.to_snapshot())
        self.sketch_id = self.source.features[0].sketch_id
        self.before = fingerprint()

    def tearDown(self):
        self.assertEqual(self.source.to_snapshot(), self.source_snapshot, "Session mutated its caller's document")
        self.assertEqual(fingerprint(), self.before, "Draft/preview mutated the published Blender model")

    def assert_atomic_rejection(self, session, mutation):
        snapshot = copy.deepcopy(session.document.to_snapshot())
        display = geometry_values(session.display_problem)
        error = session.error
        state = session.report.state
        with self.assertRaises(ValueError):
            mutation()
        self.assertEqual(session.document.to_snapshot(), snapshot)
        self.assertEqual(geometry_values(session.display_problem), display)
        self.assertEqual(session.error, error)
        self.assertEqual(session.report.state, state)

    def test_construction_snapping_plane_ownership_and_deletion(self):
        plane = n.SketchPlane()
        plane.origin = n.Vector3(10, 20, 30)
        plane.x_axis, plane.y_axis = n.Vector3(0, 1, 0), n.Vector3(0, 0, 1)
        session = Session(self.source, name="Offset sketch", plane=plane)
        line = session.add_line((0, 0), (10, 0), start_ref=n.SketchReference(), horizontal=True)
        session.add_constraint(K.LENGTH, [ref(line)], 10)
        self.assertEqual(session.report.state, S.FULLY_CONSTRAINED)
        circle = session.add_circle((10, 0), 2, center_ref=ref(line, E.LINE_END))
        session.add_constraint(K.RADIUS, [ref(circle)], 2)
        self.assertEqual(session.report.state, S.FULLY_CONSTRAINED)
        rectangle = session.add_rectangle((15, 0), (25, 5))
        self.assertEqual(len(rectangle), 4)
        problem = session.document.get_sketch(session.sketch_id)
        self.assertEqual(len(problem.entities), 6)
        self.assertEqual((problem.plane.origin.x, problem.plane.origin.y, problem.plane.origin.z), (10, 20, 30))
        self.assertEqual((problem.plane.x_axis.y, problem.plane.y_axis.z), (1, 1))
        center = entity(session.display_problem, circle).geometry.center
        self.assertAlmostEqual(center.x, 10, places=6)
        self.assertAlmostEqual(center.y, 0, places=6)
        foreign = self.source.get_sketch(self.sketch_id).entities[0].id
        self.assert_atomic_rejection(session, lambda: session.add_line((0, 0), (2, 2), start_ref=ref(foreign, E.LINE_START)))
        self.assert_atomic_rejection(session, lambda: session.add_line((1, 1), (1, 1)))
        self.assert_atomic_rejection(session, lambda: session.add_circle((0, 0), 0))
        self.assert_atomic_rejection(session, lambda: session.add_rectangle((1, 1), (1, 5)))
        session.erase_targets([ref(line, E.LINE_END)])
        remaining = session.document.get_sketch(session.sketch_id)
        self.assertNotIn(line, {item.id for item in remaining.entities})
        self.assertIn(circle, {item.id for item in remaining.entities})
        self.assertTrue(all(target.entity != line for item in remaining.constraints for target in item.targets))

    def test_constraint_diagnostics_last_good_display_and_repair(self):
        session = Session(self.source, self.sketch_id)
        problem = session.document.get_sketch(self.sketch_id)
        line = problem.entities[0].id
        self.assertEqual(session.report.state, S.FULLY_CONSTRAINED)
        duplicate = session.add_constraint(K.HORIZONTAL, [ref(line)])
        self.assertEqual(session.report.state, S.REDUNDANT)
        self.assertTrue(session.report.redundant_constraints)
        session.erase_constraint(duplicate)
        self.assertEqual(session.report.state, S.FULLY_CONSTRAINED)
        good = geometry_values(session.display_problem)
        conflict = session.add_constraint(K.LENGTH, [ref(line)], 30)
        self.assertEqual(session.report.state, S.CONFLICTING)
        self.assertTrue(session.report.conflicting_constraints)
        self.assertTrue(session.error)
        self.assertIn(conflict, {item.id for item in session.document.get_sketch(self.sketch_id).constraints})
        self.assertEqual(geometry_values(session.display_problem), good)
        repaired = session.fork()
        repaired.erase_constraint(conflict)
        self.assertEqual(repaired.report.state, S.FULLY_CONSTRAINED)
        self.assertEqual(session.report.state, S.CONFLICTING, "Repairing a preview changed its baseline session")
        session.erase_constraint(conflict)
        self.assertEqual(session.report.state, S.FULLY_CONSTRAINED)
        self.assertFalse(session.error)
        self.assert_atomic_rejection(session, lambda: session.add_constraint(K.RADIUS, [ref(line)], 2))

    def test_point_drag_uses_temporary_constraints_and_cancel_preserves_baseline(self):
        session = Session(self.source, name="Free line")
        line = session.add_line((0, 0), (5, 2))
        self.assertEqual(session.report.state, S.UNDERCONSTRAINED)
        baseline = session.document.to_snapshot()
        ids = [item.id for item in session.document.get_sketch(session.sketch_id).constraints]
        preview = session.fork()
        preview.move_point(ref(line, E.LINE_END), (12, 7))
        problem = preview.document.get_sketch(preview.sketch_id)
        end = entity(problem, line).geometry.end
        self.assertAlmostEqual(end.x, 12, places=6)
        self.assertAlmostEqual(end.y, 7, places=6)
        self.assertEqual([item.id for item in problem.constraints], ids, "Drag guide constraints leaked into saved design intent")
        self.assertEqual(session.document.to_snapshot(), baseline)
        # Discarding a preview is cancellation; a later preview starts from the
        # same baseline rather than accumulating the abandoned pointer motion.
        other_preview = session.fork()
        other_preview.move_point(ref(line, E.LINE_END), (9, 3))
        self.assertAlmostEqual(entity(other_preview.display_problem, line).geometry.end.x, 9, places=6)
        self.assertEqual(session.document.to_snapshot(), baseline)
        fixed = Session(self.source, self.sketch_id)
        fixed_line = fixed.document.get_sketch(self.sketch_id).entities[0].id
        self.assert_atomic_rejection(fixed, lambda: fixed.move_point(ref(fixed_line, E.LINE_END), (37, 5)))

    def test_dimension_drag_edits_literals_or_shared_inputs_not_expressions(self):
        session = Session(self.source, self.sketch_id)
        problem = session.document.get_sketch(self.sketch_id)
        width = next(item.id for item in problem.constraints if item.kind == K.LENGTH)
        session.drag_dimension(width, 25)
        self.assertEqual(constraint(session.document.get_sketch(self.sketch_id), width).value, 25)
        self.assertAlmostEqual(session.display_problem.entities[0].geometry.end.x, 25, places=6)
        source = n.Document.from_snapshot(self.source.to_snapshot())
        source.add_parameter("shared_width", 20, "mm")
        source.add_parameter_expression("derived_width", "shared_width * 2", "mm")
        source.set_sketch_dimension(self.sketch_id, width, "shared_width")
        other = source.add_sketch("Other profile", commands.rectangle_profile(20, 7, "shared_width"))
        bound = Session(source, self.sketch_id)
        bound.drag_dimension(width, 30)
        self.assertEqual(next(item.value for item in bound.document.parameters if item.name == "shared_width"), 30)
        self.assertEqual(constraint(bound.document.get_sketch(self.sketch_id), width).parameter, "shared_width")
        other_width = next(item for item in bound.document.evaluated_sketch(other).constraints if item.kind == K.LENGTH)
        self.assertEqual(other_width.value, 30)
        self.assertEqual(next(item.value for item in source.parameters if item.name == "shared_width"), 20)
        bound.set_dimension(width, "derived_width")
        self.assertAlmostEqual(bound.display_problem.entities[0].geometry.end.x, 60, places=6)
        self.assert_atomic_rejection(bound, lambda: bound.drag_dimension(width, 65))
        bound.set_dimension(width, 40)
        self.assertIsNone(constraint(bound.document.get_sketch(self.sketch_id), width).parameter)
        self.assertAlmostEqual(bound.display_problem.entities[0].geometry.end.x, 40, places=6)

    def test_parallel_perpendicular_and_circle_dimensions_solve_geometrically(self):
        for kind in (K.PARALLEL, K.PERPENDICULAR):
            session = Session(self.source, name="Relations")
            first = session.add_line((0, 0), (4, 0.1))
            second = session.add_line((0, 2), (4, 2.5))
            session.add_constraint(kind, [ref(first), ref(second)])
            self.assertEqual(session.report.state, S.UNDERCONSTRAINED)
            a, b = (entity(session.display_problem, identifier).geometry for identifier in (first, second))
            ax, ay, bx, by = a.end.x - a.start.x, a.end.y - a.start.y, b.end.x - b.start.x, b.end.y - b.start.y
            residual = ax * by - ay * bx if kind == K.PARALLEL else ax * bx + ay * by
            self.assertAlmostEqual(residual / math.hypot(ax, ay) / math.hypot(bx, by), 0, places=6)
        for kind, value in ((K.RADIUS, 3), (K.DIAMETER, 6)):
            session = Session(self.source, name="Circle dimensions")
            circle = session.add_circle((3.1, 3.9), 2)
            session.add_constraint(kind, [ref(circle)], value)
            session.add_constraint(K.DISTANCE, [n.SketchReference(), ref(circle, E.CIRCLE_CENTER)], 5)
            session.add_constraint(K.DISTANCE_Y, [n.SketchReference(), ref(circle, E.CIRCLE_CENTER)], 4)
            self.assertEqual(session.report.state, S.FULLY_CONSTRAINED, session.error)
            geometry = entity(session.display_problem, circle).geometry
            self.assertAlmostEqual(geometry.center.x, 3, places=6)
            self.assertAlmostEqual(geometry.center.y, 4, places=6)
            self.assertAlmostEqual(geometry.radius, 3, places=6)


def scene_workflow(directory):
    """Confirm detached sketches, create/cut a body, then persist and exchange it."""
    scene = bpy.context.scene
    original = p.read_document(scene)
    original_body = original.features[0].body_id
    baseline = fingerprint()
    source = n.Document.from_snapshot(original.to_snapshot())
    source.add_parameter("stock_width", 30, "mm")
    source.add_parameter("hole_radius", 3, "mm")
    plane = n.SketchPlane()
    plane.origin = n.Vector3(100, 200, 300)
    plane.x_axis, plane.y_axis = n.Vector3(0, 1, 0), n.Vector3(0, 0, 1)
    session = Session(source, name="Interactive plate profile", plane=plane)
    lines = session.add_rectangle((0, 0), (30, 20), first_ref=n.SketchReference())
    width_constraint = session.add_constraint(K.LENGTH, [ref(lines[0])], parameter="stock_width")
    session.add_constraint(K.LENGTH, [ref(lines[1])], 20)
    assert session.report.state == S.FULLY_CONSTRAINED, session.error
    preview = session.fork()
    preview.drag_dimension(width_constraint, 34)
    assert next(item.value for item in session.document.parameters if item.name == "stock_width") == 30
    assert fingerprint() == baseline, "An unconfirmed sketch/drag changed the scene"
    profile_id = session.sketch_id
    commands.commit(scene, session.document)  # No modal owner is active in this focused check.
    base = commands.extrude_sketch(bpy.context, profile_id, 5)
    document = p.read_document(scene)
    body = document.get_feature(base).body_id
    assert body != original_body
    assert math.isclose(p.runtime(scene).evaluator.volume_mm3(document, body), 3000, rel_tol=1e-9)
    hole = Session(document, name="Interactive hole profile", plane=plane)
    circle = hole.add_circle((10, 10), 3)
    radius_constraint = hole.add_constraint(K.RADIUS, [ref(circle)], parameter="hole_radius")
    hole.add_constraint(K.DISTANCE_X, [n.SketchReference(), ref(circle, E.CIRCLE_CENTER)], 10)
    hole.add_constraint(K.DISTANCE_Y, [n.SketchReference(), ref(circle, E.CIRCLE_CENTER)], 10)
    assert hole.report.state == S.FULLY_CONSTRAINED, hole.error
    hole_id = hole.sketch_id
    commands.commit(scene, hole.document)
    cut = commands.extrude_sketch(bpy.context, hole_id, 5, input_feature_id=base)
    document = p.read_document(scene)
    assert document.get_feature(cut).body_id == body
    assert document.get_feature(cut).input_id == base
    expected = 3000 - 45 * math.pi
    assert math.isclose(p.runtime(scene).evaluator.volume_mm3(document, body), expected, rel_tol=1e-9)
    assert all(item.state == n.EvaluationState.UP_TO_DATE for item in p.runtime(scene).report.features)
    step_path = str(Path(directory) / "interactive-sketch-part.step")
    p.runtime(scene).evaluator.write_step(document, body, step_path)
    kernel = n.new_geometry_adapter()
    exchanged = kernel.read_step(step_path)
    assert math.isclose(kernel.volume_mm3(exchanged), expected, rel_tol=1e-8)
    snapshot = scene.blendercad_document_snapshot
    blend_path = str(Path(directory) / "interactive-sketch-part.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    scene = bpy.context.scene
    document = p.read_document(scene)
    assert scene.blendercad_document_snapshot == snapshot
    assert document.get_sketch(profile_id).plane.origin.x == 100
    assert constraint(document.get_sketch(profile_id), width_constraint).parameter == "stock_width"
    assert constraint(document.get_sketch(hole_id), radius_constraint).parameter == "hole_radius"
    assert math.isclose(p.runtime(scene).evaluator.volume_mm3(document, body), expected, rel_tol=1e-9)
    generation = p.runtime(scene).evaluator.tessellate(document, original_body)["geometry_generation"]
    commands.set_parameter(bpy.context, "stock_width", 40)
    document = p.read_document(scene)
    assert math.isclose(p.runtime(scene).evaluator.volume_mm3(document, body), 4000 - 45 * math.pi, rel_tol=1e-9)
    assert (p.runtime(scene).report.computed_count, p.runtime(scene).report.reused_count) == (2, 1)
    assert p.runtime(scene).evaluator.tessellate(document, original_body)["geometry_generation"] == generation
    assert document.get_sketch(profile_id).entities[0].geometry.end.x == 30, "Upstream edit rewrote saved construction guesses"
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message="Interactive sketch cut baseline")
    assert bpy.ops.blendercad.parameter(name="hole_radius", value=4) == {"FINISHED"}
    bpy.ops.ed.undo_push(message="Interactive sketch cut radius edit")
    assert math.isclose(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 4000 - 80 * math.pi, rel_tol=1e-9)
    assert bpy.ops.ed.undo() == {"FINISHED"}
    scene = bpy.context.scene
    assert math.isclose(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 4000 - 45 * math.pi, rel_tol=1e-9)
    assert bpy.ops.ed.redo() == {"FINISHED"}
    scene = bpy.context.scene
    assert math.isclose(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 4000 - 80 * math.pi, rel_tol=1e-9)
    print("Sketch editor scene workflow: arbitrary-plane construction, confirmed extrusion/cut, parameter edits, STEP, save/reopen and undo/redo passed")


with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, presentation as p
        from blendercad.sketch_commands import Session
        n = addon._native
        K, E, S = n.SketchConstraintKind, n.SketchElement, n.SketchSolveState
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.blendercad.new_document()
        bpy.ops.blendercad.add_rectangle_solid(width_mm=20, height_mm=10, depth_mm=5)
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SketchEditorChecks))
        if not result.wasSuccessful():
            raise SystemExit(1)
        scene_workflow(directory)
    finally:
        addon.unregister()
