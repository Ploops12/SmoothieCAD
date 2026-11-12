# Document-owned sketches and snapshots

## Ownership and interfaces

`src/blendercad/sketch.hpp` defines solver-neutral sketch data. `Document` owns ordered `SketchDefinition` records containing an immutable sketch UUID, a display name, a 3D plane, line/circle entities and constraint definitions. Entity and constraint UUIDs are stable values, not container indices or solver handles. IDs are unique across the document, sketches, entities and constraints; display names are not identities and may repeat.

The core exposes `add_sketch`, `sketch`, `replace_sketch` and `rename_sketch`. The Python binding exposes `sketches` and `get_sketch` as **copies**, with explicit replacement to commit an edit. Transient sketch vector/variant getters also return owned copies so they cannot dangle after container replacement. For example, inside Blender with the addon installed:

```python
from blendercad import _native as cad

document = cad.Document()
problem = cad.SketchProblem()
problem.entities = [cad.SketchEntity(cad.Line2(cad.Point2(0, 0), cad.Point2(10, 0)))]
sketch_id = document.add_sketch("Profile", problem)

candidate = document.get_sketch(sketch_id)
entities = candidate.entities
line = entities[0].geometry
line.end.x = 20
entities[0].geometry = line
candidate.entities = entities
document.replace_sketch(sketch_id, candidate)
```

This edits the native design definition only; Blender drawing tools and scene command transactions are not implemented by this example. Solving a detached problem does not change the document unless the caller explicitly replaces it.

## Validation and publication

`validate_sketch` is shared by document storage and the PlaneGCS adapter and does not depend on either Blender or solver objects. It checks finite values, positive line length/circle radius, orthonormal plane axes (1e-10 numerical frame threshold), typed local targets, target counts, dimensional domains and unique identities. Non-dimensional geometric constraints must use a zero `value`. Distances and coordinates are in mm; signed X/Y dimensions mean second point minus first. Sketch coordinates remain planar even when the plane occupies arbitrary 3D space; this is not free-space spatial constraint solving.

Structural validity is different from solver feasibility. Well-formed redundant/conflicting constraints can be stored and reopened for repair without loading silently solving or changing them. Geometry evaluation and diagnostics remain separate responsibilities. There are no process pointers, evaluated OCCT shapes, solver tags, constraint diagnostics or display indices in the snapshot.

Sketch edits validate a candidate collection before replacing document storage. Rejected edits leave both definitions and revision unchanged. Actual accepted changes advance the document's uint64 content revision once; no-op replacements/renames and unchanged numeric edits do not. Revision exhaustion rejects changes before mutation instead of wrapping and permitting stale cache keys. Native document mutation is not a thread-safe or Blender-undo API; a command/evaluation layer must own transactions and revision-aware publication.

## Schema 2

The binding maps typed core records to JSON-compatible values; Blender's storage adapter uses Python's standard JSON encoder. This avoids a second JSON dependency in geometry/core code and keeps scene storage replaceable. All supported fields are required, unknown fields are rejected, and numeric snapshot fields reject booleans, nonfinite values and type coercions. Malformed snapshots raise `ValueError`.

Root fields are `schema_version`, `document_id`, `revision`, `geometric_tolerance_mm`, `parameters`, and `sketches`. Each sketch has `id`, `name`, `version` (currently 1), `plane`, `entities` and `constraints`. Plane vectors use three-number arrays. Lines encode `id`, `type: "line"`, `start` and `end`; circles encode `id`, `type: "circle"`, `center` and `radius`. Entity point arrays contain two numbers.

Constraints encode `id`, `type`, `targets` and `value`. Type names are stable strings: `coincident`, `horizontal`, `vertical`, `parallel`, `perpendicular`, `distance`, `length`, `radius`, `diameter`, `distance_x`, `distance_y`. Targets encode `entity` (UUID or null for origin) and `element` (`geometry`, `line_start`, `line_end`, `circle_center`, `origin`). Invalid/dangling and cross-sketch references are rejected, never resolved by names or substituted geometry.

Schema-1 parameter-only snapshots migrate in memory to schema 2 with no sketches, preserving document identity, parameter values, tolerance and revision. Loading alone does **not** rewrite the scene string; a later explicit committed edit can save the upgraded snapshot. Unsupported document or sketch versions fail closed and leave the original scene property intact. Invalid legacy records likewise remain stored for diagnosis rather than being silently repaired or discarded.

## Verification and remaining work

`cmake --build build --target check` includes document snapshot checks in Blender and a packaged-addon save/reopen check. Coverage includes all constraint/target encodings, IDs and arbitrary planes, schema migration/future rejection, malformed fields/references/numbers, copy lifetimes, no-op revisions, atomic rejection and revision overflow. The Blender check stores a fully constrained circle sketch on a rotated/translated plane, reopens it, solves a detached copy, and preserves both legacy and unsupported-future scene payloads.

This is not feature history or a complete persistence/history workflow. Sketch deletion/duplication with downstream dependencies, feature references/provenance, expression-driven dimensions, undo/redo reconstruction and interactive sketch editing remain to be implemented. The existing sidebar reports the stored sketch count, not an evaluated sketch display or solver status.
