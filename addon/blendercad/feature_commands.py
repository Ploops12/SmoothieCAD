"""Detached feature definitions and speculative native evaluation.

The document owns every dimension and reference. This module neither imports
Blender nor owns scene objects: preview arrays are disposable evaluated output,
and publication remains the Blender command adapter's responsibility.
"""

from dataclasses import dataclass
import math
from numbers import Real

from . import _native as n


@dataclass(frozen=True)
class Operation:
    kind: str
    sketch_id: str = ""
    input_id: str = ""
    edges: tuple = ()
    feature_id: str = ""

    def __post_init__(self):
        if self.kind not in {"extrude", "cut", "fillet", "depth", "radius"}:
            raise ValueError("Unsupported CAD feature operation")
        object.__setattr__(self, "edges", tuple(self.edges))


def _clone(document):
    return n.Document.from_snapshot(document.to_snapshot())


def create_feature(document, operation, value):
    """Apply one shared creation definition, without evaluating or publishing."""
    if document.rollback_before is not None:
        raise ValueError("Return to the end of history before creating a feature")
    if operation.kind == "extrude":
        return document.add_extrusion("Sketch extrusion", operation.sketch_id, value)
    if operation.kind == "cut":
        return document.add_subtractive_extrusion("Sketch cut", operation.input_id,
                                                 operation.sketch_id, value)
    if operation.kind == "fillet":
        return document.add_fillet("Fillet", operation.input_id, list(operation.edges), value)
    raise ValueError("Choose an extrusion, cut or fillet to create a feature")


def set_feature_input(document, feature_id, value):
    """Apply the feature's depth/radius definition through the native document."""
    feature = document.get_feature(feature_id)
    if feature.type == "fillet":
        document.set_fillet_radius(feature_id, value)
    elif feature.type in {"extrusion", "subtractive_extrusion"}:
        document.set_feature_depth(feature_id, value)
    else:
        raise ValueError("This CAD feature has no supported length input")


def _input(document, feature_id):
    feature = document.get_feature(feature_id)
    return feature.radius if feature.type == "fillet" else feature.depth


def _current_feature(report, identifier):
    return next((item for item in report.features if item.feature_id == identifier), None)


def _current_body(report, identifier):
    return next((item for item in report.bodies if item.body_id == identifier), None)


class Session:
    """A detached feature transaction retaining its last valid target display.

    Native structural errors reject mutations atomically. Well-formed candidates
    with failed geometry remain in ``document`` with ``valid == False`` while
    ``arrays`` and ``display_document`` retain their preceding valid source.
    Failed candidates cannot be mistaken for confirmable current results.
    """

    def __init__(self, document, evaluator, operation, value=None):
        if n is None:
            raise RuntimeError("The native CAD module is required to preview features")
        if not isinstance(operation, Operation):
            raise TypeError("Feature previews require an Operation definition")
        if not evaluator.matches_document(document):
            raise ValueError("CAD document changed; recompute before previewing a feature")
        self.operation = operation
        self.document = _clone(document)
        self.evaluator = evaluator.fork()
        self.report = self.evaluator.evaluate(self.document)
        self._baseline_states = {item.feature_id: item.state for item in self.report.features}
        self.arrays = None
        self.display_document = None
        self.error = ""
        self.valid = False

        if operation.kind in {"depth", "radius"}:
            feature = self.document.get_feature(operation.feature_id)
            expected_types = {"fillet"} if operation.kind == "radius" else {"extrusion", "subtractive_extrusion"}
            if feature.type not in expected_types:
                raise ValueError("Choose a matching depth or radius feature input")
            result = _current_feature(self.report, feature.id)
            if result is None or result.state != n.EvaluationState.UP_TO_DATE:
                raise ValueError("Feature preview requires a current available feature")
            self.feature_id = feature.id
            self.body_id = feature.body_id
            body = _current_body(self.report, self.body_id)
            if body is None or body.state != n.EvaluationState.UP_TO_DATE:
                raise ValueError("Feature preview requires a current valid Solid")
        else:
            if value is None:
                raise ValueError("Choose a depth or radius for the new feature")
            if operation.input_id:
                result = _current_feature(self.report, operation.input_id)
                if result is None or result.state != n.EvaluationState.UP_TO_DATE:
                    raise ValueError("Feature preview requires a current available input feature")
            self.feature_id = create_feature(self.document, operation, value)
            self.body_id = self.document.get_feature(self.feature_id).body_id

        # Editing or extending a body can start with its current committed result
        # as the last valid display, even when the first candidate cannot solve.
        body = _current_body(self.report, self.body_id)
        if body is not None and body.state == n.EvaluationState.UP_TO_DATE:
            source = _clone(document)
            self.arrays = self.evaluator.tessellate(source, self.body_id)
            self.display_document = source
        if operation.kind in {"depth", "radius"} and value is not None:
            set_feature_input(self.document, self.feature_id, value)
        self._accept(self.document)

    @property
    def input(self):
        return _input(self.document, self.feature_id)

    @property
    def value_mm(self):
        return self.document.length_value(self.input)

    def fork(self):
        """Copy an exact drag baseline without replaying definition mutations."""
        result = object.__new__(Session)
        result.operation = self.operation
        result.document = _clone(self.document)
        result.evaluator = self.evaluator.fork()
        result.report = self.report  # Bound reports expose read-only fields.
        result._baseline_states = self._baseline_states.copy()
        result.feature_id, result.body_id = self.feature_id, self.body_id
        result.arrays = self.arrays  # Disposable arrays are never mutated here.
        result.display_document = _clone(self.display_document) if self.display_document is not None else None
        result.error, result.valid = self.error, self.valid
        return result

    def _diagnostic(self, report):
        feature = _current_feature(report, self.feature_id)
        if feature is None or feature.state != n.EvaluationState.UP_TO_DATE:
            return (feature.diagnostic or "Feature has no current valid result") if feature else "Feature is unavailable"
        body = _current_body(report, self.body_id)
        if body is None or body.state != n.EvaluationState.UP_TO_DATE:
            failed = next((item for item in report.features
                           if item.body_id == self.body_id and item.state in
                           {n.EvaluationState.FAILED, n.EvaluationState.BLOCKED}), None)
            return (failed.diagnostic if failed is not None else "") or "Solid has no current valid result"
        for item in report.features:
            if (item.state in {n.EvaluationState.FAILED, n.EvaluationState.BLOCKED}
                    and self._baseline_states.get(item.feature_id) != item.state):
                return item.diagnostic or "Preview would invalidate another dependent CAD feature"
        return ""

    def _accept(self, document):
        evaluator = self.evaluator.fork()
        report = None
        arrays = None
        error = ""
        try:
            report = evaluator.evaluate(document)
            error = self._diagnostic(report)
            if not error:
                arrays = evaluator.tessellate(document, self.body_id)
                if not arrays["current"]:
                    error = "Feature preview produced a stale display instead of a current result"
        except (ValueError, RuntimeError, TypeError, OverflowError) as failure:
            error = str(failure) or type(failure).__name__
        self.document, self.evaluator, self.report = document, evaluator, report
        self.error, self.valid = error, not error
        if self.valid:
            self.arrays = arrays
            self.display_document = _clone(document)

    def set_input(self, value):
        """Explicitly replace the literal/parameter binding of this feature."""
        candidate = _clone(self.document)
        set_feature_input(candidate, self.feature_id, value)
        self._accept(candidate)

    def set_numeric(self, value):
        """Adjust a literal driver without silently severing parameter intent."""
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError("Feature length must be finite numeric millimetres")
        candidate = _clone(self.document)
        current = self.input
        if isinstance(current, str):
            parameter = next(item for item in candidate.parameters if item.name == current)
            if parameter.expression is not None:
                raise ValueError(f"Parameter '{current}' is expression-defined; edit its expression or upstream inputs")
            candidate.set_parameter_value(current, float(value))
        else:
            set_feature_input(candidate, self.feature_id, float(value))
        self._accept(candidate)
