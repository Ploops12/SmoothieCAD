# Working-slice acceptance

The active objective is the first full working single-part slice, following the milestone order in `AGENTS.md`. It is **not complete** when a native primitive can be built or the addon can be imported. Assemblies and later engineering modules are outside this initial acceptance scope.

## Current state and next dependencies

1. **Bootstrap:** CMake targets, bindings, addon ZIP skeleton and build documentation exist. Runtime dependency bundling and relocatable distribution remain incomplete.
2. **Core foundation:** stable IDs, basic unit-tagged parameters, persistent sketch definitions, versioned scene snapshots/migration, OCCT geometry/query/tessellation/STEP APIs and an embedded PlaneGCS adapter exist. Native solver and persistence checks cover line/circle definitions and diagnostics; feature definitions, evaluation ownership and reference history remain incomplete. OndselSolver belongs to the later assembly adapter and is not required for single-part acceptance.
3. **Solid integration:** pending. Non-mesh selectable host, managed mesh, revision-aware face/edge/vertex picking, materials, centralized capability checks, supported commands, explicit conversion, load and undo/redo reconstruction are required. Audit built-in entry points and document unavoidable addon enforcement gaps.
4. **Parametric modeling:** pending. Constrained lines/circles/rectangles on arbitrary planes; parameter arithmetic, units and dependency cycles; extrusion/subtraction and constant-radius fillets; ordered dependency history, editing/suppression/rollback and transactional previews. Preserve references or expose explicit repair diagnostics rather than silently changing the selected region.
5. **Acceptance:** pending. Create and edit a mechanical bracket entirely in Blender, including sketch constraints and parameters, additive/subtractive features and fillets. Verify save/reopen, undo/redo, earlier-feature edits, failure/blocked-dependent behavior, material/rendering behavior, and STEP round trips. Repeat representative small-mechanical, ordinary and furniture-scale workflows without relaxing the default error budget. Verify the installed package without runtime downloads or user compilation.

Each completed capability must have durable documentation and proportionate verification before integration. Native geometry checks are evidence only for the kernel-service boundary; they do not stand in for the interactive bracket workflow.
