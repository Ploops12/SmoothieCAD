# Constant-radius fillets and feature history

## Blender workflow

Select a current Solid in **Edge** selection mode and select one or more CAD
edges. **Fillet Selected CAD Edges** opens a radius dialog. Enter a literal
millimetre radius or a named `mm` parameter (which may have an expression).
Confirm to create a feature; cancel leaves the document and display unchanged.
**Preview Selected Edge Fillet** instead opens the transactional viewport tool:
it shows a detached ghost result, supports a scalar radius handle and numeric
entry, and confirms/cancels through the same feature definitions. History's
**Handle** edits an existing radius with downstream recomputation. See
[solid feature previews](solid-feature-previews.md) for bindings and input rules.
The operation consumes the body's latest feature, retaining its body identity.
All selected edges use the same radius. OCCT may propagate a contour along
tangent-connected edges; this is not an arbitrary face push/pull tool.

The sidebar's **Feature History** lists evaluation states and diagnostics. Depth and
radius buttons edit the corresponding feature input. Suppression preserves
definitions and blocks consumers rather than bypassing the suppressed feature.
**Before** (or **Edit Inputs** for fillets) exposes an earlier result without deleting later definitions;
**Resume Full History** restores the requested tip. **Earlier**/**Later** reject
dependency-invalid ordering. **Delete** rejects dependents unless its dialog's
explicit cascade option is enabled. These commands use the same atomic document
and Blender undo path as other CAD edits; feature history is not the undo log.

For a broken fillet input, use **Edit Inputs** to roll back immediately before
that fillet. Select replacement edges on its input body, then **Repair and
Resume**. This explicitly replaces the complete target list and resumes the
history in one undoable command. It does not silently select a nearby edge or
rebind a bookmark. Suppressed or failed predecessors must be repaired first.

## Definitions and evaluation

`FilletFeature` belongs to the native document. It stores an input feature UUID,
a literal/parameter radius, and inline persistent edge targets. It does not
require named bookmark records and contains no sketch, display ordinals or
kernel pointers. `add_fillet`, `set_fillet_radius` and `set_fillet_edges` validate
ownership, unique targets, edge kind, ancestor creators and length units before
atomic publication. Nonpositive finite radii and well-formed unknown anchor keys
can remain saved repairable definitions; evaluation reports their failure.

The C++ `feature_sketch` query now returns an optional identity;
`feature_length` supplies the common length input, while `feature_depth` and
`feature_radius` reject the wrong operation type. Python `sketch_id` and `depth`
are `None` for fillets; `radius` is `None` for extrusion/cut features. Callers
must not assume every feature owns a profile sketch.

The evaluator resolves every target against the current input result **before**
cache lookup, requires exactly one resolved edge, and rejects distinct aliases
that resolve to the same edge. It then calls the OCCT adapter with transient
indices. The adapter deep-copies input topology and geometry before invoking
`BRepFilletAPI_MakeFillet`, so failed or successful construction cannot mutate
cached predecessors. Result validity and the document's modelling error budget
are checked by the common geometry storage boundary. No tolerance is relaxed.

Radius, target definitions, resolved inputs and upstream geometry participate
in cache identity. Earlier edits recompute affected descendants. Failure retains
an explicitly stale last-good display, blocks consumers and forbids current
measurement, selection and STEP export. Reconstruction after load/undo uses
definitions and history, not saved native caches.

## Provenance and cloning

Direct OCCT `Modified`, `Generated`, `IsDeleted` and exact unchanged membership
remain distinct. The fillet builder can report new faces without reporting
their new edges/vertices. For these globally untraced output elements, the
adapter additionally records `boundary_generated`: exact boundary membership
of directly reported output faces. This is separate diagnostic metadata, not a
fabricated OCCT `Generated` result or geometric proximity match.

The reference adapter names these elements from their complete source-anchor
sets using length-delimited `boundary:` keys. It never treats such a boundary
as continuation of a deleted source edge. Missing/ambiguous origins and multiple
elements with identical provenance remain ambiguous; capture is refused.
Generated surfaces and uniquely distinguishable circular boundary edges can
therefore be referenced by later fillets while preserving explicit failure
when topology no longer provides a unique identity.

Independent Solid duplication remaps inline targets, including nested
construction/`derived:`/`boundary:` keys, through the complete cloned UUID map.
Origin signatures are sorted again after remapping. Unknown or malformed keys
are preserved as repairable intent, never normalized into an apparently valid
target. Document-level bookmarks stay on the original body. Radius parameters
remain shared document parameters, as with existing feature depth inputs.

## Persistence and verification

Schema **7** adds version-1 `fillet` records with exactly `id`, `name`, `version`,
`type`, `body`, `input`, `radius`, `edges` and `suppressed`. Radius encodes either
`{"value_mm": number}` or `{"parameter": name}`. Each inline edge encodes
`body`, `kind`, `feature`, `key`. Schemas 1–6 migrate without changing definitions
or rewriting the raw scene property on load. Future versions fail closed.
Extrusion/cut record formats and numeric/parameter-bound sketch versions remain
unchanged.

`cmake --build build --target check` includes `native_fillets` and packaged
`blender_fillets`: analytical volume/radius checks, small/ordinary/furniture
scales, arbitrary planes, immutable predecessors, invalid inputs, selective
caching, stale/blocked recovery, reference repair, generated-edge chaining,
independent duplication, snapshot/save/reopen, undo and STEP round trips.
The optional `check_fillet_ui` target exercises actual window selection,
dialogs, cancellation, history and undo; generated evidence is in
`build/fillet-ui-tests`, removed by normal CMake `clean`.

Supported scope is OCCT constant-radius edge filleting on valid solid inputs.
Variable-radius laws, chamfers and general
edge-chain editing are not implemented. Some complex blends lack uniquely
identifiable provenance; explicit repair can select another nameable region,
but cannot manufacture missing construction history. This feature is not full
bracket or relocatable-package acceptance.
