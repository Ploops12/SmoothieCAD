# Managed Solid presentation

## Implemented boundary

The addon now publishes evaluated bodies as selectable non-mesh Empty hosts
and unselectable, addon-managed mesh displays. The native document remains the
sole design definition. Objects carry document/body UUID associations; object
names and evaluated face indices are not persistent identities. This is the
initial replaceable presentation backend, not native Blender Solid support.

In the 3D View sidebar, create a CAD document and use **Add Rectangle Solid**.
Its numeric dialog creates a fully constrained XY rectangle sketch and a
positive extrusion through `commands.py`. Width/height/depth are millimetres.
The generated geometry is visible in normal shading and Cycles. The host can
be selected by clicking the displayed body or through the Outliner, translated
and rotated using normal Blender transforms. Scale is not a CAD operation.
Creation frames the new body's display bounds; **Frame CAD Solid** (Numpad
Decimal for a Solid-only selection) frames its geometry rather than the Empty's
origin. Framing lowers the viewport near-clip distance when needed for small
parts. It does not change modelling tolerance or geometry.

The sidebar exposes feature-depth edits, creation/editing of named length
parameters, recomputation, whole-body material selection, duplication, deletion
and **Convert to Regular Mesh**. A depth may be a literal millimetre value or a
named length parameter. This does not yet supply arithmetic expressions,
interactive sketch tools, face-based modelling or fillets.

## Ownership, units and publication

`presentation.py` owns disposable native evaluators, BVHs, pick maps and Blender
mesh publication. `commands.py` owns detached-document edits and prepares
native results/display meshes before publishing a snapshot. UI operators call
those commands and carry Blender `UNDO`; construction history is not the undo
log. These synchronous paths touch Blender data only on its main thread.
There is no asynchronous publication or interactive preview session yet.

Preparation forks the native evaluator's complete state, sharing immutable
cached shape ownership without mutating the published cache. Display meshes
and regular-mesh conversion copies are staged independently. A scene journal
retains original data/parents/tags/visibility, links and selection while changes
are applied. Objects are reversibly unlinked before final deletion. If
publication fails, the journal restores scene state, the original snapshot and
the original evaluator/pick packets; new unused meshes/materials are discarded.
Blender returning `CANCELLED` alone would not undo partial datablock changes.
After logical commit, disposal of unused old caches does not turn a successful
edit into a cancelled operation. This protects controlled synchronous command
failures, not process crashes or arbitrary third-party scripts mutating the
same data during callbacks.

`blendercad_role` distinguishes `SOLID` hosts from `DISPLAY` meshes. Both store
`blendercad_document_id` and `blendercad_body_id`. Presentation recovers their
association using these tags; names and collection hierarchy are not semantic
inputs to geometry evaluation. Duplicate identities are rejected rather than
silently assigned another body. Do not duplicate tagged objects using an
unguarded native path: use **Duplicate CAD Solid**.
Display lookup additionally verifies mesh type and parent identity; an orphan,
misparented, linked or multi-scene tagged object is rejected before publication,
not adopted merely because its tags happen to match.

Design coordinates are millimetres in double precision. Display coordinates
use `0.001 / scene.unit_settings.scale_length` Blender units per millimetre;
changing scene units invalidates picks until recomputation. Host translation
and rotation position the local presentation. They do not modify a sketch or
the B-rep, and native STEP export currently remains in part-local coordinates.
Assembly instances and saved document-owned placements remain future work.

Mesh publication uses bulk arrays and `foreach_set`. Triangle face attributes
are `blendercad_face`. Sampled loose CAD-edge segments carry `blendercad_edge`;
surface triangulation edges carry `-1`. Additional loose CAD vertices carry
`blendercad_vertex`; ordinary surface/sample vertices carry `-1`. The three
domains share the evaluated shape's topology numbering. Those loose elements
are display aids and do not become extra Cycles surfaces.

The first material slot is whole-body presentation state and survives mesh
regeneration, duplication and conversion. Per-face material assignment is not
supported: copying face ordinals after topology changes could apply materials
to unrelated regions. Persistent region references must precede that feature.

## Visibility and collection lifecycle

`lifecycle.py` is a Blender-only presentation adapter. The Solid host owns
collection membership, object-level viewport/render visibility, per-ViewLayer
Hide/Show state and local-view membership. Its managed display follows those
settings; do not edit the child's visibility as independent design state.
Parenting alone does not propagate these settings in Blender. Matching the
host's collections also gives the display Blender's collection visibility,
render restrictions and ViewLayer exclusions without inventing another set of
saved CAD visibility definitions. Hosts may belong to several local collections.
Host viewport hiding does not imply render hiding, matching ordinary Blender
semantics. A body unavailable at the current rollback point is additionally
hidden in both viewport and render; returning to the end of history respects
the host's existing flags instead of unconditionally showing the display.

Changed values are synchronized after main-thread dependency-graph updates,
before saving, and before the render graph is built. A main-loop timer also
handles local-view changes and deferred updates. Local-view/picking/highlight
visibility uses the applicable 3D viewport, not just object-level visibility.
The timer may take up to approximately 0.2 seconds on an idle main loop; busy
Blender operations can defer it longer. Synchronization does not solve sketches,
evaluate features, replace meshes, invalidate current pick tokens, or modify the
document snapshot/revision. It never forces a graph update inside a graph
callback, and only writes changed settings to avoid update loops.

Render initialization synchronizes static host visibility before the first
render, then dependency-graph/timer mutation is disabled until render completion
or cancellation. Off-main-thread callbacks do not access Blender datablocks.
Animated visibility changes during a render/animation sequence are not yet a
supported workflow: they need a frame-evaluation adapter rather than unsafe
worker-thread scene mutations. Synchronous background Cycles rendering is
checked; interactive render-thread behavior is not yet acceptance-tested.

The adapter only services already reconstructed current documents. Invalid or
future snapshots are not parsed, adopted or rewritten by visibility callbacks.
Linked or multi-scene host/display associations fail validation before any
visibility changes; the sidebar reports the ownership issue. A collection
shared into another scene can create the same unsupported ownership condition.
Resolve it by returning the Solid to one local owning scene before recomputing.
Presentation changes made during a CAD transaction are journalled along with
collection links, ViewLayer hide state and local-view membership, so a failed
publication restores their preceding values.

## Picking, failures and reconstruction

Every publication gets a fresh random display token, including after load,
undo/redo and a cache-reused recomputation. `current_packet` checks the raw
scene snapshot, scene unit scale, body association, current-result state and
display token. `resolve` validates face/edge/vertex indices against that packet.
`pick_ray` now intersects the current B-rep rather than accepting the display
triangle silhouette. Face/Edge/Vertex modes provide current subelement picks,
exact trimmed point projection, native measurements and GPU highlights. The
click operator selects the owning host; Shift-click toggles subelements. See
[selection and query services](selection-and-queries.md) for clipping, occlusion,
perspective handling, transient selection lifecycle and curve-candidate limits.

**These picks are not persistent design references.** They must not be saved
or fed into downstream CAD features as stable face/edge identities. The
[reference service](persistent-references.md) now translates a current pick
into a construction-derived target and exposes split/merged/deleted/ambiguous/
unavailable states. Save CAD Selection Reference and Repair From Selection
operate on these stable targets, not on saved display indices.

Failed or blocked features retain their last-valid display and expose state
and diagnostics in the selected Solid's sidebar. Stale displays have no current
pick access and cannot be converted as the requested current result. Native
measurements/STEP exports retain the evaluator's stronger current-result guard.
Stale display object colors are red, but a viewport using material colors may
not show that tint; the sidebar diagnostic is the definitive visible state.

Versioned snapshots and generated meshes save normally in `.blend`. Persistent
load/undo/redo handlers discard all native/picking caches and reconstruct from
the restored definitions. Future/invalid snapshots remain untouched and expose
an error instead of being rewritten. If a failed model is reopened, its saved
last-good display is retained, but no old B-rep is invented and no picks are
published for it. Empty-history/rollback bodies retain their hosts/placements
with display hidden, ready to reappear when history makes them available.

## Compatible commands and remaining integration gaps

Duplication copies the body feature chain and referenced sketch/entity/
constraint definitions with fresh identities. Named parameters intentionally
remain shared document-level inputs. It copies host placement and whole-body
material. This is independent body duplication, not an assembly instance.
Document-level named reference bookmarks remain on their original body when
another body definition is duplicated; consuming feature inputs will require
explicit clone/remap semantics when those feature types are implemented.
Duplication while rolled back is rejected explicitly. Deletion removes the
selected body chains with dependency-aware cascading; unused sketch definitions
remain saved. Conversion requires current geometry, detaches the generated
surface mesh, removes CAD tags/attributes and loose display aids, and deletes
the body's feature chain. Blender undo restores both intent and presentation.

The initial entry-point audit and exact incompatibility policy are in
[Solid operation audit](solid-operation-audit.md). Native scripts, customized
keymaps and several toolbar/menu/property paths cannot currently be universally
vetoed. There is no after-the-fact deformation acceptance or automatic conversion.
Unsupported modifications made through those gaps are not CAD edits.

Collection instancing, linked scenes, custom keymaps, origin changes, parent edits and
arbitrary display-data mutations are not yet supported CAD workflows. Do not
claim these addon adapters integrate every Blender visibility/lifecycle path.
Additional audited adapters or native integration are required. Background
tests cover command execution and ray resolution. An optional isolated-window
event test covers the standard startup keymap/workspace, not every customized
Blender workspace or physical input device.

## Verification

Run `cmake --build build --target check`. CMake packages the addon and runs
`blender_solids` and `blender_capabilities` alongside the native and persistence
checks. Solid checks cover constrained rectangle creation, analytical volume,
bulk topology mappings, occlusion-aware face rays, Cycles CPU rendering,
placement/material preservation, failed edits, stale-token rejection, independent
definition duplication, conversion/deletion, unit changes, save/reopen and
undo/redo including conversion undo. Tests use temporary files and leave no
generated models or renders in the source tree.
Injected failures after mesh replacement and conversion detachment/unlinking
verify snapshot, object/data identity, associations, selection, native queries,
pick tokens and generated-mesh counts are restored.
`blender_lifecycle` additionally checks host flags, separate ViewLayers,
collection moves/exclusions, local view, rollback visibility, snapshot/cache
stability, ownership rejection, transactional restoration, save/reopen and
first-render hidden/shown behavior using actual Cycles image pixels.

When `xvfb-run` is installed on the Linux build host, CMake also provides
`cmake --build build --target check_solid_ui`. It launches a real Blender window
in an isolated X server and injects mouse/key events through Blender's event
queue. It verifies click selection, numeric-dialog/deletion cancellation,
Shift-D definition duplication, conversion confirmation and Ctrl-Z restoring
the correct pre-conversion design, plus explicit dispatch into the scale/Tab
guards. This caught and prevents regression of nested-operator undo loss.
Logs, configuration and temporary recovery files remain under `build/ui-tests`
and are removed by the normal CMake `clean` target. No user preferences are
saved. This optional test does not establish universal operation protection.
It also verifies real edge/vertex and perspective-edge events, native inspector
values, saved references and successful GPU overlay draws in the supported
OpenGL viewport. Native H/Alt-H and Numpad-Slash events verify complete-Solid
Hide/Show and local-view display/picking without changing the document or mesh
publication token.
