# Transactional solid-feature previews and handles

## Blender workflow

The sketch list's **Preview Extrude** and **Preview Cut** buttons start an extrusion or
subtractive extrusion from that saved sketch. A cut requires exactly one active
owning Solid. **Preview Selected Edge Fillet** uses the selected persistent CAD
edges and the body's current history tip. In **Feature History**, **Handle**
adjusts an existing extrusion/cut depth or fillet radius, including earlier
features whose downstream results must recompute.

The existing numeric dialogs and direct Python commands remain available. They
share the same native feature-definition mutations with the preview tool; there
is no alternate mesh-modelling implementation.

| Input | Behaviour |
| --- | --- |
| Drag the bright endpoint | Adjust depth along the sketch normal, or the scalar radius |
| `G`, move, click | Adjust the same input without first hitting the endpoint |
| `D`, type, Enter | Enter numeric millimetres, or `=name` to bind a length parameter |
| `U` | Explicitly replace a parameter binding with its current literal value |
| `R` | Reset the entire detached candidate to its initial state |
| Enter outside a field/drag | Confirm one undoable modelling interaction |
| Esc during a field/drag | Restore its exact detached baseline |
| Esc or right-click otherwise | Cancel the whole preview |

A numeric adjustment of a bound input changes its literal named parameter, not
the feature's binding. All users of that parameter participate in native
evaluation. Expression-defined drivers cannot be dragged or numerically
overwritten; edit their expression/upstream inputs, bind another parameter, or
explicitly use `U` to detach this feature. Type arithmetic in the document's
parameter editor, not in a second expression parser.

Depth handles follow the profile's positive right-handed normal. Radius handles
are scalar controls on a visible local axis, not a physical blend-centre
placement or arbitrary face push/pull. If a depth axis is viewed end-on, screen
horizontal motion supplies the scalar adjustment at the anchor's viewport
scale. Middle-mouse and wheel navigation remain available outside a drag.

## Candidate display and failures

The cyan translucent shell and CAD edge lines are an explicitly X-ray **ghost
result** over the unchanged committed Solid. They are not a second selectable
Solid, normal shaded replacement, pick source or render object. This permits
seeing a cut's candidate boundaries without mutating or hiding saved display
geometry. Only the target body's ghost is drawn; shared-parameter changes to
other bodies are evaluated and checked, then displayed normally on confirmation.

An invalid geometric candidate retains the last valid ghost, colors it amber,
shows its diagnostics and disables confirmation. Its length value and failure
remain in the draft so the user can recover, reset or cancel. The tool does not
publish stale geometry as a successful preview. Preexisting independent failures
do not prohibit editing a current target, but newly failed or blocked features
elsewhere in the document prevent confirmation of a shared-parameter change.

The document's modelling budget remains unchanged. The ghost consumes native
bulk tessellation and semantic edge samples; its display approximation is not a
measurement or modelling tolerance. CAD queries and exports continue to use
only the committed evaluated geometry.

## Ownership and publication

`feature_commands.py` has no Blender API dependency. `Operation` describes a
creation or input edit; `create_feature` and `set_feature_input` are shared by
the numeric command service and detached `Session`. A session copies the design
snapshot and forks the native evaluator. Feature/body identities are created
once and retained across updates. `fork()` supplies exact local-action baselines
without accumulating a permanent revision for every mouse move.

Candidate evaluation uses private caches and retains a separately identified
last-valid display document. No source document, published evaluator, managed
mesh, Blender object, selection or picking token changes before confirmation.
Confirmation uses the normal `commands.commit` evaluation/publication journal
and selects the affected body. New bodies retain the explicit sketch-edit
placement frame; cuts and edits retain their owning Solid's placement.

`feature_editor.py` owns modal input and transaction authorization. Source
snapshot, scene units, runtime identity, ownership, host placements and viewport
lifetimes are guarded. Competing addon commits, exports, picks and sketch tools
are rejected while a preview owns the transaction. Load/undo/redo/render
initialization and unregister discard drafts and release handlers/timers/cursor
state. A timer also checks invalidation when the pointer is idle. Render-thread
callbacks only signal cancellation; Blender data cleanup runs on the main
thread. An ordinary addon still cannot veto arbitrary scripts or all native
operators; external changes cancel the preview without overwriting those changes.

`feature_view.py` owns disposable, lazily created GPU batches and projection.
Draw callbacks restore blend/depth state, never allocate scene meshes and never
perform native geometry evaluation. GPU data belongs to one candidate overlay
and is discarded when its arrays change or the tool ends.

## Checks and scope

```sh
cmake --build build --target check -j2
cmake --build build --target check_feature_preview_ui -j2
```

The background `blender_feature_preview` check covers analytic dimensions,
volumes/radii and cut rays, stable identities, source/cache/display/pick
immutability, failed-candidate retention and recovery, exact baseline forks,
shared parameter failures, expression-driver protection, arbitrary planes,
unavailable/stale source rejection and publication undo/redo. The real-window
target verifies real endpoint and keyboard drags, exact local cancellation,
independent extrusion creation/undo, parameter-preserving numeric confirmation
with one-step undo/redo, precise through cuts, actual fillet edge selection,
invalid cut/fillet retention and recovery, expression protection and explicit
unlink/reset. It also checks competing commands, source/placement/viewport
invalidation, resource cleanup and save/reopen with an active uncommitted draft.
Five reviewed GPU screenshots cover valid and invalid ghosts. Its generated evidence and
isolated application profile live in `build/feature-preview-ui-tests`, owned by
normal CMake `clean`.

There is no persistence schema, native dependency or kernel tolerance change.
This adds solid feature input tools, not a complete sketch toolbar,
multiple profile regions, additive unions, variable-radius
fillets or a native Blender gizmo integration. Candidate evaluation is synchronous
on Blender's main thread; complex parts may pause input during evaluation. A
background job system would need source-revision checks before publication and
must not access Blender data off-thread.

[Face-attached sketches](face-attached-sketches.md) now supply persistent upstream
planes to these tools; depth handles query their evaluated frame rather than a
saved placeholder plane.
