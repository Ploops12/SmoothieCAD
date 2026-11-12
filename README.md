# BlenderCAD

BlenderCAD is an ordinary Blender addon with a separate C++ computational core. The current implementation establishes the build/package path, a versioned design document, and a minimal OCCT geometry service. It does not yet provide interactive CAD modelling tools.

## Current implementation

- `src/blendercad/`: persistent document/parameter definitions and geometry/solver interfaces.
- `bindings/`: pybind11 extension exposing document and geometry APIs.
- `addon/blendercad/`: Blender addon entry point, status panel, and scene-embedded document snapshot.
- `tools/`: addon packaging utility.
- `tests/`: focused geometry, sketch-solving, Blender persistence, and CMake cleanup checks.
- `docs/`: architecture and build/dependency notes.

The native core provides stable IDs, versioned snapshots, atomic revisions, unit-tagged parameters, persistent sketches and extrusion/subtractive feature definitions. PlaneGCS solves sketch constraints; a revision-aware evaluator drives OCCT, caches unchanged results and retains explicitly stale displays on failure while blocking stale measurements/exports. Features support named length inputs, suppression, dependency-checked reorder/deletion and rollback. The addon preserves these definitions in Blender scene properties. These are foundation APIs: Solid presentation, topology provenance and interactive modelling commands are not implemented yet.

## Build and package

The initial build targets Linux, Blender 5.2.3 / CPython 3.14, C++20, pybind11 3.1.0, and OCCT 7.9.3. CMake acquires pinned dependency sources and builds them as needed; matching installed OCCT and pybind11 packages can be reused. There is no separate dependency-download step.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target package_addon
cmake --build build --target check
```

Install `build/dist/blendercad-0.1.0.zip` through Blender Preferences → Get Extensions → dropdown → Install from Disk. This first package contains a Linux CPython 3.14 extension and is not portable to other platforms or Python ABIs.

See [Build and dependencies](docs/build-and-dependencies.md) for dependency discovery, platform constraints, and the current integration boundary.

See [Geometry service and verification](docs/geometry-service.md) for native APIs and focused checks, and [Working-slice acceptance](docs/roadmap.md) for remaining milestone requirements.

See [Document snapshots](docs/document-snapshots.md) for schema versions, sketch ownership, edit semantics and load failure behavior.

See [Features and evaluation](docs/features-and-evaluation.md) for body ownership, dependency history, caching, failures and query/export guards.
