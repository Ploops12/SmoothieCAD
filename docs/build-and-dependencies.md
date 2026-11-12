# Build and dependencies

## Build target

The current packaging target is for this Linux environment: Blender 5.2.3 using CPython 3.14, GCC-compatible C++20, OCCT 7.9.3, and pybind11 3.1.0. CMake requires exact OCCT, Python, and pybind11 versions and uses installed package configurations. It does not fetch dependencies, invoke external CAD programs, or compile third-party libraries for the user.

Configure and package from the repository root:

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target package_addon
```

The package is written to `build/dist/blendercad-0.1.0.zip`. Install it in Blender using Preferences → Get Extensions → dropdown → Install from Disk. The binary module in this first package is tied to Linux x86-64 and CPython 3.14. Separate build/package outputs will be needed for every supported platform and Blender Python ABI.

To point CMake at non-standard installations, set the normal CMake package search variables (`CMAKE_PREFIX_PATH`, `OpenCASCADE_DIR`, `pybind11_DIR`, and `Python_ROOT_DIR`). A configure failure for a missing or mismatched dependency is intentional; do not silently substitute another version.

## Dependency boundary

| Dependency | Use | Current resolution |
| --- | --- | --- |
| OCCT | Geometry kernel and CAD exchange | Required, exact 7.9.3 CMake package; current adapter uses modeling-data and primitive/topology libraries |
| pybind11 | Native/Python boundary | Required, exact 3.1.0 CMake package |
| Python | Extension ABI and packaging script | Required, exact 3.14 interpreter/development module |
| PlaneGCS | Planar sketch solver | Not integrated yet; adapter and pinned source revision belong to the sketch-constraint milestone |
| OndselSolver | Assembly solver | Not integrated yet; adapter and pinned source revision belong to the assembly milestone |

The OCCT adapter currently owns in-memory `TopoDS_Shape` values behind adapter-local opaque handles and supports box construction, validity checks, volume queries, and release. The addon snapshot does not contain evaluated shapes. Runtime library bundling, license notices for redistributed binary dependencies, and relocation/rpath policy remain packaging work before distribution. No runtime downloads or user-side dependency builds are part of the intended addon install flow.

## Layer ownership

The C++ core is independent of Blender. `Document` owns a stable identity, schema version, revision, geometric tolerance, and named scalar parameters tagged as millimetres, degrees, or unitless. Its binding snapshot is a Python mapping; the addon serializes that mapping as JSON into a hidden scene string property. Snapshot loading rejects unsupported schema versions rather than rewriting them. The addon currently creates an empty document and displays its basic state; parameter editing and feature definitions are not exposed yet.

`GeometryAdapter` is a controlled query boundary; callers receive opaque `ShapeHandle` values rather than kernel pointers. Handles are transient to one adapter instance and must not be serialized. The current box operation is a low-level foundation call, not a saved CAD feature. `SketchConstraintAdapter` accepts solver-neutral line/circle geometry, persistent entity and constraint IDs, a 3D sketch plane, and reports underconstrained, fully constrained, redundant, conflicting, or failed states. It has no PlaneGCS implementation yet. OndselSolver is not used by this milestone.

The three representations remain separate: the scene snapshot stores design definitions; the OCCT adapter owns evaluated shapes; Blender presentation is not yet implemented. Future APIs should preserve this ownership split and associate evaluated results with document revisions before publishing them to Blender.

Future code should preserve three distinct representations: persistent design definitions, evaluated OCCT geometry, and disposable presentation data. Do not persist pointers, object names as identities, or display-mesh topology as authoritative CAD geometry.
