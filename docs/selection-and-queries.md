# CAD selection, highlighting and precise queries

## Blender workflow

The sidebar's **Face / Edge / Vertex** controls choose the candidate domain.
Click selects a current CAD subelement and its Solid host; Shift-click adds or
removes that subelement. Different domains can coexist in the transient
selection. Selecting a new element without Shift clears the previous selection.
Removing the most recent element leaves any remaining selection on that host
available for inspection. Ordinary Blender objects continue through Blender's
selection path when no CAD candidate is hit.

Selected faces have a translucent overlay, edges have an orange polyline and
vertices have a point marker. Overlays respect depth, visibility and the
viewport's Show Overlays switch. They are GPU batches, not objects, modifiers or
modelling geometry. They do not change the document or generated mesh data.

The sidebar reports native CAD face area, edge length, supported analytic radius,
or a vertex's **part-local** position in millimetres. Host translation/rotation
does not change intrinsic length/area. **Save CAD Selection Reference** and
**Repair From Selection** work for all three domains using the existing named
reference command (`blendercad.save_reference`). Unnameable construction regions
remain inspectable but cannot acquire an invented persistent identity.

This is selection and exact point projection, not a completed sketch-dragging or
constraint-editing tool. A future drawing/gizmo command must consume these
queries through the document/constraint machinery, not alter a display mesh.

## Native query boundary

`geometry_queries.hpp` defines kernel-neutral result values. Geometry adapters
own the OCCT shapes; no query exposes an unmanaged kernel object. The low-level
adapter supports:

| Method | Result source |
| --- | --- |
| `measure(handle, element)` | B-rep face surface area, edge length or vertex coordinate; kernel centroid and optional analytic circle/cylinder/sphere radius |
| `closest_point(handle, element, point_mm)` | Minimum-distance point on the actual trimmed face, bounded edge or vertex |
| `ray_cast(handle, origin_mm, direction, max_distance_mm)` | Closest forward discrete intersection with a trimmed B-rep face; authoritative face index, point, distance and oriented normal |

The corresponding low-level Python functions are `geometry_measure(adapter,
handle, element)`, `geometry_closest_point(adapter, handle, element, point_mm)`
and `geometry_ray_cast(adapter, handle, origin_mm, direction, max_distance_mm)`.
They are adapter-local services, not persisted document operations.

Queries use doubles in part-local millimetres. Ray directions are normalized
with scale-safe arithmetic; nonfinite/zero directions and invalid distances are
rejected. Invalid topology kinds/indices and foreign/released handles are
rejected. Query tolerance is bounded by the document's modelling budget and
OCCT numerical confusion; it does not relax that budget. Surface normal
evaluation at a singularity can return `None` without discarding a valid hit.

Area/length come from kernel integration, not display triangles or sampled
polylines. Radius is returned only for recognized analytic geometry, not fitted
to tessellation. A centroid is not necessarily a point on a trimmed region.
Closest-point queries include trim wires, holes and edge endpoints, rather than
projecting onto an infinite supporting surface. Equal minima may return one
OCCT solution. Ray casting reports discrete face intersections, not a general
solver for ray/surface coplanar overlap. These services do not establish a
rigorous numerical error bound for every possible imported spline geometry.

`FeatureEvaluator` adds `measure_element`, `closest_point_element` and
`ray_cast` with full-document/current-body guards. `measure_reference` and
`closest_point_reference` additionally require exactly one `RESOLVED` target.
A split/merged/ambiguous/deleted/unavailable reference is never reduced to its
first candidate. A caller may explicitly inspect a current evaluated candidate
without saving its index as a design reference. Stale last-valid bodies cannot
supply current query results.

## Picking pipeline

`selection.py` owns viewport math and selection candidates. Faces are intersected
against current authoritative B-reps of visible compatible hosts; a display BVH
or triangle silhouette is not the final intersection authority. This permits a
true curved hit even where a deliberately coarse display polygon misses it.
Normal scene meshes and visible last-valid failed displays still occlude CAD
selection. Current CAD occlusion uses native face intersections.

Edge and vertex candidates use a default ten-pixel radius, independent of
modelling tolerance. CAD vertices use their exact generated positions; edge
samples propose screen-space candidates. Segments are clipped in homogeneous
space before projection, with perspective-correct interpolation. Near/far
clipping and segments crossing the near plane are handled before testing hits.
Several arcs of one curve may occupy the same pixels: all nearby candidates
are considered before visibility rejection, rather than letting an occluded
first arc hide another visible part of the same edge.

Candidate coordinates are projected onto the native trimmed edge/vertex, then
retested in screen space and checked for occlusion at the candidate's own ray.
The returned snapped point lies on CAD geometry. This is not a proof of the
global nearest screen point on an arbitrary curve; display sampling can limit
candidate discovery at extreme zoom/coarse tessellation. Adaptive screen-error
sampling or a dedicated curve/ray minimization service is the actionable
follow-up if those cases become necessary. No measurement is taken from the
screen candidate.

Viewport transforms use Blender's presentation precision. Kernel queries remain
part-local doubles. Supported host transforms are rigid translation/rotation;
scaled, reflected, sheared or nonfinite hosts cannot provide valid CAD selection
or inspection. This query preflight does not pretend to veto scripts or all
native transform paths; see the [operation audit](solid-operation-audit.md).

## Lifecycle and replaceability

Selections, refined hit points, measurements and GPU batches are disposable.
Every display publication/load/undo/redo replaces the packet token and clears
subelement selection/inspection. Scene-unit changes reject old packets until
recomputation, and the panel indicates that recomputation is needed. Naming
translation still goes through [persistent references](persistent-references.md).
Only persistent definitions enter the document snapshot; its schema remains 4.
The selection-mode setting is presentation state stored by Blender.

`highlight.py` creates shaders/batches lazily inside an interactive POST_VIEW
callback. Bulk triangle-face masks and edge ranges select the correct semantic
domain; triangulation edges do not become selectable CAD edges. Host placements
are applied through the GPU matrix stack. Depth/blend/write-mask/line-width state
is restored; point size is a shader uniform. Batch owners are weak references
and unregister releases live caches and the draw handler. Background addon
registration never allocates GPU resources.

Native computation remains synchronous. No background job accesses Blender
data. No new supplemental dependencies or runtime downloads are introduced.
Faces are currently queried per visible body; optimization should follow
separate measurements of evaluation/query/picking/transfer costs, not assumptions.

## Verification

Run `cmake --build build --target check`. The pipeline packages the addon and
runs native query/guard tests, packaged selection tests and highlight-array
checks alongside existing geometry/history/persistence checks. Cases cover
analytic area/length/radius, trimmed-hole closest points, inward hole normals,
arbitrary planes, extreme finite direction scaling, invalid inputs and stale or
unresolved targets. Packaged tests cover pixel selection, ordinary/failed-display
occlusion, units/placements, Shift toggles, clipping, edge-on overlapping arcs,
stale inspection and saving edge/vertex references. A coarse cylinder test
proves that display triangles miss a ray which the CAD picker still resolves.

`cmake --build build --target check_solid_ui` additionally injects actual Blender
mouse events for face/edge/vertex and perspective edge selection. It forces
interactive redraws and verifies GPU overlay draws without a handler error,
native inspector values and saved references. Standard shortcut protection,
dialog cancellation and conversion undo remain covered. This verifies the
supported Linux/OpenGL workspace, not every GPU backend, custom keymap,
instancing/linked-scene or native-integration gap.
