# Document-owned sketches and snapshots

## Ownership and interfaces

`src/blendercad/sketch.hpp` defines solver-neutral sketch data. `Document` owns ordered `SketchDefinition` records containing an immutable sketch UUID, a display name, a 3D plane, line/circle entities and constraint definitions. Entity and constraint UUIDs are stable values, not container indices or solver handles. IDs are unique across document definitions; display names are not identities and may repeat. Features introduce stable feature/body identities; a cut's repeated output-body UUID refers to the same body's history, not a duplicate body definition.

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

## Schema 7 and migration

The binding maps typed core records to JSON-compatible values; Blender's storage adapter uses Python's standard JSON encoder. This avoids a second JSON dependency in geometry/core code and keeps scene storage replaceable. All supported fields are required, unknown fields are rejected, and numeric snapshot fields reject booleans, nonfinite values and type coercions. Malformed snapshots raise `ValueError`.

Root fields are `schema_version`, `document_id`, `revision`, `geometric_tolerance_mm`, `parameters`, `sketches`, `features`, `rollback_before` and `references`. Each sketch has `id`, `name`, `version` (1 for numeric-only, 2 for parameter-bound dimensions), `plane`, `entities` and `constraints`. Plane vectors use three-number arrays. Lines encode `id`, `type: "line"`, `start` and `end`; circles encode `id`, `type: "circle"`, `center` and `radius`. Entity point arrays contain two numbers. [Feature records](features-and-evaluation.md) persist typed dependencies/parameters, stable body IDs, version and suppression; rollback uses a feature UUID or null. [Reference records](persistent-references.md) save named construction anchors with reference/body/creator UUIDs, topology kind, key and version, never display ordinals.

Constraints encode `id`, `type`, `targets` and `value`. Type names are stable strings: `coincident`, `horizontal`, `vertical`, `parallel`, `perpendicular`, `distance`, `length`, `radius`, `diameter`, `distance_x`, `distance_y`. Targets encode `entity` (UUID or null for origin) and `element` (`geometry`, `line_start`, `line_end`, `circle_center`, `origin`). Invalid/dangling and cross-sketch references are rejected, never resolved by names or substituted geometry.

Schema 5 adds [parameter expression definitions](parameter-expressions.md): a parameter stores either `value` or `expression` alongside `name` and `unit`, never both. Expression values and directed dependencies are reconstructed, not saved caches.

Schema 6 adds [sketch dimension bindings](sketch-dimensions.md). Version-2 sketches may replace a dimensional constraint's `value` with `parameter`; both fields together are invalid. Numeric-only sketches continue to emit version 1. The resolved numeric constraints and solved geometry are copies, not saved caches.

Schema 7 adds [constant-radius fillet records](fillets-and-history.md) with persistent inline edge targets and a literal/parameter radius. Extrusion/cut and sketch formats remain unchanged.

Schemas 1/2/3/4/5/6 migrate in memory to schema 7, preserving their existing definitions, identities, tolerance and revision. Missing feature history initializes empty with no rollback; missing references initialize empty. Loading alone does **not** rewrite the scene string; a later explicit committed edit can save the upgraded snapshot. Unsupported document, sketch, feature or reference versions fail closed and leave the original scene property intact. Invalid legacy records likewise remain stored for diagnosis rather than being silently repaired or discarded.

## Verification and remaining work

`cmake --build build --target check` includes document snapshot checks in Blender and a packaged-addon save/reopen check. Coverage includes all constraint/target encodings, IDs and arbitrary planes, feature records/dependencies/depth inputs, schema migration/future rejection, malformed fields/references/numbers, copy lifetimes, no-op revisions, atomic rejection and revision overflow. The Blender check reopens a constrained circle and its feature-driven extrusion on a rotated/translated plane, rebuilds geometry with a fresh evaluator, and preserves legacy and unsupported-future scene payloads.

Native feature history, named construction references, parameter-bound dimensions and dependency-checked sketch/feature/reference deletion now exist, but this is not a complete persistence/history UI workflow. The addon duplicates body definitions and reconstructs native state/managed meshes/reference resolution after load and undo/redo; see [Solid integration](solid-integration.md). Constant-radius fillets consume persistent edge targets; the sidebar provides history controls and explicit fillet repair. Initial interactive sketch editing exists; full bracket acceptance remains pending.
