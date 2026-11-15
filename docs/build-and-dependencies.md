# Build and dependencies

## Pipeline

Build-host prerequisites are a C++20 compiler/build tool, CMake 3.25+, Git, the CPython 3.14 development SDK, and Blender 5.2.3 for acceptance checks. These are the host toolchain. Native library acquisition, compilation, addon packaging, checks and cleanup are orchestrated by CMake; do not manually download libraries or run a separate provisioning script.

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target package_addon -j2
cmake --build build --target check -j2
```

On Linux with the optional host tool `xvfb-run` installed, CMake additionally
provides `cmake --build build --target check_solid_ui`,
`cmake --build build --target check_sketch_ui`, and
`cmake --build build --target check_fillet_ui`. These isolate Blender windows,
configuration and generated files under the build tree and check actual
event-queue routing/undo, sketch drawing/constraints, fillet selection/dialogs/history and cancellation.
They are separate from the headless `check` target; see
[Solid integration](solid-integration.md) and [interactive sketches](interactive-sketches.md)
for coverage and limits. Normal `clean` removes these targets' logs, screenshots
and temporary files.

The first configure acquires pinned build dependencies. Subsequent builds reuse the build-local cache. Matching installed OCCT 7.9.3 and pybind11 3.1.0 packages are preferred by default; if absent, CMake fetches and builds their pinned source revisions. `-DBLENDERCAD_USE_SYSTEM_DEPENDENCIES=OFF` forces those source paths. The solver's Eigen/Boost header dependencies are always acquired at the recorded versions, and PlaneGCS itself is vendored at a fixed upstream revision. `cmake/Dependencies.cmake` is the dependency lock/build entry point. It verifies archive hashes or pinned Git object identities; `cmake/PlaneGCS.cmake` checks the vendored source hashes.

No dependency acquisition or compilation runs inside the addon. The package is written to `build/dist/blendercad-0.1.0.zip`. Its binary currently targets this Linux CPython 3.14 ABI. Runtime library bundling/relocation and complete redistribution artifacts remain unfinished, so this ZIP is still a development artifact requiring the build machine's OCCT runtime. Do not treat a successful build as installed-package acceptance on a different machine.

To use non-standard host SDK/package locations, use `Python_ROOT_DIR`, `CMAKE_PREFIX_PATH`, `OpenCASCADE_DIR`, and `pybind11_DIR`. CMake does not silently use another OCCT, Python or pybind11 version. For an already populated tree, configure with `-DBLENDERCAD_OFFLINE=ON`; an empty offline cache cannot satisfy missing dependencies. Standard `FETCHCONTENT_SOURCE_DIR_<NAME>` overrides can use pre-populated source trees when offline builds need explicit local inputs.

## Pins and ownership

| Dependency | Use | Source identity |
| --- | --- | --- |
| OCCT 7.9.3 | Geometry, topology, tessellation, exchange | `a016080bf6738d6aeae020badee4e888ad1540a5` ([release](https://github.com/Open-Cascade-SAS/OCCT/releases/tag/V7_9_3)) or exact installed package |
| pybind11 3.1.0 | C++/Python boundary | `97bf890db679505a14dfe547a5e77bb2bd05dc90` ([release](https://github.com/pybind/pybind11/releases/tag/v3.1.0)) or exact installed package |
| Eigen 3.4.0 | PlaneGCS linear algebra headers | `3147391d946bb4b6c68edd901f2add6ac1f31f8c` ([upstream](https://gitlab.com/libeigen/eigen)) |
| Boost 1.88.0 | PlaneGCS Graph/Math headers | Release archive SHA256 `46d9d2c06637b219270877c9e16155cbd015b6dc84349af064c088e9b5b12f7b` ([published checksum](https://live.boost.org/users/history/version_1_88_0.html)) |
| PlaneGCS | Planar sketch constraints | FreeCAD `5a6be36bc32900ea0d041b92792b0ae07ea31400`; see [embedding notes](planegcs.md) |
| OndselSolver | Later assembly constraints | Not integrated; not needed for the initial single-part slice |

Blender supplies NumPy for bulk display arrays and native checks; CTest runs the native checks in Blender rather than requiring a separate host NumPy installation. The addon and native libraries never invoke an external CAD application.

## Cleanup

```sh
# Remove compiled outputs and generated addon packages; retain downloaded sources.
cmake --build build --target clean

# Purge build-local dependency caches. Reconfigure before the next build.
cmake --build build --target purge_dependencies

# Remove an entire generated build tree, including cache/configuration.
# This also works when initial dependency acquisition/configuration failed.
cmake -DBLENDERCAD_BUILD_DIR=build -DMODE=all -P cmake/Clean.cmake
```

Full cleanup requires an out-of-source build marked by this project and refuses the source tree or an ancestor containing it. Dependency cleanup removes only `_deps/` and legacy `dependencies/` under that build tree; it never follows a custom FetchContent cache location outside the build directory. Source, vendored dependency files and Git state are not cleanup targets.

Filleting links the existing OCCT `TKFillet` toolkit; both installed-library and
source-build dependency lists include it. This does not introduce another library
project or a runtime download. The source-build fillet path has not yet received
the current feature acceptance suite.

The Linux dependency-lifecycle verification exercised matching installed packages and forced CMake source builds of OCCT/pybind11; both passed the four then-existing CTest groups. A normal `clean` followed by `check` also passed without reacquiring cached sources. The actual dependency-purge target and full-tree script were exercised in a disposable project build, including preservation of external source inputs. The current `check` target additionally covers document/sketch/feature snapshots and feature evaluation/history. These checks do not establish portability to other platforms or final runtime-library packaging.

## Layer ownership

The C++ document core owns stable identities, revisions, tolerance, named unit-tagged values and persistent sketch/feature definitions. The Python binding translates versioned mappings; the addon serializes these definitions into a hidden Blender scene property. Unsupported future snapshots are rejected and preserved. The addon exposes initial numeric rectangle/depth commands and managed Solid presentation, not the complete bracket workflow. Fillet and history controls are described in
[fillets and history](fillets-and-history.md). See [document snapshots](document-snapshots.md) for schema and mutation contracts and [Solid integration](solid-integration.md) for publication, commands and undo.

The OCCT adapter owns transient evaluated shapes and exposes controlled queries, never kernel pointers. A separate feature evaluator owns cached results, current-result guards and explicitly stale displays. [CAD measurements and precise picks](selection-and-queries.md) query evaluated B-rep geometry, not display triangles. Display indices are translated through construction/Boolean history into [persistent anchors and repair states](persistent-references.md). The sketch adapter owns PlaneGCS objects and maps diagnostics to persistent constraints. Solver pointers/caches are not document data. The addon publishes managed meshes, selection overlays and disposable caches reconstructed after load/undo. Rendering, reference/selection commands, query guards and capability checks run through CMake; check also builds the native reference-tracker test executable.
