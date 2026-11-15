# Parameter-driven sketch dimensions

## Definition, resolution and solving

A dimensional sketch constraint stores either a literal in millimetres or a
named length parameter. Supported kinds are length, point distance, radius,
diameter and signed X/Y point distances. Coincidence, horizontal/vertical and
parallel/perpendicular constraints do not take a parameter binding. Angle and
unitless parameters are rejected as inputs to these length dimensions.

The parameter may itself be defined by an [expression](parameter-expressions.md).
This gives a directed chain from named definitions to numeric sketch dimensions,
then simultaneous PlaneGCS solving, then feature evaluation. The geometric solver
does not change the upstream parameter or solve an expression backwards. There
is no dependency from a measured B-rep result back into the parameter graph.

`SketchConstraint.parameter` is an optional parameter name. On a bound saved
definition, its native `value` field is normalized to zero and is not another
dimension, guess or fallback. It is omitted from the snapshot. Unbound
constraints retain their existing literal `value`. Geometry coordinates remain
saved starting guesses, not a competing evaluated shape. Recomputing does not
rewrite those guesses or assign new sketch/entity/constraint UUIDs.

`Document.evaluated_sketch(sketch_id)` returns an owned numeric copy: it resolves
the parameter names, substitutes canonical millimetre values, clears the bindings
and validates the resulting numeric problem. The standalone sketch solver has
no document dependency and rejects unresolved bindings. Use the evaluated copy:

```python
from blendercad import _native as cad

# document already owns the sketch and its named parameters
problem = document.evaluated_sketch(sketch_id)
report = cad.new_sketch_solver(document.geometric_tolerance_mm).solve(problem)
```

The feature evaluator obtains that same numeric copy before comparing its cache
key. It reuses geometry when the relevant resolved dimensions/other inputs are
unchanged, even if an unrelated parameter changed. Changed profile dimensions
invalidate their consumers and dependent feature results; independent bodies can
reuse their geometry. Construction provenance continues to use the original
entity UUIDs, never the position in a solved/display array.

## Editing and failure behavior

`Document.set_sketch_dimension(sketch_id, constraint_id, value)` accepts a finite
literal number or a named millimetre parameter string. The operation preserves
constraint identity and publishes an atomic candidate. Unknown sketches,
constraints or parameters, incompatible units, and geometric constraint targets
reject without changing definitions/revision. Identical definitions are no-ops;
switching a binding to an equal numeric literal still changes intent. The
existing revision-overflow guard applies before publication. A later upstream
edit no longer drives a constraint whose binding was explicitly replaced by a
literal.

Literal constraints retain their domain rules: length/radius/diameter must be
positive, point distance nonnegative, and signed X/Y distances may be negative.
A well-formed parameter binding may later resolve to an invalid range, or to a
value conflicting with other sketch constraints. It remains a repairable saved
definition. Evaluation fails with a contextual diagnostic, retains last-valid
display and blocks dependent features; measurement and STEP export cannot use
that stale display as the current result. Fixing the parameter can recover the
feature chain without replacing its definition identities. Numerical solver
failure/conflict/redundancy remains distinct from definition validation.

All copies/solving/geometry evaluation use the document's modelling error budget;
this feature does not change that budget or display tessellation tolerance.
Plane placement remains arbitrary orthonormal 3D, with planar 2D constraints.

## Blender workflow

**Add Rectangle Solid** accepts optional width/height parameter names alongside
numeric dimensions. Bound names supply the starting size and persistent length
constraints; numeric fields are disabled when a name is provided. The depth can
subsequently reference a parameter through its existing depth dialog.

Select a Solid to see the sketches used by its feature chain in the sidebar.
Their dimensional constraints show literal values or parameter names with their
resolved values. **Edit** opens **Edit Sketch Dimension**; leave the parameter
field empty for a literal or enter an existing named length parameter. Line/
circle numbers in these labels are presentation labels only, not saved identity.
Available solver states and degrees of freedom are displayed alongside the
dimensions. These controls call `commands.set_sketch_dimension`, using the same
native command, speculative evaluation and transactional mesh publication as
numeric and parameter editors. Confirmation is one Blender undoable operation;
cancelling the dialog does not publish a candidate.

Duplicating a Solid remaps sketch/entity/constraint/feature/body UUIDs but retains
the named parameter bindings. The copies are independent definitions sharing
document-level parameters, not assembly instances. Neither duplicate acquires
the other's saved reference bookmarks. Edit a binding to a literal or another
parameter to make its dimension independent.

## Persistence and verification

Document schema **6** adds bound dimensions. Numeric-only sketches retain
version **1** and numeric constraint records. A sketch with bindings emits
version **2**, where a constraint has either `{id, type, targets, value}` or
`{id, type, targets, parameter}`, never both. Version-1 sketches cannot contain
bindings, and schemas 1–5 cannot contain version-2 sketches. Removing the last
binding emits the equivalent numeric-only version-1 encoding again. Schema-5
parameter expressions remain supported unchanged. Loading older definitions
migrates in memory without rewriting the original scene snapshot; future or
invalid records remain untouched for diagnosis.

`cmake --build build --target check` includes `native_sketch_parameters` and
`blender_sketch_parameters`: numeric resolution, copy lifetimes, every dimensional
kind, domain/unit rejection, snapshot alternatives/versions, no-op/overflow,
reference preservation, selective recomputation, blocked dependents, last-valid
failure/recovery, arbitrary planes, analytic volume/radius, duplication,
save/reopen and undo/redo. `check_solid_ui` confirms/cancels the real dimension
dialog in a Blender window. The complementary [interactive editor](interactive-sketches.md)
now provides viewport drawing, constraint selection and point/dimension dragging
through these same definitions. Complete mechanical-bracket acceptance remains
pending.
