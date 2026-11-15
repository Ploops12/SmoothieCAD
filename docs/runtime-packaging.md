# Linux runtime bundles and rebuild materials

## Build pipeline

`cmake --build build --target package_addon -j2` builds the extension, stages its
runtime, collects notices, creates a source companion and writes the installable
ZIP. There is no runtime download or compilation step. Intermediate files stay
under `build/package`; normal CMake `clean` removes them, ZIP/source artifacts and
relocation/rebuild-check outputs. Dependency sources remain cached unless purged.

| Artifact | Contents |
| --- | --- |
| `build/dist/blendercad-0.1.0.zip` | Addon Python, native module, private `.libs`, exchange resources, runtime manifest, notices |
| `build/dist/blendercad-0.1.0-sources.tar.gz` | Intentional project sources/build scripts, vendored PlaneGCS/compatibility files, OCCT/Eigen/Boost/pybind11 rebuild inputs, notices, per-file hashes and relinking instructions |
| `build/package/blendercad/runtime-manifest.json` | Actual module/library hashes, SONAMEs, dependency closure, resource hashes, source paths and required ELF symbol versions |

`stage_runtime` uses CMake's `GET_RUNTIME_DEPENDENCIES` and `readelf`, not a
hard-coded list of the directly linked OCCT toolkits. Installed STEP libraries
also bring XCAF/visualization dependencies. All resolved non-platform libraries
are copied under their required SONAME names. Unresolved/conflicting libraries,
foreign architectures and unrelocatable library search paths fail packaging.
Discovery enforces the selected OCCT target directory, rejecting mixed host
toolkits or an older staged bundle. A focused staging check also verifies that
unmarked existing output directories are not erased.

The extension has Linux **DT_RPATH `$ORIGIN/.libs`**. Its transitive semantics
are deliberate: DT_RUNPATH on the extension alone cannot locate indirect
dependencies of installed libraries that have no search path of their own.
This uses linker facilities rather than adding a binary-patching dependency.
The native development module links to the same generated private directory.

The explicit platform exclusions are glibc/loader components, `libstdc++` and
`libgcc_s`; these belong to the running Blender/Linux process and are recorded,
not silently bundled over Blender's loaded C++ runtime. Supporting font/X11/
compression libraries are bundled even if this Blender already loads them.
This package targets this Linux system, Blender 5.2.3 and CPython 3.14, not older
Linux distributions or another Python ABI. The current installed-library build
requires GLIBC 2.44 and GLIBCXX 3.4.32; the actual artifact's manifest is authoritative.

## Installed initialization and resources

`addon/blendercad/runtime.py` checks manifest format, required files and hashes
and complete dependency/file coverage before importing the extension. Missing/corrupt files leave CAD unavailable with
an actionable installation error; they do not trigger downloads or fall back to
another host OCCT installation. Linux mapped-library inspection rejects a foreign
OCCT runtime already loaded by another addon, because SONAME reuse can otherwise
silently defeat a private bundle. Deleted mapped libraries also require restart
instead of treating an in-place upgrade as a current runtime. This is fail-closed detection, not independent
linker namespaces: another addon importing OCCT later remains an integration gap.

The bundle includes `XSTEPResource`, `XSMessage`, `SHMessage` and `StdResource`.
Native `configure_exchange_resources` validates their required files. STEP
sessions temporarily select these directories and disable user-default overrides
under the existing exchange mutex; success or failure restores the previous
process environment. No host `CASROOT`/`CSF_*` paths are required. Raw development
imports of `_native` can still use the host environment unless explicitly
configured. Environment scoping serializes BlenderCAD exchange calls, not arbitrary
concurrent exchange calls made by unrelated libraries.

## Notices and source companion

CMake acquires the recorded OCCT and pybind11 source pins for packaging materials
even when matching installed binaries are selected. Eigen/Boost and PlaneGCS
already have build-local/vendored sources. Actual upstream license/copyright
files, PlaneGCS source headers and local compatibility descriptions are indexed
in `notices/notice-index.json`. Supporting runtime notices use explicit package
mappings. Unknown/missing notice mappings fail rather than substituting a guessed
license: use `BLENDERCAD_RUNTIME_LICENSE_ROOT` or per-SONAME directories under
`BLENDERCAD_NOTICES_OVERRIDE_DIR` to supply actual files on another distribution.

The source archive includes complete relinkable project/solver sources and local
shims, OCCT and pybind11 source trees, Eigen sources and the complete Boost header
tree used by PlaneGCS. `BUILDING-AND-RELINKING.md` explains offline dependency
overrides and intentional solver modifications. Normal binary users do not need
these compilers or sources. Source/archive hashes connect the rebuild materials
to the generated runtime manifest; no Git database, build cache or unrelated user
files enter the companion.

The installed distribution's OCCT binary may contain downstream patches. A cached
upstream source pin is a reproducible rebuild baseline, not proof of identical
distribution sources or byte-identical reconstruction. Use
`BLENDERCAD_USE_SYSTEM_DEPENDENCIES=OFF` to build binaries from the included pins.
No project-wide license is invented by this packaging work; the individual notices
and source/relink materials are retained without claiming a blanket release or
redistribution-compliance determination.

## Verification

With the host test tool `bwrap` available:

```sh
cmake --build build --target check_relocation -j2
```

The test extracts the ZIP to a path containing spaces, clears host OCCT/loader
environment overrides and masks host OCCT libraries **and resources** in a child
mount namespace. It verifies package-local mapped libraries, PlaneGCS solving,
filleting/tessellation, STEP and save/reopen. A missing-library negative control
tests both normal addon initialization and direct extension import bypassing the
manifest validator. The latter must fail despite host OCCT being installed.
Further controls reject omitted library records, corrupt resources and
foreign/deleted loaded OCCT runtimes; success and failure restore the actual C
process environment, not only Python's cached `os.environ` mapping.
No installed files are modified. `check` includes this group when `bwrap` exists.

The separate `check_source_rebuild` target extracts the companion and builds
the pinned sources offline through CMake, then runs native fillet checks. It is
separate because compiling OCCT is substantially heavier than ordinary checks.
Logs and evidence remain under `build/source-rebuild-tests`; normal clean removes
them. Successful relocation is not portability to another ABI or the full bracket
acceptance gate.
