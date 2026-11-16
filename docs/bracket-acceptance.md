# Mechanical bracket workflow and acceptance

Status: the reference single-part workflow passes the background checks and all
three real-window cases, including installed-addon restart/edit/export. This
does not complete broader CAD or assembly capabilities. CAM, FEA and CFD are
separate modules outside the current CAD scope.

## Reference part

The reference part is a right-angle bracket, not a box standing in for a bracket.
Its XY profile is the closed line chain `(0,0) → (50,0) → (50,5) → (5,5) →
(5,40) → (0,40) → (0,0)` in millimetres. Extrusion through 30 mm produces the
bracket width along Z. A radius-3 mm through hole crosses each perpendicular
mounting arm; a radius-1 mm fillet rounds the outside sweep edge at `(50,0)`.

| Case | Scale | X span × Y height × Z width (mm) | Thickness | Hole radius | Fillet radius |
| --- | --- | --- | --- | --- | --- |
| Small mechanical | 0.01 | 0.5 × 0.4 × 0.3 | 0.05 | 0.03 | 0.01 |
| Ordinary | 1 | 50 × 40 × 30 | 5 | 3 | 1 |
| Furniture | 20 | 1000 × 800 × 600 | 100 | 60 | 20 |

Every case keeps the document's **0.001 mm** modelling budget. Camera clipping,
view distance and light/camera setup are presentation settings, not changes to
the CAD tolerance. Blender numeric properties/cursor placements can quantize
input values; numerical checks distinguish those stored inputs from kernel error
rather than silently increasing the budget.

The nominal volume is
`[(50×5 + 5×35)×30 − 2π×3²×5 − (1−π/4)×1²×30] × scale³`,
approximately `12460.81860607884 × scale³ mm³`. The two bores lie away from the
rounded corner and do not intersect each other, so this analytical subtraction
is valid. Measurements and STEP checks use CAD geometry, not triangle volume.

## Construct entirely in Blender

1. Create a CAD document. Add length parameters `span=50`, `height=40`,
   `thickness=5`, `width=30`, `hole_radius=3`, `edge_radius=1` (scale the values
   for another case). Add expressions `rise=height-thickness`, `foot_x=span*0.6`,
   `arm_y=height*0.5`, `mid_width=width*0.5`, `through_depth=thickness*3` with length units.
2. Start an XY sketch at the origin. Use `L` and Shift-clicks for the six
   horizontal/vertical segments above, snapping the first/last endpoints to the
   origin and connecting each successive endpoint. Select whole lines with `S`.
   Use `D` to bind the first horizontal length to `=span`, the short right
   vertical to `=thickness`, the inside vertical to `=rise`, and the top
   horizontal to `=thickness`. The profile should be fully constrained.
3. Confirm the sketch and use **Extrude** with parameter `width`.
4. Move Blender's 3D cursor to `(0, 2*thickness, 0)` in world millimetres while
   keeping this unplaced Solid active. Start an XZ sketch: its positive normal
   points along −Y. Draw a circle at `(foot_x, mid_width)` in sketch coordinates.
   Bind radius with `D`; select origin then circle center and use signed `A`/`B`
   dimensions for the two center coordinates. Confirm and extrude with **Cut
   selected Solid**, depth parameter `through_depth`.
5. Move the cursor to `(-thickness,0,0)`. Start a YZ sketch (normal +X), draw the
   second circle at `(arm_y, mid_width)`, constrain its center/radius, confirm,
   and cut with `through_depth` again.
6. Use Edge selection, click the exterior width edge at `(span,0,width/2)`,
   and **Fillet Selected CAD Edges** with `edge_radius`.
7. Edit the earlier `span` parameter, then undo/redo. The profile, foot-hole
   position, cuts and fillet should recompute without replacing stable design
   identities. Save/reopen the `.blend` file, render the managed Solid normally,
   and use **Export Solid as STEP**. STEP exports part-local millimetres; it
   does not contain the editable feature/constraint history.

The [sketch editor](interactive-sketches.md), [fillet/history controls](fillets-and-history.md)
and [STEP exporter](step-exchange.md) share the document command/evaluation path.
No external CAD application or mesh Edit Mode constructs this part.

The cutters deliberately start outside the stock and overtravel it. Blender's
cursor position is float precision: trying to end a thickness-length cut exactly
on a CAD face left a microscopic skin in the furniture-scale window check.
Explicit overtravel expresses the intended through hole without changing the
kernel tolerance, silently snapping geometry or weakening the ray checks. These
are positioned sketch planes, not an implemented associative-face or through-all
feature; arbitrary later thickness edits can require repositioning them.

## CMake checks and evidence

```sh
cmake --build build --target check -j2
cmake --build build --target check_bracket_ui -j2
cmake --build build --target check_installation -j2
```

`blender_bracket` supplements the event workflow with precise service checks
through Blender operators and detached sketch commands at all three scales:
constraints/parameters, analytical dimensions/radii/volume, earlier edits,
failure/blocked propagation, export guards, save/reopen, undo/redo and Cycles
rendering. The raw OCCT adapter is used only for independent STEP readback,
not for bypassing sketch/feature construction.

`check_bracket_ui` uses separate isolated Blender windows for each scale. It
draws the actual L profile and both circles with modal mouse/key events, enters
parameter dimensions, creates the features with public operators, selects the
fillet edge by a real viewport click, edits/undoes/redoes an earlier parameter,
saves/reopens and exports. Camera/cursor setup does not inject design geometry.
Screenshots read the completed GPU framebuffer, including the history panel.
Focused window targets `check_bracket_ui_small`, `check_bracket_ui_ordinary`
and `check_bracket_ui_furniture` are also available.

`check_installation` runs the bracket fixture and invokes Blender's actual
`preferences.addon_install`/`addon_enable` operators. It verifies the installed
module and private kernel paths, opens the saved bracket, edits an earlier
parameter and exports STEP. A separate offline Blender process reloads saved
isolated preferences, automatically enables the addon and repeats editing/export.
No manual extraction or `sys.path` injection stands in for addon installation.
The profile, preferences and temporary application files stay under the build
tree and do not modify the user's Blender configuration.

Artifacts live under `build/bracket-tests` and
`build/bracket-ui-tests/{small,ordinary,furniture}`. Normal CMake `clean` removes
their logs, models, STEP files, images and isolated Blender config/recovery data.
Installer/restart evidence is under `build/installation-tests` and is cleaned
through the same pipeline. Window checks suppress kernel core dumps; Blender's
own diagnostics/recovery files use their isolated runtime directory.
The installed ZIP includes its runtime; no dependency download or compilation
runs during these Blender workflows.

## Scope not established by this gate

This model uses explicit planar sketch frames, one region per profile, two
individual through cuts and a constant-radius edge fillet. It does not establish
associative face-attached planes, multiple profile regions, arbitrary surfacing,
variable-radius blends, assemblies, spatial sketch solving, CAM/FEA/CFD or
technical drawing sheets. Solid feature previews and depth/radius handles now
have their [own transaction tools](solid-feature-previews.md). Full toolbar/gizmo coverage,
broader native operation protection and the documented rendering/selection gaps
remain separate work even after this reference part passes.

[Associative planar supports](face-attached-sketches.md) now have a separate
moving-pocket workflow and explicit repair checks; the historical reference
bracket above continues to demonstrate its explicitly positioned-plane recipe.
