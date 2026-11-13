# Directed, unit-aware parameter expressions

## Definitions and evaluation

The document owns named parameters. A parameter has a declared unit (`mm`,
`deg` or `unitless`) and exactly one definition: a finite literal value, or an
arithmetic expression. `Parameter.value` is its resolved double-precision value
in the declared canonical unit. `Parameter.expression` is the original string
or `None`; it is not Python code and is never passed to Python `eval`.

`parameter_expression.cpp` owns parsing, dimensional arithmetic and the directed
dependency graph. It has no Blender, OCCT or PlaneGCS dependency. This graph is
distinct from simultaneous sketch constraint solving: a parameter reads other
parameters, but cannot solve an equation backwards or depend on measured output
geometry. A feature can use the resulting named length for its extrusion depth.
Binding sketch constraint values to parameters remains a subsequent capability.

The native APIs are `add_parameter(name, value, unit)`,
`add_parameter_expression(name, expression, unit)`, `set_parameter_value(name,
value)` and `set_parameter_expression(name, expression)`. Existing parameter
units cannot be changed through these edits. Setting a literal replaces an
expression, even if its current numeric value is identical. Editing the same
definition is a no-op; changing its text advances the content revision even
when the numeric result happens to be unchanged.

All edits resolve a detached candidate graph before publication. Missing
references, self-dependencies/cycles, syntax errors, incompatible dimensions,
division by zero and nonfinite results reject the entire edit with an owning
parameter diagnostic. Neither upstream literals nor downstream resolved values
change on rejection. Accepted edits update dependent values together and advance
the revision once, subject to the existing overflow guard. Snapshot restoration
supports forward references regardless of parameter list order. Parameter names
are unique, case-sensitive references; renaming/deleting parameters is not yet
exposed, so there is no implicit rename or substitution policy.

## Expression language

Supported operations are parentheses, unary `+`/`-`, binary `+`, `-`, `*` and
`/`, with ordinary precedence and left associativity. Decimal/scientific numeric
literals are unitless unless followed by a supported unit suffix:

| Suffix | Canonical value |
| --- | --- |
| `mm` | millimetres |
| `cm` | 10 millimetres |
| `m` | 1000 millimetres |
| `um` | 0.001 millimetres |
| `deg` | degrees |
| `rad` | 180 / π degrees |

Bare reference names use ASCII letters/underscores followed by letters,
underscores or digits. Double-quoted references support existing names containing
spaces or Unicode; inside quotes, `\"` and `\\` escape a quote or backslash.
Names are not silently normalized or coerced. Unit words are suffixes after
numbers and may still be ordinary parameter names elsewhere.

Examples for a length parameter:

```text
thickness * 2 + 1 mm
(width * height) / width
"plate width" / 4
2.5 cm + 250 um
```

Length and angle are distinct dimensions, and unitless scalars are not angles.
Addition/subtraction require matching dimensions. Multiplication/division combine
length/angle exponents, permitting compound intermediate quantities and
cancellation; the final dimension must match the parameter's declared unit.
`width + 2` is not an implicit millimetre conversion: use `width + 2 mm`.
`width / height` can define a unitless ratio. Adding an angle to a length or
assigning an area to a length is rejected. Unit conversions do not alter the
document's geometric tolerance budget.

Functions, exponentiation, comparisons, implicit multiplication, conditionals,
external state, object attributes and arbitrary code are intentionally absent.
Expression length is bounded at 65,536 bytes, syntax nesting at 128 and dependency
chains at 512 parameters; oversized definitions fail rather than consuming
unbounded parser stack. The graph traversal/evaluation is iterative. Arithmetic uses
ordinary finite double precision, not symbolic algebra or an exact arithmetic
system. Division is checked against zero, not a modelling tolerance.

## Blender commands and persistence

**Set CAD Parameter** can create a parameter with a declared unit or edit an
existing one. Enable **Use expression** to enter arithmetic. Existing parameters
have individual **Edit** buttons and show both the expression and resolved value
in the sidebar. The depth dialog still references a named length parameter;
put arithmetic in that parameter rather than interpreting depth strings as two
different languages. Numeric and expression edits use the same `commands.py`
transaction, speculative evaluator and scene journal as other CAD edits.

Schema **5** saves literals as `{name, value, unit}` and expressions as
`{name, expression, unit}`. No resolved expression value, parser program,
dependency traversal or solver/kernel pointer is serialized. Restoring recomputes
the graph; a supplied `value` alongside `expression` is invalid, not a trusted
cache. Schemas 1–4 migrate in memory with their literal definitions unchanged.
Loading alone never rewrites an old or unsupported-future scene snapshot.

Feature caches use the resolved depth as part of the input key. An upstream edit
reevaluates affected feature geometry when its resulting depth changes; an
unrelated parameter edit can reuse existing native geometry. Current-result
guards still compare complete document content, not revision numbers alone.
Geometry failures such as a negative extrusion depth remain feature diagnostics
with last-valid presentation, separate from invalid expression definitions.

## Verification

`cmake --build build --target check` packages the addon and includes
`native_parameters` and `blender_parameters`. They exercise arithmetic/units,
dependency updates, rejection atomicity, cycles, syntax and numerical failures,
snapshot migration/validation, revision guards, feature cache behavior and
Blender command persistence. These checks do not establish interactive sketch
dimension dragging or a full bracket workflow.
The optional `check_solid_ui` CMake target confirms expression creation and edit
cancellation through a real Blender dialog/event loop.
