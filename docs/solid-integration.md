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

## Picking, failures and reconstruction

Every publication gets a fresh random display token, including after load,
undo/redo and a cache-reused recomputation. `current_packet` checks the raw
scene snapshot, scene unit scale, body association, current-result state and
display token. `resolve` validates face/edge/vertex indices against that packet.
`pick_ray` refines a triangle hit to its evaluated-local CAD face and checks
normal scene occlusion; the click operator selects the owning host. Shift-click
extends selection. Interactive edge/vertex snapping/highlighting and precise
analytic hit refinement are not implemented.

**These picks are not persistent design references.** They must not be saved
or fed into downstream CAD features as stable face/edge identities. A later
reference service must translate feature provenance and OCCT topology-change
relationships and expose split/merged/deleted/ambiguous repair states.

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

Host visibility through every native Outliner/collection path, collection
instancing, linked scenes, custom keymaps, origin changes, parent edits and
arbitrary display-data mutations are not yet supported CAD workflows. Do not
claim Empty parenting alone integrates all Blender visibility/lifecycle rules.
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
