# STEP export from Blender

## Workflow and coordinate contract

Select exactly one active CAD Solid and use **Export Solid as STEP** in the
BlenderCAD sidebar, or **File → Export → BlenderCAD Solid (.step/.stp)**. The
standard Blender file browser chooses the destination and handles its normal
overwrite confirmation. Export is available only when that Solid has a current
valid native result and no sketch edit is in progress.

The exported part is in **part-local millimetres**. Host translation/rotation,
scene unit display settings and presentation tessellation are not baked into
the STEP coordinates. A placed Solid and the same unplaced definition therefore
export the same CAD part. The file-browser description states this contract;
this is not positioned assembly or world-coordinate export. Unsupported
non-rigid host placement is rejected rather than exported as a display-only
deformation.

This command exports one selected body's result at the current history position.
It does not export other selected objects, a mesh conversion, an entire scene,
assembly instances or the feature/constraint history itself. A valid selected
body may be exported even if an independent body has failed. Rollback exposes
its earlier evaluated result; unavailable, suppressed, failed or blocked body
results cannot be replaced with a stale display for export.

## Ownership and current-result checks

`commands.export_step(context, filepath)` is the shared command service. It uses
the central `export` capability and explicit document/body associations to
validate a single active owning Solid. It rejects an active sketch draft,
invalid/shared object associations, missing native state and a document that no
longer matches its evaluator. It does not recompute automatically, replace
display meshes or change the scene's document snapshot.

The actual writer is `FeatureEvaluator.write_step(document, body_id, path)`.
Its existing full-document/current-body guard checks evaluated CAD geometry,
not Blender triangles, object names or a revision number alone. The geometry
adapter owns the OCCT shape and STEP exchange session, explicitly writes
millimetre units and uses the packaged exchange resources. Analytic/spline B-rep
geometry is transferred through OCCT; display tessellation does not determine
export accuracy or curved-surface representation. Changing display units alone
does not change the part-local CAD definition.

The Python operator is `bpy.ops.blendercad.export_step(filepath=...)`. A direct
`EXEC_DEFAULT` call supplies a path without opening the browser; the command
also accepts Blender-relative `//` paths. The browser supports `.step` and
`.stp`. The operator has neither modelling undo nor `REGISTER` replay: exporting
does not create a feature, change construction history or become an F9 redo
operation. Scene undo does not remove an exported file.

## Atomic file publication and errors

The writer first creates a unique sibling temporary `.step` file in the chosen
destination directory. OCCT writes that file; only a successful nonempty write
with unchanged source content is published with `os.replace`. Errors remove the
temporary file and leave an existing destination untouched. The parent directory
must already exist; export does not silently create a directory hierarchy.
Native writer failures, invalid paths, permissions and replacement failures are
reported as export errors without editing the document or scene objects.

This guarantees ordinary same-filesystem atomic publication, not storage-device
durability across a power failure or recovery from unrelated processes changing
directory permissions or destinations concurrently. The source snapshot and
evaluator match are checked again before publication; a changed source requires
recomputation and another export instead of publishing an obsolete result.

## Verification scope and remaining exchange work

Use the CMake packaging/check pipeline for native import and packaged-addon
verification. `blender_step_export` exercises execution through the public
operator, STEP readback dimensions/volume/analytic radii, filenames with spaces,
existing-destination replacement, injected writer/publication failures, rejection
of drafts and stale/failed results, unchanged snapshots/native cache/display
objects, and part-local coordinates after normal host placement. Kernel STEP
round-trip checks and private-runtime resource relocation are separate evidence
for the adapter and installed package boundaries.

The [bracket workflow](bracket-acceptance.md) additionally exports an actual
constrained two-arm part at three scales. `check_installation` uses Blender's
installer/enable operators in an isolated profile, then restarts Blender with
the saved addon preferences and exports an edited bracket from the installed
module. This check exposed scene access during restricted addon registration;
initial scene reconstruction now runs from a main-thread timer after registration,
with load/undo/redo reconstruction still handled by their existing callbacks.

Blender STEP **import**, positioned assembly export, multi-body selection export,
exchange options and downstream CAD feature reconstruction are separate work.
The existence of this export command does not by itself complete the entire
create/edit/save/reopen/render/mechanical-bracket acceptance workflow.
