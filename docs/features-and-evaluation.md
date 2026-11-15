# Saved features and evaluated bodies

## Definition and dependency ownership

`Document` owns ordered `FeatureDefinition` records, not OCCT shapes. Feature version 1 supports `extrusion` and `subtractive_extrusion`. Each record has a stable feature UUID, display name, version, output-body UUID, suppression state and typed parameters. Its sketch/input references are its saved dependency definitions; `Feature.dependencies` exposes those references without a competing editable dependency list.

An extrusion creates a new body identity. A subtractive extrusion retains its input body's identity and must consume that body's latest preceding history tip. Independent bodies can coexist and reorder; branching one body into competing tips is not implemented. Missing/forward/cyclic dependencies, invalid identity reuse, unsupported versions and dimensionally invalid parameter references are rejected before document mutation. Duplicate display names remain allowed.

Depth is either a literal in millimetres or a named parameter whose unit is `mm`. The parameter can have an [arithmetic expression](parameter-expressions.md); depth strings themselves remain names, not a second expression language. Finite nonpositive depth can remain a saved definition, but evaluation reports failure because this feature version extrudes only a positive distance along the sketch plane's right-handed normal. Boolean cuts that remove no material likewise fail instead of silently publishing an unchanged part.

`add_extrusion`, `add_subtractive_extrusion`, `set_feature_depth`, `set_feature_suppressed`, `reorder_features`, `set_rollback_before`, `erase_feature` and `erase_sketch` use the document's atomic candidate-publication/revision rules. Reorder requires a complete valid permutation. Deletion rejects dependents by default; explicit `cascade=True` removes downstream features, leaving unrelated sketch definitions intact. A rollback marker is also a deletion dependency: clear it explicitly or authorize cascade; cascade clears a deleted marker in the same transaction. Native document edits are not Blender undo interactions.

## Evaluation path

`FeatureEvaluator` owns disposable kernel adapters, evaluated results and last-valid shapes. It solves a **copy** of the referenced sketch with PlaneGCS, validates the profile, then uses OCCT for extrusion/subtraction. It does not overwrite saved sketch coordinates or store solver state in the document.

Profiles currently support one circle or one simple closed line loop. Line connectivity is assembled from endpoints within the document's geometric budget, independent of entity order/direction. A canonical shared junction can adjust an endpoint by no more than that budget. Ambiguous joins, open/disconnected loops and mixed/multiple-loop profiles fail explicitly; the evaluator does not arbitrarily select a loop or replace the profile with a mesh. Separate sketches/features can represent individual holes until explicit profile-region selection is added. Underconstrained but solvable profiles can evaluate; their solver state remains available in the feature report.

| State | Output behavior |
| --- | --- |
| `UP_TO_DATE` | Current analytic geometry is available for display, measurement and STEP export |
| `FAILED` | Feature could not evaluate; last-valid display is retained if available |
| `BLOCKED` | A referenced input has no current output; stale input geometry is never evaluated downstream |
| `SUPPRESSED` | Definition remains, but output is unavailable and consumers block; there is no implicit dependency bypass |
| `ROLLED_BACK` | At/after the stable rollback marker; definition/cache remains but is excluded from the requested result |

Rollback is stored as `rollback_before`: a feature UUID or null for the full history. It can expose a preceding valid body for measurement/export without deleting later definitions. Suppression is distinct: a suppressed requested body tip is unavailable, so use rollback to request an earlier current state rather than treating stale suppressed output as current.

## Caching, failures and queries

Cached results compare operation parameters, body identity/version, the full sketch definition, resolved depth, tolerance and upstream result identity. Unchanged results survive unrelated parameter/name edits and independent-body reorders. Changed inputs recompute affected downstream features. Deleted-feature results are released. Kernel handles never leave the evaluator; shapes retain their owning adapter, including across tolerance changes, so operands cannot be mixed across handle namespaces.

Queries take the current `Document`. The evaluator compares its complete source snapshot, not only document UUID/revision: undo or divergent snapshots can repeat a revision with different content. A changed document must be evaluated before queries proceed. Measurements and STEP exports also require the requested body tip to be `UP_TO_DATE`; an old valid shape is **not** exported as the requested failed result. Export guards run before touching the destination file.

`tessellate` can return last-valid display data with `current=False`. Metadata includes document/body/requested-feature UUIDs, the actual geometry-source feature UUID, evaluated revision, geometry-source revision, requested tessellation settings and a geometry generation scoped to this evaluator. Reusing correct unchanged geometry can retain an older geometry-source revision while still being current for the newly evaluated definition. CAD face/edge/vertex indices remain local to that geometry generation, not persistent references. Triangle and sampled-edge indices also depend on tessellation settings: a geometry generation alone does not identify a display mesh. Presentation must replace picking arrays with every display publication, reject stale picks and discard picking data whenever an evaluator is replaced. Changing a body identity cannot relabel an old body's cached display.

If a new failed feature has no own last-valid shape, the body may display its previous valid result or a current upstream ancestor, explicitly marked stale with the true geometry-source feature. Initial failure may have no display. Native last-valid B-rep caches are process-local. Managed Blender meshes save normally and can remain visible after reopening failed definitions, but do not recreate an unavailable past B-rep or supply current picks/measurements/exports.

Calls are synchronous and contain no Blender API access. Concurrent document mutation/evaluator calls and asynchronous scene publication are not implemented. A future worker/presentation layer must keep immutable source snapshots and apply only still-current results on Blender's main thread.

`FeatureEvaluator.fork()` copies document/report/cache state for speculative
evaluation while retaining shared ownership of immutable cached shapes and
their original geometry-adapter namespaces. Successful or failed evaluation
on the branch does not replace the source evaluator's results. Either branch
can outlive the other. This supports transactional presentation preparation;
it is not a concurrent/thread-safe evaluator API. Generation counters are
branch-local, not globally unique pick identities.

## Persistence and verification

Snapshot schema 3 adds `features` and `rollback_before`. Feature records encode `id`, `name`, `version`, `type`, `body`, `sketch`, `input` (null for a base extrusion), `depth` and `suppressed`. Depth encodes exactly one of `{"value_mm": number}` or `{"parameter": name}`. Schema 1/2 load in memory with an empty feature history and no rollback; original scene payloads remain unchanged until an explicit save of edited definitions. Unsupported feature versions/types fail closed.

Schema 4 introduced [named persistent references](persistent-references.md), retained in current schema 6 alongside expression definitions and [parameter-bound sketch dimensions](sketch-dimensions.md). Evaluated shapes carry construction/Boolean provenance; `capture_reference` translates a current element into a stable target and `resolve_reference` reports explicit outcomes. `EvaluationReport.references` associates diagnostics with saved reference UUIDs. Schema-3 documents migrate with an empty reference list.

`cmake --build build --target check` covers a constrained plate extrusion and circular subtraction, analytical volume/STEP round trips, snapshot reconstruction, selective cache reuse, failure/blocked propagation, stale export protection, tolerance cycling, suppression/rollback, reorder/deletion, same-revision divergent documents, profile connectivity and body-identity isolation. The packaged Blender check saves/reopens a feature-driven cylinder and reconstructs native geometry with a fresh evaluator. These are native feature-service checks, not acceptance of the interactive bracket workflow.

## Remaining integration

Initial Solid hosts, managed Blender meshes, evaluated-local picking/highlighting, whole-body materials, parameter/dimension commands and undo/redo reconstruction are implemented in the [presentation adapter](solid-integration.md). The [interactive sketch editor](interactive-sketches.md) provides detached drawing/constraint/drag previews and confirmed sketch extrusion/cuts. Construction/Boolean provenance and explicit named-reference repair now exist; display indices still must not be saved in downstream feature definitions. Reference-consuming features, solid feature previews, a full sketch toolbar and complete history UI remain incomplete. Constant-radius fillets, multiple profile regions and broader operations remain separate feature work. No bracket slice or final installed-package acceptance is claimed here.
