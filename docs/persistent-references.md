# Construction-based persistent topology references

## Implemented scope and ownership

Saved references now identify faces, edges and vertices of evaluated extrusion
and subtractive-extrusion bodies using stable feature/sketch identities and
OCCT construction/change history. They do not save transient topology indices,
display tokens, process handles or geometric nearest-neighbor guesses.

The layers remain separate:

- `geometry_occt.cpp` owns kernel objects and reports typed, evaluated-local
  construction seeds and exact Boolean history through `GeometryTopology`.
- `reference_tracker.cpp` translates those relationships into construction
  anchors, propagated identities and explicit resolution states without any
  OCCT or Blender dependency.
- `evaluation.cpp` maps ordered profile inputs to stable sketch entity and
  endpoint identities, owns evaluated reference maps, and guards currentness.
- `Document` owns named `ReferenceDefinition` records and explicit repair edits.
- Blender picks are disposable; commands translate a current picked element to
  a persistent target before committing its definition.

These named references are currently document-level bookmarks and the API for
subsequent feature consumers. Fillets and face-attached sketches do not yet
consume them. A feature consuming a saved reference must require `RESOLVED`
and propagate unavailable/repair diagnostics, never select the first candidate.

## Construction and operation provenance

Extrusion seeds come from `BRepPrimAPI_MakePrism` first/last/generated
relationships on the exact profile face, ordered edges and shared vertices.
Caps use `cap:start`/`cap:end`; sides and cap edges use the profile entity UUID;
swept edges and end vertices use stable sketch endpoint aliases. A circle seam
uses the source circle's plane-X parameter origin. Polygon traversal begins
with the smallest entity UUID and follows connectivity, independent of saved
entity array order. Coincident endpoints can provide multiple names for one
topological vertex; aliases alone are not a merge.

Subtraction records every operand face/edge/vertex's unchanged `IsSame`,
same-kind `Modified`, cross-kind `Generated` and explicit `IsDeleted`
relationships. No missing relationship is interpreted as deletion. Metadata
stores values/indices only; it does not retain unmanaged operand pointers.

New intersection topology can receive a key derived from its complete named
origin set and creator feature. The key uses length-delimited components, not
an output traversal ordinal. Multiple outputs with indistinguishable
construction provenance remain ambiguous. Capturing such a particular fragment
is refused instead of creating an apparently stable index-based name. Likewise,
unreported/unsupported construction history has no invented target.
Generated names require uniquely resolvable predecessor identities, not merely
nonempty alias lists: a split/ambiguous predecessor cannot be laundered into a
new resolved name just because a later operation happens to leave one output.

Tessellation explicitly maps each original element into its meshed copy,
preserving authoritative element numbering and accumulated face orientation.
Copy-map orientation cannot be assumed; signed triangle-volume checks cover
the regression where a copied reversed cap produced incorrect winding.

## Persistence and queries

Schema **4** adds `references` to the document snapshot. Each record contains:

```json
{
  "id": "<reference UUID>", "name": "Mounting face", "version": 1,
  "body": "<body UUID>", "kind": "face",
  "feature": "<construction creator feature UUID>", "key": "cap:end"
}
```

The document context supplies ownership; body/feature UUIDs are globally unique
within it. Keys are bounded printable non-whitespace ASCII, at most 65536 bytes.
Oversized generated identities are not truncated; inherited names may remain
available but an otherwise unnamed result cannot be captured. Well-formed
unknown keys are retained as repairable intent, not silently rewritten.

Schemas 1/2/3 migrate in memory with an empty reference list while preserving
existing definitions, IDs, rollback, tolerance and revision. Loading does not
rewrite the raw scene string. Unknown fields/types, unsupported document or
reference versions, duplicate IDs and foreign/missing creator/body associations
are rejected. A future-schema payload remains stored for diagnosis.

Native/Python interfaces:

```python
target = evaluator.capture_reference(document, body_id, cad.TopologyKind.FACE, face_index)
reference_id = document.add_reference("Mounting face", target)
report = evaluator.evaluate(document)
resolution = evaluator.resolve_reference(document, document.get_reference(reference_id).target)
# Explicit replacement preserves the reference UUID/name:
document.repair_reference(reference_id, replacement_target)
document.erase_reference(reference_id)
```

Queries compare the complete source document and require current evaluated
geometry. Bookkeeping-only edits reuse geometry while refreshing reference
diagnostics. Loading, undo/redo or a fresh evaluator reconstructs equivalent
identities from definitions/history; old native caches are not necessary.

| State | Meaning and required action |
| --- | --- |
| `RESOLVED` | Exactly one usable construction-derived target |
| `SPLIT` | One source continued into multiple pieces; no automatic piece choice |
| `MERGED` | Distinct predecessor elements coalesced; explicit replacement required |
| `DELETED` | Kernel explicitly deleted it, its known source entity was removed, or its creator no longer exists |
| `AMBIGUOUS` | Provenance is non-unique, unknown or incomplete; no guessed substitute |
| `UNAVAILABLE` | Creator/body is failed, blocked, suppressed or outside current rollback |

Resolution candidates are evaluated-local diagnostics, not saved replacement
identities. Within a feature chain, deleting all but one split fragment does not
silently promote the survivor to the original reference. A uniquely named new
merged region with distinguishable origins can be an explicit replacement;
unidentifiable split fragments still require another nameable region or a later
region-naming capability. Definitions
are reevaluated against the current feature graph, not a hidden cross-revision
topology or undo log. Complex changed derived-origin expressions that cannot be
proven deleted remain ambiguous rather than selecting similar geometry.

Removing a creator feature/sketch rejects dependent saved references by default.
Explicit cascade removes them in the same atomic document edit. Suppression and
rollback retain definitions. Independent-body duplication leaves document-level
bookmarks on their original body; future reference-consuming features will need
an explicit input-remapping clone contract. Body conversion/deletion includes
bookmarks whose creators are removed.

## Blender workflow

Click a current CAD face, then use **Save Picked Face Reference** in the sidebar.
Named reference state and repair diagnostics appear in the sidebar. To repair,
click a different uniquely nameable current face and use **Repair From Picked
Face** on the existing record. **Remove Reference** deletes only that bookmark.
Each command is undoable through the same transaction path as other CAD edits.

Every display publication clears the picked-element token, even when geometry
was cached. A stale pick or last-good failed display cannot create/repair a
reference. Mouse selection currently captures faces; native capture supports
all three topology kinds. Edge/vertex UI selection, highlighting, analytic hit
refinement and reference-consuming modelling features remain follow-up work.

## Verification

`cmake --build build --target check` builds the native tracker executable and
addon before CTest. `reference_tracker` checks synthetic split/merge/ambiguous/
missing-history and explicit-repair boundaries; `native_topology` verifies real
OCCT histories, construction input indices and signed display orientation;
`native_references` covers captures in all domains, earlier constraint edits,
reordered entities, generated hole edges, actual split/deleted faces, equal
geometry with replaced UUIDs, unavailable states and snapshot reconstruction.

`document_snapshot`, `blender_foundation` and `blender_references` cover strict
schema-4 records, legacy migration, future-version preservation, atomic repair/
deletion/overflow, save/reopen, stale picks, body duplication scope and repair
undo/redo. The optional `check_solid_ui` additionally saves a persistent reference
from the face retained by an actual Blender mouse event. These checks establish
the supported reference boundary, not arbitrary CAD naming or the full bracket
acceptance workflow.
