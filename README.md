# BlenderCAD

BlenderCAD is an ordinary Blender addon with a separate C++ computational core. The current implementation establishes the build/package path, a versioned design document, and a minimal OCCT geometry service. It does not yet provide interactive CAD modelling tools.

## Current implementation

- `src/blendercad/`: persistent document/parameter definitions and geometry/solver interfaces.
- `bindings/`: pybind11 extension exposing document and geometry APIs.
- `addon/blendercad/`: Blender addon entry point, status panel, and scene-embedded document snapshot.
- `tools/`: addon packaging utility.
- `docs/`: architecture and build/dependency notes.

The native core provides stable document IDs, schema-versioned snapshots, content revisions, named parameters with basic unit tags, and an OCCT adapter for box construction, validity, volume, and shape release. The addon can create and persist an empty document snapshot in Blender scene properties. These geometry calls are foundation APIs, not a user-facing feature history. Feature evaluation, sketches, solver implementations, topology provenance, and modelling commands are not implemented yet.

## Build and package

The initial build targets Linux, Blender 5.2.3 / CPython 3.14, C++20, pybind11 3.1.0, and OCCT 7.9.3. Dependencies are resolved from the build machine; CMake does not download or build third-party sources.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target package_addon
```

Install `build/dist/blendercad-0.1.0.zip` through Blender Preferences → Get Extensions → dropdown → Install from Disk. This first package contains a Linux CPython 3.14 extension and is not portable to other platforms or Python ABIs.

See [Build and dependencies](docs/build-and-dependencies.md) for dependency discovery, platform constraints, and the current integration boundary.
