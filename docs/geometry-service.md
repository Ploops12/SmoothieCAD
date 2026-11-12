# Native geometry service

`GeometryAdapter` owns OCCT shapes; its public boundary exposes adapter-scoped `ShapeHandle` values, never kernel pointers. Handles are transient, cannot be used across adapters, and become invalid after `release`. They are not document identities or persistent topology references.

## Implemented operations

- Box construction with positive finite dimensions in millimetres.
- Closed polygon and circle extrusion on an arbitrary orthonormal sketch plane. Profile coordinates and positive extrusion depth are millimetres; the direction is the plane's right-handed normal. Polygons are closed automatically and must define a valid planar face.
- Boolean subtraction of two evaluated solids.
- Kernel validity, analytic volume in mm³, and maximum vertex/edge/face tolerance queries.
- Tessellation with independently specified absolute linear deflection in mm and angular deflection in radians.
- STEP solid import and export using millimetres as the internal length unit.

The adapter rejects null, invalid, and non-solid results before allocating a handle. Each adapter accepts a configurable geometric tolerance budget, defaulting to **0.001 mm**. Results with OCCT topology tolerances exceeding that budget are rejected; the budget is never silently increased. This check is not a general proof of geometric accuracy, nor a substitute for exchange-deviation validation. Display deflection is separate and does not change modeling tolerance.

These are low-level kernel operations, not saved CAD features. A future evaluator must associate outputs with feature identities, document revisions and topology-change provenance. In particular, local face/edge indices must not enter saved references. There is no stale-result/export gate at the document level yet because feature evaluation has not been implemented.

## Bulk presentation data

`tessellate` returns a mapping of contiguous one-dimensional NumPy arrays. No per-triangle calls cross into Python:

| Key | Type | Meaning |
| --- | --- | --- |
| `positions` | float64 | XYZ triples in mm; nodes are split at CAD face boundaries |
| `triangles` | uint32 | Vertex index triples, outward-oriented for the evaluated faces |
| `triangle_faces` | uint32 | One zero-based evaluated face index per triangle |
| `face_count` | integer | Number of evaluated CAD faces |
| `edge_positions` | float64 | Sampled CAD-edge XYZ triples in mm |
| `edge_offsets` | uint64 | Start/end offsets in points for each evaluated CAD edge; length is edge count + 1 |
| `cad_vertices` | float64 | One XYZ triple per evaluated CAD vertex |

Face, edge and vertex indices refer only to that evaluated shape. The presentation publisher must attach its source revision and discard obsolete picks. Edge ranges preserve zero-length entries for degenerate edges. A topology/geometry copy is meshed so tessellation caches cannot mutate the authoritative shape. Native tessellation releases the Python GIL and never touches Blender data.

STEP operations use fresh sessions and explicit writer units instead of changing global export-unit settings. Exchange registry/session work is serialized within this adapter. OCCT failures are translated to Python runtime errors; invalid caller inputs and invalid/released/foreign handles raise value errors.

## Verification commands

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target package_addon -j2
ctest --test-dir build --output-on-failure
ALSOFT_DRIVERS=null blender --background --factory-startup --python-exit-code 1 --python tests/native_geometry.py -- --module-dir build/addon/blendercad
ALSOFT_DRIVERS=null blender --background --factory-startup --python-exit-code 1 --python tests/blender_foundation.py -- --addon-zip build/dist/blendercad-0.1.0.zip
```

Host CTest requires NumPy in the configured Python; the target Blender includes it. `ALSOFT_DRIVERS=null` avoids an audio-shutdown stall observed in the sandbox, unrelated to the CAD checks.

The checks cover analytical volumes at 0.01–1000 mm scales, rotated/translated planes, mesh orientation and topology mappings, curved tessellation refinement, subtraction, STEP round trips, configured tolerance rejection, handle isolation/lifetimes, document snapshot round trips and malformed UUID rejection. The packaged-addon Blender check covers native import, registration, scene save/reopen, registration cycling, and protection against overwriting existing/future-schema documents. These checks do not establish interactive selection, undo synchronization, constrained sketching, feature history or bracket acceptance.
