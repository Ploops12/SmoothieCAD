"""Detached sketch-edit commands; no Blender objects or scene state are owned.

A session edits document definitions and solves disposable numeric copies. Solver
results replace saved geometry guesses only for deliberate point movement; normal
constraint/dimension edits retain the definition's original geometry guesses.
"""

from numbers import Real

from . import _native as n


def _clone_document(document):
    return n.Document.from_snapshot(document.to_snapshot())


def _copy_problem(problem):
    # Native setters copy plane, entity and constraint values; Python's copy /
    # deepcopy is not a serialization contract for these bound C++ objects.
    result = n.SketchProblem()
    result.plane = problem.plane
    result.entities = problem.entities
    result.constraints = problem.constraints
    return result


def _point(uv):
    if isinstance(uv, n.Point2):
        return n.Point2(uv.x, uv.y)
    try:
        u, v = uv
        return n.Point2(u, v)
    except (TypeError, ValueError) as error:
        raise ValueError("Sketch coordinates require two numeric plane-local values") from error


def _bad_report(report):
    return report.state in (n.SketchSolveState.CONFLICTING, n.SketchSolveState.FAILED)


def _diagnostic(report):
    return "; ".join(report.diagnostics) or f"Sketch solve is {report.state.name.lower()}"


class Session:
    """A reversible draft suitable for baseline.fork() mouse previews.

    Structural/native definition errors reject an operation atomically. A valid
    but currently unsolved definition remains editable, with its latest report
    and the preceding valid numeric display retained for diagnosis and repair.
    """

    def __init__(self, document, sketch_id="", name="Sketch", plane=None):
        if n is None:
            raise RuntimeError("The native CAD module is required to edit sketches")
        self.document = _clone_document(document)
        if sketch_id:
            problem = self.document.get_sketch(sketch_id)
            self.sketch_id = sketch_id
        else:
            problem = n.SketchProblem()
            if plane is not None:
                problem.plane = plane
            self.sketch_id = self.document.add_sketch(name, problem)
        self.display_problem = n.SketchProblem()
        self.display_problem.plane = problem.plane
        self.report = None
        self.error = ""
        self._accept(self.document)

    def fork(self):
        result = object.__new__(Session)
        result.document = _clone_document(self.document)
        result.sketch_id = self.sketch_id
        result.display_problem = _copy_problem(self.display_problem)
        result.report = self.report  # Native reports expose read-only fields.
        result.error = self.error
        return result

    def _solve(self, document):
        try:
            problem = document.evaluated_sketch(self.sketch_id)
            report = n.new_sketch_solver(document.geometric_tolerance_mm).solve(problem)
        except (ValueError, RuntimeError, OverflowError) as error:
            return None, None, f"Sketch {self.sketch_id}: {error}"
        if _bad_report(report):
            return None, report, f"Sketch {self.sketch_id}: {_diagnostic(report)}"
        return problem, report, ""

    def _accept(self, document):
        display, report, error = self._solve(document)
        self.document = document
        self.report = report
        self.error = error
        if display is not None:
            self.display_problem = display

    def _edit(self, operation, mutate):
        candidate = _clone_document(self.document)
        try:
            problem = candidate.get_sketch(self.sketch_id)
            result = mutate(problem)
            candidate.replace_sketch(self.sketch_id, problem)
        except (TypeError, ValueError, RuntimeError, OverflowError) as error:
            raise type(error)(f"{operation} in sketch {self.sketch_id}: {error}") from error
        self._accept(candidate)
        return result

    @staticmethod
    def _reference(entity_id, element):
        return n.SketchReference(entity_id, element)

    def add_line(self, start_uv, end_uv, start_ref=None, end_ref=None,
                 horizontal=False, vertical=False):
        def mutate(problem):
            line = n.SketchEntity(n.Line2(_point(start_uv), _point(end_uv)))
            constraints = problem.constraints
            for element, target in ((n.SketchElement.LINE_START, start_ref),
                                    (n.SketchElement.LINE_END, end_ref)):
                if target is not None:
                    constraints.append(n.SketchConstraint(n.SketchConstraintKind.COINCIDENT,
                        [self._reference(line.id, element), target]))
            whole = self._reference(line.id, n.SketchElement.GEOMETRY)
            if horizontal:
                constraints.append(n.SketchConstraint(n.SketchConstraintKind.HORIZONTAL, [whole]))
            if vertical:
                constraints.append(n.SketchConstraint(n.SketchConstraintKind.VERTICAL, [whole]))
            problem.entities = [*problem.entities, line]
            problem.constraints = constraints
            return line.id
        return self._edit("Add line", mutate)

    def add_circle(self, center_uv, radius, center_ref=None):
        def mutate(problem):
            circle = n.SketchEntity(n.Circle2(_point(center_uv), radius))
            problem.entities = [*problem.entities, circle]
            if center_ref is not None:
                center = self._reference(circle.id, n.SketchElement.CIRCLE_CENTER)
                problem.constraints = [*problem.constraints,
                    n.SketchConstraint(n.SketchConstraintKind.COINCIDENT, [center, center_ref])]
            return circle.id
        return self._edit("Add circle", mutate)

    def add_rectangle(self, first_uv, second_uv, first_ref=None):
        def mutate(problem):
            first, second = _point(first_uv), _point(second_uv)
            points = [first, n.Point2(second.x, first.y), second, n.Point2(first.x, second.y)]
            lines = [n.SketchEntity(n.Line2(points[index], points[(index + 1) % 4])) for index in range(4)]
            element, kind = n.SketchElement, n.SketchConstraintKind
            constraints = problem.constraints
            for index, line in enumerate(lines):
                end = self._reference(line.id, element.LINE_END)
                start = self._reference(lines[(index + 1) % 4].id, element.LINE_START)
                constraints.append(n.SketchConstraint(kind.COINCIDENT, [end, start]))
                whole = self._reference(line.id, element.GEOMETRY)
                constraints.append(n.SketchConstraint(kind.HORIZONTAL if index % 2 == 0 else kind.VERTICAL, [whole]))
            if first_ref is not None:
                start = self._reference(lines[0].id, element.LINE_START)
                constraints.append(n.SketchConstraint(kind.COINCIDENT, [start, first_ref]))
            problem.entities = [*problem.entities, *lines]
            problem.constraints = constraints
            return [line.id for line in lines]
        return self._edit("Add rectangle", mutate)

    def add_constraint(self, kind, targets, value=0, parameter=None):
        def mutate(problem):
            constraint = n.SketchConstraint(kind, list(targets), value, parameter=parameter)
            problem.constraints = [*problem.constraints, constraint]
            return constraint.id
        return self._edit("Add constraint", mutate)

    def _entity_for_reference(self, problem, reference, point_only=False):
        if not isinstance(reference, n.SketchReference):
            raise ValueError("Expected a native sketch reference")
        if not reference.entity or reference.element == n.SketchElement.ORIGIN:
            raise ValueError("The sketch origin cannot be moved or deleted")
        entity = next((item for item in problem.entities if item.id == reference.entity), None)
        if entity is None:
            raise ValueError(f"Unknown sketch entity {reference.entity}")
        if not point_only and reference.element == n.SketchElement.GEOMETRY:
            return entity
        geometry = entity.geometry
        if isinstance(geometry, n.Line2) and reference.element in (
                n.SketchElement.LINE_START, n.SketchElement.LINE_END):
            return entity
        if isinstance(geometry, n.Circle2) and reference.element == n.SketchElement.CIRCLE_CENTER:
            return entity
        raise ValueError("Reference must identify a compatible line endpoint or circle center")

    def erase_targets(self, refs):
        def mutate(problem):
            removed = {self._entity_for_reference(problem, reference).id for reference in refs}
            problem.entities = [entity for entity in problem.entities if entity.id not in removed]
            problem.constraints = [constraint for constraint in problem.constraints
                if not any(target.entity in removed for target in constraint.targets)]
        return self._edit("Delete geometry", mutate)

    def erase_constraint(self, constraint_id):
        def mutate(problem):
            constraints = problem.constraints
            if not any(constraint.id == constraint_id for constraint in constraints):
                raise ValueError(f"Unknown sketch constraint {constraint_id}")
            problem.constraints = [constraint for constraint in constraints if constraint.id != constraint_id]
        return self._edit("Delete constraint", mutate)

    def set_dimension(self, constraint_id, value):
        candidate = _clone_document(self.document)
        try:
            candidate.set_sketch_dimension(self.sketch_id, constraint_id, value)
        except (ValueError, RuntimeError, OverflowError) as error:
            raise type(error)(f"Set dimension {constraint_id} in sketch {self.sketch_id}: {error}") from error
        self._accept(candidate)

    def move_point(self, ref, target_uv):
        if self.report is None or self.error or _bad_report(self.report):
            raise ValueError(f"Cannot move a point in unsolved sketch {self.sketch_id}; repair its dimensions/constraints first")
        try:
            problem = self.document.evaluated_sketch(self.sketch_id)
            self._entity_for_reference(problem, ref, point_only=True)
            target = _point(target_uv)
            # Seed from the current valid solution to preserve its branch while
            # dragging; ordinary refresh never changes saved definition guesses.
            problem.entities = self.display_problem.entities
            kind = n.SketchConstraintKind
            origin = n.SketchReference()
            problem.constraints = [*problem.constraints,
                n.SketchConstraint(kind.DISTANCE_X, [origin, ref], target.x),
                n.SketchConstraint(kind.DISTANCE_Y, [origin, ref], target.y)]
            report = n.new_sketch_solver(self.document.geometric_tolerance_mm).solve(problem)
            if _bad_report(report):
                raise ValueError(_diagnostic(report))
            candidate = _clone_document(self.document)
            raw = candidate.get_sketch(self.sketch_id)
            raw.entities = problem.entities
            # Original permanent constraints/bindings remain untouched; the two
            # temporary drag constraints exist only in the numeric solver copy.
            candidate.replace_sketch(self.sketch_id, raw)
        except (ValueError, RuntimeError, OverflowError) as error:
            raise type(error)(f"Move point in sketch {self.sketch_id}: {error}") from error
        self._accept(candidate)

    def drag_dimension(self, constraint_id, new_value):
        if isinstance(new_value, bool) or not isinstance(new_value, Real):
            raise ValueError("Dimension dragging requires a numeric value, not a parameter binding")
        raw = self.document.get_sketch(self.sketch_id)
        constraint = next((item for item in raw.constraints if item.id == constraint_id), None)
        if constraint is None:
            raise ValueError(f"Unknown dimension constraint {constraint_id} in sketch {self.sketch_id}")
        if constraint.parameter is None:
            return self.set_dimension(constraint_id, float(new_value))
        parameter = next((item for item in self.document.parameters if item.name == constraint.parameter), None)
        if parameter is None:
            raise ValueError(f"Dimension {constraint_id} refers to unknown parameter '{constraint.parameter}'")
        if parameter.expression is not None:
            raise ValueError(f"Cannot drag dimension {constraint_id}: parameter '{parameter.name}' is expression-defined; edit its expression or upstream inputs")
        candidate = _clone_document(self.document)
        try:
            candidate.set_parameter_value(parameter.name, float(new_value))
        except (ValueError, RuntimeError, OverflowError) as error:
            raise type(error)(f"Drag dimension {constraint_id}, parameter '{parameter.name}': {error}") from error
        self._accept(candidate)
