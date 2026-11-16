# Interactive planar sketches in Blender

## Entering and leaving the editor

The BlenderCAD sidebar lists document sketches. Use **XY**, **XZ**, **YZ** or
**Cursor** to start a new sketch at the 3D cursor, or **Edit** beside an existing
sketch. Cursor mode uses the cursor's orientation, enabling arbitrary planes.
When a Solid is active, new or unused sketch coordinates are expressed in that
Solid's part-local frame; consumed sketches use its placement only when that
Solid consumes the sketch. Otherwise editing uses the document's part-origin
frame. The overlay identifies the frame. Unused sketches have no persisted body
owner: select the desired Solid before editing or extruding them. A shared
sketch remains one definition, not a separately stored shape per placed body.
Standalone sketches are listed in the sidebar but currently drawn only while
editing, not as independent persistent viewport objects.

**On selected face → Into Solid/Outward** starts an associative planar sketch
on one current selected CAD face. Its saved upstream face reference replaces a
literal frame; earlier changes move the evaluated plane. Attached sketches use
their consuming Solid's placement (or their support Solid before consumption),
not an unrelated active object. See [face-attached sketches](face-attached-sketches.md)
for orientation, offsets, explicit repair/detach and dependency behaviour.

The view temporarily aligns to the sketch plane and uses orthographic projection.
Existing geometry, or the active Solid's bounds for a new empty sketch, determines
the initial view extent; there is no fixed large-part floor preventing tiny
profiles from being picked.
Its preceding rotation, location, distance, perspective mode and near clipping
are restored when editing ends. Middle-mouse and wheel/trackpad navigation remain
available. Picking intersects the visible clipped viewport ray with the plane;
parallel/out-of-clip rays do not invent points on another plane. Blender's cursor
orientation is reorthonormalized in double precision before becoming a native
design plane. This is planar sketching, not free-space spatial constraints.

**Enter** confirms one undoable interaction. **Esc** cancels the entire pending
sketch edit, except while typing or dragging: the first Esc cancels that local
action and returns to the sketch, and a subsequent Esc cancels the session.
Right-click clears an unfinished drawing and returns to selection. An unfinished
line-chain rubber band is not a saved entity.

## Drawing, selection and constraints

| Input | Behavior |
| --- | --- |
| `L` | Line chain: click a start, then successive endpoints |
| `C` | Circle: click center, then a radius point |
| `R` | Rectangle: click opposing corners |
| Shift + line endpoint click | Add horizontal/vertical constraint on the dominant sketch axis |
| `S` | Sketch selection mode |
| Click / Shift-click | Select / toggle endpoints, circle centers, origin or whole geometry |
| `H` / `V` | Horizontal / vertical for one selected whole line |
| `P` / `O` | Parallel / perpendicular for two selected whole lines |
| `K` | Coincidence for two selected points, including the origin |
| `D` | Length for one whole line, radius for one whole circle, distance for two points |
| Shift-`D` | Diameter for one selected whole circle |
| `A` / `B` | Signed X / Y distance for two selected points |
| Click a dimension label, then `D` | Edit the existing dimension |
| Tab / Shift-Tab | Cycle saved constraints forward / backward |
| `X` / Delete | Delete selected constraint, or selected geometry and constraints referencing it |
| `G`, move, click | Drag a selected endpoint, circle center or dimension |
| Press-drag-release an endpoint/center | Point drag with the same solver path |

Endpoint/center/origin snapping within the screen radius creates explicit
coincidence constraints when constructing geometry. Rectangle construction adds
four coincidence and four horizontal/vertical constraints; size and placement
remain free unless constrained separately. A click at a snapped first corner can
anchor it to the origin or another point. Repeated positions alone are not
substitutes for saved coincidence constraints.

Dimension entry appears in the viewport. Type a finite numeric millimetre value
(including a sign or scientific notation), then Enter. Prefix an existing named
length parameter with `=`, for example `=plate_width`, to bind the dimension.
Arithmetic belongs in the [parameter definition](parameter-expressions.md), not
in another sketch expression parser. Length/radius/diameter must be positive,
point distance nonnegative, and signed X/Y distances can be negative. Bindings
and numeric dimension edits use the same [dimension commands](sketch-dimensions.md)
as sidebar fields.

The header shows solve state and remaining degrees of freedom. Conflicting,
redundant and failed results remain distinct; the selected constraint's status
appears in the overlay. Tab also selects nondimensional constraints whose targets
are highlighted, providing a way to remove an earlier conflicting constraint
without deleting its entities. Constraint numbers in this UI are transient
labels, never document identities. Selecting ambiguous coincident screen points
currently chooses the first nearest target; there is no disambiguation menu yet.

## Dragging and previews

Point dragging solves a numeric copy with temporary signed X/Y guide constraints.
If the requested movement conflicts with permanent constraints, it is rejected;
the permanent constraints are not weakened or removed. A successful movement
explicitly updates the saved starting coordinates in the detached draft, retains
all entity/constraint identities, and discards the temporary guides. Fully
constrained points require changing their defining dimensions instead of forcing
their display positions.

Dimension dragging is horizontal screen movement measured in sketch millimetres
at drag start. It edits a literal dimension, or the literal named parameter to
which that dimension binds. Shared parameter users therefore change together on
confirmation. An expression-defined parameter is not silently replaced by a
literal: dragging reports that its expression/upstream inputs must be edited.
Esc during a drag restores its exact draft baseline. The solid's committed
display is not edited to pretend that it has a different dimension.

The preview is a direct GPU sketch overlay, not a mesh object. It shows the last
valid numeric sketch and pending drawing curves above the committed scene. This
intentional sketch-edit X-ray display is separate from occlusion-aware Solid
picking. Lines/circles are sampled for display and screen candidates; their
definitions and solver arithmetic remain analytic/double precision. Dimension
labels share drawing/picking anchors. Error coloring and textual diagnostics
retain the preceding valid sketch when a structurally valid draft conflicts or
fails to solve. Failed definitions may still be confirmed for later repair;
their dependent CAD features follow the existing last-valid/blocked contract.

## Module ownership and transaction boundaries

`sketch_commands.Session` owns a detached native document and solver copies. It
has no Blender API dependency, scene objects, kernel handles, or persistence side
effects. Each action validates a candidate document before replacing draft
state. `fork()` supplies independent baselines for mouse previews. Original
geometry guesses are preserved for ordinary constraint edits and deliberately
updated only by geometry construction or successful point movement.

`sketch_view.py` owns plane projection, UUID-based sketch picking, sampled
presentation and lazy GPU/BLF resources. `sketch_editor.py` owns Blender modal
input, transient selection, view/cursor/handler lifetimes and the final command.
There are no preview meshes or saved preview pointers. Confirmation calls the
same `commands.commit` speculative evaluator/journal used by numeric controls.
One modal confirmation creates one Blender undo boundary, not one per mouse
move. F9 operator-property replay is not offered for mouse strokes; Blender
undo/redo restores the saved definitions. There is no separate per-stroke Ctrl-Z
history inside a session yet.

Until confirmation, the scene snapshot/revision, native feature evaluator, CAD
meshes and pick tokens remain unchanged. Saving through an external script saves
only committed intent, not the draft. Other addon editors are disabled and
command publication rejects non-owning transactions while a sketch is active.
Changed source snapshot, scene units, host placement or a replaced viewport
invalidates the session; cancellation preserves the external state instead of
overwriting it. Load/undo/redo or render initialization discard active drafts.
Worker-thread callbacks never touch Blender data, and all computation/application
in this synchronous editor runs on the main thread. Arbitrary scripts and
unguarded native operations are not universally vetoed by an ordinary addon.

## From sketch to solid

After confirming a sketch, use its **Extrude** button. Enter a literal depth or
named length parameter to create a body. A new independent body receives the
same active-frame placement used to edit that profile, applied within the scene
publication journal; it does not jump to the origin or remain linked to the
source Solid's later placement changes. Enable **Cut selected Solid** to consume
the selected body's latest feature and create a subtractive extrusion. Depth
follows the sketch plane's positive right-handed normal; position/orient the
sketch accordingly. Creating features while rolled back is rejected explicitly.
Earlier sketch edits recompute affected features, and stable references resolve
through their existing provenance rather than new display indices.
The sketch list's **Preview Extrude**/**Preview Cut** buttons offer the same creation
definitions through a detached ghost result and depth handle, with numeric
entry and one-interaction confirmation/cancellation; see
[solid feature previews](solid-feature-previews.md).

Current profile evaluation still accepts one connected simple line loop or one
circle per sketch. Open chains, multiple regions and mixed line/arc loops do not
silently become an arbitrary chosen face. Use separate closed sketches/cuts for
separate hole profiles until multiple-region/arc evaluation is implemented.
Curved-surface attachment, a full sketch toolbar/gizmo system, per-stroke
undo and additive unions remain follow-up work.
[Constant-radius fillets](fillets-and-history.md) now consume selected persistent
CAD edges after extrusion/cuts. The complete bracket acceptance gate is not claimed here.

## Build and verification

The original editor uses parameter-bound sketch definitions introduced in schema
6. Attached sketches add schema-8 support definitions, while unattached sketch
formats remain unchanged. No supplemental dependency is required. CMake packages
all editor modules automatically.

```sh
cmake --build build --target check -j2
cmake --build build --target check_sketch_ui -j2
```

The background `blender_sketch_editor` check exercises detached ownership,
construction/snapping, every supported constraint family, solve diagnostics and
repair, atomic rejection, temporary point guides, dimension drivers, arbitrary
planes, confirmed extrusion/cuts, analytic volume, STEP round trip, persistence,
cache reuse and undo/redo. The isolated-window target draws all three primitives
with mouse events, enters dimensions, checks GPU rendering, cycles/deletes
constraints, previews/cancels dimension and point drags, confirms a rectangle,
extrudes it, and verifies one-step sketch undo/redo. It also rejects a competing
CAD command during modal editing, checks revision-overflow setup cleanup,
moved/rotated-frame reopen/extrusion alignment, viewport replacement cancellation
and small-profile picking. Logs, screenshots and isolated config/runtime
files live under `build/sketch-ui-tests` and normal CMake `clean` removes them.
