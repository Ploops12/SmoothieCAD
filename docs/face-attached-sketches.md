# Associative planar face sketch supports

## Blender workflow

Select one current CAD face in **Face** selection mode. In the sketch panel,
**On selected face → Into Solid** starts a sketch whose positive normal points
into the part, suitable for a positive-depth cut. **Outward** uses the outward
face normal, suitable for an independent extrusion. Only exact analytic planar
faces are supported; a curved face is rejected rather than approximated by a
tangent plane. The ordinary line/circle/rectangle and constraint tools operate
on this attached sketch. Confirmation saves the support and sketch together in
one undoable interaction; cancellation leaves no sketch or attachment behind.

The face frame is defined in part-local CAD millimetres. Its origin is the
projection of the part origin onto the infinite support plane, not the clicked
point, trimmed-face centroid or Blender cursor. The initial X direction comes
from the selected analytic plane and is saved as an explicit part-local tangent
direction. **Into Solid** reverses the normal and local Y direction, so signed
Y dimensions may be negative on an upper XY face. The editor aligns to that
right-handed frame. The supporting face does not implicitly trim the sketch.

Use the saved sketch's **Extrude** or **Preview Cut** command for a blind pocket.
When an earlier thickness/depth edit moves its support plane, the sketch and
pocket follow it. This is geometry association, not an assembly placement
constraint: moving an independently positioned source host does not move other
Solid hosts. New independent extrusion hosts copy the source frame once; their
later placements remain separate Blender presentation state.

For an existing sketch, **Attach to Face** explicitly replaces its literal
plane with the selected face support and resumes full history. Its 2D geometry
and constraints are retained, not silently projected from a mesh. A support
must precede every consuming feature. To attach/rebind a consumed sketch, first
use **Support Inputs** to roll back before its earliest consumer and then select
an upstream face. Picking the consumer's own output would create a cycle and
is rejected.

## Frame edits, failure and repair

**Frame** edits signed U/V/normal offsets and normal reversal. The normal
offset follows the chosen oriented normal; it is not a modelling tolerance.
**Tangent direction** keeps the saved direction or explicitly chooses part-local
X/Y/Z to project into the plane. An axis parallel to the normal is an explicit
failure, never an automatic axis switch. These controls use the same document
transaction path as sketch dimensions and solid features.

An unavailable/suppressed support result blocks consumers. Split, merged,
deleted, ambiguous, nonplanar or degenerate support frames fail with diagnostics;
no nearby face is substituted. Existing last-valid Solid display remains visibly
stale and downstream consumers block. Current measurements, picks and STEP
export cannot use that stale result. An unused attached sketch shows its support
diagnostic in the sketch panel even before it has a consuming feature.

To repair, use **Support Inputs**, select one replacement planar face and choose
**Repair + Resume**. This explicitly replaces the support reference
and result context while retaining tangent direction, offsets and 2D intent.
If that tangent is parallel to the replacement normal, explicitly change it in
**Frame**. An unconsumed sketch can be repaired directly against a current face.
**Detach** freezes the current resolved CAD plane and removes the dependency;
it requires a resolvable support, not a stale or guessed fallback plane.

Native feature/sketch deletion validates the complete support dependency
closure. Non-cascade deletion rejects dependents. Explicit cascade deletion
removes attached sketches, their consumers/descendants and affected bookmarks.
**Delete Sketch** exposes that choice. Solid delete/conversion rejects
unselected dependent Solids and unused attached sketches; select dependent
Solids too, or explicitly detach/delete their sketches. It does not silently
destroy a second unselected part.

## Native ownership and evaluation

`SketchSupport` stores an upstream feature identity, a persistent FACE target,
saved tangent direction, signed offsets and reversal. The explicit result
context is distinct from the face's anchor creator: a face created by an
extrusion may be selected after a cut. Resolution occurs in that selected
upstream feature's current result, never in the body's final tip or last-valid
cache. Later operations on that body cannot accidentally make the sketch depend
on its own output. No face ordinal, kernel pointer, object name or UV index is
saved.

The geometry adapter's `planar_face` uses OCCT's analytic surface and oriented,
location-applied face. The evaluator projects the saved tangent into its normal
plane, constructs a right-handed orthonormal frame and applies the offsets. This
avoids using incidental OCCT UV axes or a changing trimmed centroid on recompute.
The dimensionless `1e-10` near-parallel/frame threshold is separate from the
unchanged document geometric budget (default **0.001 mm**).

The ordered document DAG includes support context/creator dependencies as well
as ordinary sketch and feature inputs. Reorder, self-reference, cycles and
missing/wrong-body support contexts are rejected atomically. Cross-body support
is an explicit document dependency, not a positioned assembly joint.
`Document.feature_dependencies(feature_id)` includes these transitive-through-
sketch inputs; the standalone `Feature.dependencies` property describes only
inputs intrinsic to that feature record.

During evaluation, the support must resolve uniquely against same-pass current
upstream geometry **before** cache lookup. The resolved profile plane is part of
the geometry cache key. Independent unchanged results remain reusable. Failed
or rolled-back contexts are never replaced by cached geometry. Query APIs check
the complete source snapshot, including definitions changed at a reused revision.

`Document.parameterized_sketch` resolves only 2D dimension bindings. Its attached
plane is a canonical placeholder, not evaluated geometry.
`Document.evaluated_sketch` explicitly rejects attached sketches. Use
`FeatureEvaluator.evaluated_sketch(document, sketch_id)` for the actual CAD plane
or `support_plane(document, support)` for support-only resolution. The detached
sketch session owns a private evaluator for these queries; scene state and
committed native caches remain untouched during drawing/dragging. Solid preview
handles also query the evaluated support plane.

Independent duplication remaps support contexts, face/body/creator identities
and construction keys when their definitions are copied. Explicit references to
external source bodies remain shared dependencies, as named parameters do;
duplicating a child alone does not clone unrelated upstream bodies.

## Persistence, checks and limits

Document schema **8** adds version-3 attached sketch records. Such a record has
`id`, `name`, `version`, `support`, `entities` and `constraints`, **not** `plane`.
The support record has `feature`, `face`, `x_direction`, `offset_u_mm`,
`offset_v_mm`, `normal_offset_mm` and `reversed`. Its face uses the existing
`body`/`kind`/`feature`/`key` encoding. The internal `SketchProblem.plane` must be
canonical while attached; it cannot compete with the support definition.
Unattached sketch versions 1/2 remain unchanged. Schemas 1–7 migrate in memory
without rewriting the scene property on load. Strict type/field validation and
future-version rejection remain in force.

```sh
cmake --build build --target check -j2
cmake --build build --target check_sketch_support_ui -j2
```

Native and packaged Blender checks exercise support-driven pockets, dimensions,
planes, failure/repair, dependency operations, schema round trips and committed
scene ownership. The real-window check covers actual face picking, attached
sketch authoring, earlier edits, repair and exchange. Generated window evidence
is under `build/sketch-support-ui-tests` and normal CMake `clean` owns it.

Supported scope is planar face attachment, not free-space spatial constraints,
curved-surface sketching, associative projected external sketch edges, automatic
repair or a universal topological naming solution. The explicit tangent policy
can become degenerate under a large normal change and requires repair. U/V/normal
offsets are currently literal millimetres, not parameter-expression inputs.
Evaluation remains synchronous; asynchronous jobs and native Blender enforcement
are separate work.
