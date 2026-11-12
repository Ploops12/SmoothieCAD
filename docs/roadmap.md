# Working-slice acceptance

The active objective is the first full working single-part slice, following the milestone order in `AGENTS.md`. It is **not complete** when a native primitive can be built or the addon can be imported. Assemblies and later engineering modules are outside this initial acceptance scope.

## Current state and next dependencies

1. **Bootstrap:** CMake targets, bindings, addon ZIP skeleton and build documentation exist. Runtime dependency bundling and relocatable distribution remain incomplete.
2. **Core foundation:** stable IDs, basic unit-tagged parameters, persistent sketches/features, versioned snapshots/migration, PlaneGCS and OCCT services exist. Feature evaluation now owns cached bodies, diagnostics, last-valid displays and current-result/export guards. Extrusion/subtraction, suppression, rollback and dependency-checked reorder/deletion work through native APIs. CAD topology provenance/persistent reference repair remain incomplete. OndselSolver belongs to the later assembly adapter and is not required for single-part acceptance.
3. **Solid integration:** pending. Non-mesh selectable host, managed mesh, revision-aware face/edge/vertex picking, materials, centralized capability checks, supported commands, explicit conversion, load and undo/redo reconstruction are required. Audit built-in entry points and document unavoidable addon enforcement gaps.
4. **Parametric modeling:** native sketch solving and extrusion/subtraction/history foundations exist, but Blender modelling tools are pending. Add sketch interaction/rectangles, parameter arithmetic/dimensional validation/cycles, fillets, history UI and handles, transactional previews and cancellation. Preserve CAD topology references or expose explicit repair diagnostics rather than silently changing the selected region.
5. **Acceptance:** pending. Create and edit a mechanical bracket entirely in Blender, including sketch constraints and parameters, additive/subtractive features and fillets. Verify save/reopen, undo/redo, earlier-feature edits, failure/blocked-dependent behavior, material/rendering behavior, and STEP round trips. Repeat representative small-mechanical, ordinary and furniture-scale workflows without relaxing the default error budget. Verify the installed package without runtime downloads or user compilation.

Each completed capability must have durable documentation and proportionate verification before integration. Native geometry checks are evidence only for the kernel-service boundary; they do not stand in for the interactive bracket workflow.
