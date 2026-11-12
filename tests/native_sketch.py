"""PlaneGCS adapter acceptance without Blender or solver-owned saved entities."""

import argparse
import math
from pathlib import Path
import sys
import unittest

arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
parser = argparse.ArgumentParser()
parser.add_argument("--module-dir", required=True)
options = parser.parse_args(arguments)
sys.path.insert(0, str(Path(options.module_dir).resolve()))
import _native as n

K = n.SketchConstraintKind
E = n.SketchElement
S = n.SketchSolveState


def reference(entity=None, element=E.GEOMETRY):
    return n.SketchReference(entity.id, element) if entity else n.SketchReference()


def constraint(kind, targets, value=0):
    return n.SketchConstraint(kind, targets, value)


def rectangle():
    coordinates = [(0.1, -0.1, 20.2, 0.2), (20, 0, 20.3, 10.2),
                   (20, 10, -0.1, 10.1), (0, 10, 0.2, 0.1)]
    lines = [n.SketchEntity(n.Line2(n.Point2(a, b), n.Point2(c, d))) for a, b, c, d in coordinates]
    constraints = [constraint(K.COINCIDENT, [reference(lines[i], E.LINE_END),
                                            reference(lines[(i + 1) % 4], E.LINE_START)]) for i in range(4)]
    constraints += [constraint(K.HORIZONTAL if i % 2 == 0 else K.VERTICAL, [reference(line)])
                    for i, line in enumerate(lines)]
    constraints += [constraint(K.LENGTH, [reference(lines[0])], 20),
                    constraint(K.LENGTH, [reference(lines[1])], 10),
                    constraint(K.COINCIDENT, [reference(lines[0], E.LINE_START), reference()])]
    problem = n.SketchProblem()
    problem.entities = lines
    problem.constraints = constraints
    return problem


def geometry_values(problem):
    values = []
    for entity in problem.entities:
        geometry = entity.geometry
        if isinstance(geometry, n.Line2):
            values.extend([geometry.start.x, geometry.start.y, geometry.end.x, geometry.end.y])
        else:
            values.extend([geometry.center.x, geometry.center.y, geometry.radius])
    return values


class SketchChecks(unittest.TestCase):
    def setUp(self):
        self.solver = n.new_sketch_solver()

    def test_rectangle_and_repeated_solve(self):
        for iteration in range(20):
            problem = rectangle()
            problem.plane.origin = n.Vector3(10, 20, 30)
            problem.plane.x_axis = n.Vector3(0, 1, 0)
            problem.plane.y_axis = n.Vector3(0, 0, 1)
            report = self.solver.solve(problem)
            self.assertEqual(report.state, S.FULLY_CONSTRAINED, report.diagnostics)
            self.assertEqual(report.remaining_degrees_of_freedom, 0)
            expected = [0, 0, 20, 0, 20, 0, 20, 10, 20, 10, 0, 10, 0, 10, 0, 0]
            for actual, wanted in zip(geometry_values(problem), expected):
                self.assertAlmostEqual(actual, wanted, places=6)
            self.assertEqual(problem.plane.origin.z, 30)

    def test_underconstrained_and_empty(self):
        problem = n.SketchProblem()
        self.assertEqual(self.solver.solve(problem).state, S.FULLY_CONSTRAINED)
        line = n.SketchEntity(n.Line2(n.Point2(0, 0), n.Point2(3, 1)))
        problem.entities = [line]
        self.assertEqual(self.solver.solve(problem).remaining_degrees_of_freedom, 4)
        problem.constraints = [constraint(K.HORIZONTAL, [reference(line)])]
        report = self.solver.solve(problem)
        self.assertEqual(report.state, S.UNDERCONSTRAINED)
        self.assertEqual(report.remaining_degrees_of_freedom, 3)
        self.assertAlmostEqual(problem.entities[0].geometry.start.y, problem.entities[0].geometry.end.y, places=6)

    def test_circle_diameter_signed_coordinates_and_radius(self):
        for dimensional_kind, value in ((K.DIAMETER, 10), (K.RADIUS, 5)):
            circle = n.SketchEntity(n.Circle2(n.Point2(4, -6), 4))
            problem = n.SketchProblem()
            problem.entities = [circle]
            problem.constraints = [constraint(K.DISTANCE_X, [reference(), reference(circle, E.CIRCLE_CENTER)], 5),
                                   constraint(K.DISTANCE_Y, [reference(), reference(circle, E.CIRCLE_CENTER)], -7),
                                   constraint(dimensional_kind, [reference(circle)], value)]
            report = self.solver.solve(problem)
            self.assertEqual(report.state, S.FULLY_CONSTRAINED, report.diagnostics)
            self.assertAlmostEqual(problem.entities[0].geometry.center.x, 5, places=6)
            self.assertAlmostEqual(problem.entities[0].geometry.center.y, -7, places=6)
            self.assertAlmostEqual(problem.entities[0].geometry.radius, 5, places=6)

    def test_euclidean_point_distance(self):
        circle = n.SketchEntity(n.Circle2(n.Point2(3.1, 3.9), 1))
        problem = n.SketchProblem()
        problem.entities = [circle]
        problem.constraints = [constraint(K.DISTANCE, [reference(), reference(circle, E.CIRCLE_CENTER)], 5),
                               constraint(K.DISTANCE_Y, [reference(), reference(circle, E.CIRCLE_CENTER)], 4),
                               constraint(K.RADIUS, [reference(circle)], 1)]
        report = self.solver.solve(problem)
        self.assertEqual(report.state, S.FULLY_CONSTRAINED, report.diagnostics)
        self.assertAlmostEqual(problem.entities[0].geometry.center.x, 3, places=6)

    def test_parallel_and_perpendicular(self):
        for kind in (K.PARALLEL, K.PERPENDICULAR):
            first = n.SketchEntity(n.Line2(n.Point2(0, 0), n.Point2(4, 0.1)))
            second = n.SketchEntity(n.Line2(n.Point2(0, 2), n.Point2(4, 2.5)))
            problem = n.SketchProblem()
            problem.entities = [first, second]
            problem.constraints = [constraint(kind, [reference(first), reference(second)])]
            report = self.solver.solve(problem)
            self.assertEqual(report.state, S.UNDERCONSTRAINED, report.diagnostics)
            a, b = (entity.geometry for entity in problem.entities)
            ax, ay = a.end.x - a.start.x, a.end.y - a.start.y
            bx, by = b.end.x - b.start.x, b.end.y - b.start.y
            error = ax * by - ay * bx if kind == K.PARALLEL else ax * bx + ay * by
            self.assertAlmostEqual(error / math.hypot(ax, ay) / math.hypot(bx, by), 0, places=7)

    def test_redundancy_and_conflict_ids(self):
        problem = rectangle()
        duplicate = constraint(K.HORIZONTAL, [reference(problem.entities[0])])
        problem.constraints = problem.constraints + [duplicate]
        report = self.solver.solve(problem)
        self.assertEqual(report.state, S.REDUNDANT, report.diagnostics)
        self.assertTrue(report.redundant_constraints)
        self.assertTrue(set(report.redundant_constraints) <= {item.id for item in problem.constraints})
        problem = rectangle()
        conflict = constraint(K.LENGTH, [reference(problem.entities[0])], 30)
        problem.constraints = problem.constraints + [conflict]
        before = geometry_values(problem)
        report = self.solver.solve(problem)
        self.assertEqual(report.state, S.CONFLICTING, report.diagnostics)
        self.assertTrue(report.conflicting_constraints)
        self.assertEqual(geometry_values(problem), before)

    def test_invalid_definitions_do_not_mutate(self):
        problem = rectangle()
        before = geometry_values(problem)
        problem.constraints = [constraint(K.RADIUS, [reference(problem.entities[0])], 2)]
        with self.assertRaises(ValueError):
            self.solver.solve(problem)
        self.assertEqual(geometry_values(problem), before)
        problem.constraints = [constraint(K.COINCIDENT, [reference(problem.entities[0]), reference()])]
        with self.assertRaises(ValueError):
            self.solver.solve(problem)
        problem.constraints = []
        problem.entities = [problem.entities[0], problem.entities[0]]
        with self.assertRaises(ValueError):
            self.solver.solve(problem)

    def test_infeasible_nonlinear_solve_preserves_input(self):
        circle = n.SketchEntity(n.Circle2(n.Point2(2, 1), 1))
        problem = n.SketchProblem()
        problem.entities = [circle]
        problem.constraints = [constraint(K.RADIUS, [reference(circle)], 1),
                               constraint(K.DISTANCE, [reference(), reference(circle, E.CIRCLE_CENTER)], 1),
                               constraint(K.DISTANCE_X, [reference(), reference(circle, E.CIRCLE_CENTER)], 2)]
        before = geometry_values(problem)
        report = self.solver.solve(problem)
        self.assertEqual(report.state, S.FAILED, report.diagnostics)
        self.assertTrue(report.diagnostics)
        self.assertEqual(geometry_values(problem), before)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
