# BlenderCAD

BlenderCAD is an ordinary Blender addon with a separate C++ computational core. The current implementation provides a versioned design document, OCCT/PlaneGCS services and parametric modelling commands with managed Solid display. A reference constrained sketch-to-bracket workflow is verified inside Blender at three scales; broader CAD capabilities remain under development.

## Current implementation

- `src/blendercad/`: persistent document/parameter definitions and geometry/solver interfaces.
- `bindings/`: pybind11 extension exposing document and geometry APIs.
- `addon/blendercad/`: Blender integration, document commands, managed Solid display and scene-embedded snapshots.
- `tools/`: addon packaging utility.
- `tests/`: geometry, sketch-solving, Solid/persistence, event-routing and CMake cleanup checks.
- `docs/`: architecture and build/dependency notes.

The native core provides stable IDs, versioned snapshots, atomic revisions, unit-tagged parameters, persistent sketches and extrusion/subtractive/constant-radius fillet feature definitions. PlaneGCS solves sketch constraints; a revision-aware evaluator drives OCCT, caches unchanged results and retains explicitly stale displays on failure while blocking stale measurements/exports. Features support named length inputs, suppression, dependency-checked reorder/deletion and rollback. Construction-based topology references support explicit split/merged/deleted/ambiguous diagnostics and named-reference repair. The addon preserves definitions in Blender scene properties and presents bodies through non-mesh hosts with managed meshes. Face/edge/vertex selection, GPU highlights and kernel measurements now complement numeric rectangle/depth/parameter commands, definition duplication, conversion, deletion and load/undo reconstruction. Fillets consume persistent edge targets; the sidebar exposes suppression, rollback, dependency-checked reorder/deletion and explicit fillet input repair. The reference bracket checks cover modal sketch construction, earlier parameter edits, undo/redo, save/reopen, Cycles rendering and analytic STEP exchange, including actual addon installation and restart.

## Build and package

The initial build targets Linux, Blender 5.2.3 / CPython 3.14, C++20, pybind11 3.1.0, and OCCT 7.9.3. CMake acquires pinned dependency sources and builds them as needed; matching installed OCCT and pybind11 packages can be reused. There is no separate dependency-download step.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target package_addon
cmake --build build --target check
```

Install `build/dist/blendercad-0.1.0.zip` through Blender Preferences → Get Extensions → dropdown → Install from Disk. It includes a private OCCT/runtime bundle and exchange resources; it needs no host OCCT installation, runtime downloads or user compilation. It targets this Linux CPython 3.14/Blender ABI, not other platforms or older Linux distributions.

See [Runtime packaging](docs/runtime-packaging.md) for manifests, ABI requirements, notices/source companions, relocation verification and offline rebuilding.

See [Build and dependencies](docs/build-and-dependencies.md) for dependency discovery, platform constraints, and the current integration boundary.

See [Geometry service and verification](docs/geometry-service.md) for native APIs and focused checks, and [Working-slice acceptance](docs/roadmap.md) for remaining milestone requirements.

See [Document snapshots](docs/document-snapshots.md) for schema versions, sketch ownership, edit semantics and load failure behavior.

See [Parameter expressions](docs/parameter-expressions.md) for dimensional arithmetic, dependency validation, Blender editing and schema-5 definitions.

See [Sketch dimensions](docs/sketch-dimensions.md) for parameter bindings, numeric dimension editing, solver resolution and schema-6 persistence.

See [Interactive sketches](docs/interactive-sketches.md) for viewport drawing/constraints/dragging, transactional confirmation, and sketch extrusion/cuts.

See [Fillets and history](docs/fillets-and-history.md) for persistent edge inputs, radius editing, explicit repair and schema-7 records.

See [STEP export](docs/step-exchange.md) for current-result validation, part-local millimetre coordinates and atomic file publication.

See [Bracket acceptance](docs/bracket-acceptance.md) for the reference model, three-scale Blender checks, installation evidence and remaining scope.

See [Features and evaluation](docs/features-and-evaluation.md) for body ownership, dependency history, caching, failures and query/export guards.

See [Managed Solid presentation](docs/solid-integration.md) for the Blender workflow, picking/materials, commands, undo and documented integration gaps.

See [Persistent references](docs/persistent-references.md) for construction provenance, schema-4 identities, diagnostics, repair and current naming limits.

See [CAD selection and queries](docs/selection-and-queries.md) for exact hit/measurement services, edge/vertex picking, snapping, GPU highlighting and precision limits.
