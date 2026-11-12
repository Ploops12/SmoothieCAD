# BlenderCAD Repository Guidelines

## Purpose and Current State

Build a modular, parametric CAD system inside Blender, with a foundation that can later support CAM, FEA, CFD, and native Blender integration. All design interactions—sketching, constraints, dimensions, extrusion, selection, and feature editing—must happen inside Blender.

Work in `/home/ploops/Projects/BlenderCAD`. This directory is the project’s Git repository; do not create another repository for the engine or addon. At bootstrap it has no source files, build system, or commits. Do not describe planned directories, commands, or features as already implemented.

The initial delivery is an ordinary Blender addon. Favor Blender-native interaction while keeping addon-specific storage and presentation replaceable. Do not implement a Blender fork or expand into the later engineering modules without an explicitly scoped task.

## Selected Technology and Representation

- Use **OCCT** for geometry, topology, solid operations, validation, tessellation, and CAD exchange. Initially target the installed OCCT 7.9.3.
- Use **PlaneGCS** for planar sketch constraints and diagnostics.
- Use **OndselSolver** for assembly constraints, joints, constrained movement, and kinematics.
- Integrate these as embedded libraries, not through external applications, services, or modelling executables. Pin dependency revisions reproducibly.
- Use **C++20** for computational components and **pybind11** for CPython bindings. Use Python and Blender APIs where appropriate for integration.
- Initially target this Linux system, Blender 5.2.3, and CPython 3.14. Keep the build structure portable and package the required runtime libraries without runtime downloads or user-side compilation.

Maintain three distinct layers:

1. **Design definition:** parameters, expressions, sketches, constraints, features, and dependencies.
2. **Evaluated geometry:** authoritative analytic/spline B-rep geometry and topology.
3. **Presentation:** regenerable viewport, picking, and rendering geometry.

Retain analytic geometry where useful; use splines when appropriate. Do not force everything into NURBS or meshes. Measurements and CAD exports query evaluated CAD geometry, not display triangles.

Planar sketches can occupy arbitrary planes in 3D. Free-space spatial sketch constraints are a separate capability; do not claim that assembly solving supplies them.

Do not add supplemental dependencies without a demonstrated need. Possible candidates are CGAL for specific exact-computation or validation requirements, Manifold for mesh-solid interoperability, and libfive for later implicit or field-driven geometry. They must offer a concrete advantage over existing capabilities and use defined adapters.

## Module Organization and Ownership

Establish a CMake-based project with a clear separation between native source, Python bindings, the Blender addon, focused tests, and documentation. A suitable initial layout is `src/`, `bindings/`, `addon/blendercad/`, `tests/`, and `docs/`. Document the layout when it exists; avoid speculative directories with no implementation purpose.

Keep these responsibilities distinct:

| Module | Responsibility |
| --- | --- |
| Document and parameters | Persistent identities, units, expressions, dependencies, and schema versions |
| Feature system | Feature definitions, evaluation, suppression, rollback, and scheduling |
| Geometry adapter | OCCT integration, shape lifetimes, operations, queries, validation, and tessellation |
| Sketch constraint adapter | PlaneGCS translation, solving, and diagnostic translation |
| Assembly adapter | OndselSolver translation, joints, placements, and motion |
| Reference system | Persistent references and topology-change provenance |
| Command system | Transactions, capability checks, previews, confirmation, and cancellation |
| Blender integration | Objects, operators, tools, panels, gizmos, scene organization, and undo synchronization |
| Presentation | Display geometry, picking acceleration structures, highlighting, materials, and rendering |
| Persistence and exchange | Serialization, `.blend` embedding, migrations, and CAD import/export |

Blender API dependencies are permitted. Place them deliberately so Blender object lifecycles, UI registration, and storage details do not become inseparable from geometry algorithms or constraint solving.

- The document owns design intent. Blender objects reference document/body identities instead of maintaining competing definitions.
- Geometry adapters own kernel objects. Cross-module interfaces use typed identifiers and controlled queries, not unmanaged kernel pointers.
- The document owns constraint definitions. Solver-specific entities are adapter details and must not define the saved document format.
- Numeric fields, gizmos, menus, and shortcuts issue the same commands; do not duplicate modelling logic in UI code.
- Evaluation produces geometry, diagnostics, and reference-history information. Presentation cannot independently alter the CAD definition.
- Serialize stable identities and definitions, never process pointers, object names as identities, or transient face indices.
- Transfer display arrays in bulk across the C++/Python boundary rather than calling Python for every triangle.

## Parametric Modelling and Feature History

Parameters must be named and unit-aware. Support arithmetic expressions, dimensional validation, and dependency-cycle detection. Distinguish directed parameter expressions from simultaneous geometric constraint solving.

Sketches contain persistent geometry and constraints. Initial entities are lines and circles, with rectangle construction tools. Initial constraints include coincidence, horizontal/vertical, parallel/perpendicular, distance, length, and radius/diameter. Translate solver output into useful underconstrained, fully constrained, redundant, conflicting, and failed-solve states.

Store each feature’s persistent identity, type/version, parameters, referenced inputs, dependencies, output bodies, suppression state, and diagnostics. Implement a dependency graph with an ordered user-facing history, for example:

`Parameters → Profile sketch → Extrusion → Hole cuts → Pocket → Fillets`

- Recompute affected downstream features after edits; reuse unchanged results.
- Permit reordering only when dependencies remain valid.
- Preserve downstream intent when editing earlier features wherever references remain resolvable.
- Suppression retains definitions and identifies dependents with unavailable inputs.
- Handle deletion dependencies explicitly. Rollback exposes earlier states without destroying later definitions.
- Keep feature history separate from undo history: construction intent is not an undo log.
- Use transactional previews. Confirmation commits one undoable interaction; cancellation restores the preceding state.
- Route dragging and numeric editing through the same parameter/constraint machinery.
- Do not imply arbitrary face push/pull support. Later direct-edit operations must become explicit, reproducible features.

Use feature provenance and OCCT topology-change relationships for persistent references. Handle split, merged, deleted, and ambiguous references explicitly; never silently substitute another face or edge. Identify affected features and provide a reference-repair path.

On evaluation failure, retain the last valid display with a visible failure state and mark dependent features blocked. Do not export stale geometry as the requested current result. Publish asynchronous results only when their source revision is still current.

## Solid Display, Selection, and Operation Protection

Represent a selectable Solid with a non-mesh host and addon-managed display geometry. The display mesh is not a separately editable modelling object. Keep scene organization, visibility, materials, and supported placement operations integrated with Blender.

Start with managed mesh presentation for existing viewport shading and Cycles integration. Keep the presentation backend replaceable by direct GPU drawing or native Solid display.

Associate every generated triangle with its originating CAD face and evaluated revision. Maintain equivalent mappings for displayed CAD edges and vertices. Resolve picks through these mappings and persistent references; discard stale picking data after recomputation. Use precise geometric queries for measurement, snapping, and curved-face hit refinement where necessary.

Centralize capability checks. Support CAD-aware selection, duplication/deletion, translation, and rotation. Change dimensions through parameters and supported CAD features.

Ordinary mesh Edit Mode, mesh modifiers, sculpting, and unsupported deformation must not become Solid operations. Do not accept display-only modifications, automatically convert the part, or substitute after-the-fact invalidation for blocking. Commands controlled by the addon must cancel before modification and display exactly:

> Incompatible operation - Convert to regular mesh first

Provide an explicit **Convert to Regular Mesh** command that ends CAD behaviour for that object in one undoable transaction.

An ordinary addon cannot universally veto arbitrary scripts or every built-in Blender operator. Audit supported keyboard, toolbar, menu, and operator-search entry points. Document unguardable paths as native-integration gaps; do not claim enforcement that the implementation cannot provide.

Native computation jobs must not access Blender data off-thread. Apply scene and display updates on Blender’s main thread.

## Persistence and Future Native Integration

Embed a versioned document snapshot in Blender scene properties, including imported geometry payloads when necessary. Save display geometry normally. Reconstruct native state after loading and undo/redo; caches are disposable. Use content revisions to reject stale cached or asynchronous results. Never silently rewrite unsupported future document versions.

Design interfaces so a native implementation replaces adapters rather than duplicates CAD functionality:

| Addon implementation | Future native replacement |
| --- | --- |
| Solid host and document association | Native Solid object/data-block |
| Managed mesh | Native evaluated geometry/display cache |
| Addon picking/highlighting | Native Solid selection |
| Embedded snapshot | Native serialization integration |
| Addon command checks | Native operator/capability checks |

Preserve the document, feature semantics, solver adapters, reference system, and geometry services through that transition. Do not make core functionality depend on an undocumented addon object hierarchy.

For a larger design ecosystem, separate part definitions from positioned instances. Keep associative drawings, CAM operations/toolpaths, analysis meshes/results, and simulation boundary conditions out of CAD feature definitions. These modules attach to explicit geometry regions and source revisions. Model changes mark dependent artifacts stale rather than silently treating them as current.

## Delivery Priorities

Work toward these coherent milestones:

1. Repository/bootstrap: project structure, CMake, dependency strategy, packaging skeleton, and development instructions.
2. Core foundation: library integration, document identities, geometry evaluation, constraint interfaces, serialization, and CPython bindings.
3. Solid integration: selectable host, display, picking, materials, compatible commands, persistence, and undo synchronization.
4. Parametric modelling: constrained sketches, expressions, extrusion, subtractive extrusion, constant-radius fillets, history, and handles.
5. End-to-end acceptance: create and edit a mechanical bracket entirely inside Blender; save/reopen, render, and exchange STEP.
6. Later independent capabilities: assemblies, broader CAD tools, and engineering modules.

Initial drawings mean modelling sketches, not technical drawing sheets. Assemblies, free-space spatial sketching, broad surfacing, CAM, FEA, and CFD are not prerequisites for the first single-part milestone.

## Git Workflow and Detailed Commit Messages

Use the existing `master` integration branch unless directed otherwise. Establish a small, coherent bootstrap commit. Create topic branches for major, independently reviewable capabilities, such as `feature/solid-integration` or `feature/parametric-modelling`.

Group minor related work into logical commits on the relevant branch. Do not create branches per file or function, empty scaffolding branches, excessive checkpoint commits, or knowingly broken commits. Keep small integration fixes with the feature they support. Integrate completed feature branches after their relevant checks pass.

Use an informative imperative subject followed by a **detailed commit body**. Explain:

- What functionality or behaviour was added or changed.
- Why the approach was chosen and which alternatives or tradeoffs matter.
- Public interfaces, dependency, persistence, or compatibility changes.
- Verification performed and its outcome; identify untested paths where relevant.
- Known limitations, concerns, integration gaps, and necessary follow-up work.

For trivial changes, keep the body proportionate, but do not reduce substantial feature commits to an unexplained one-line message. Reviewers should understand intent and concerns without reconstructing the development conversation. Do not invent conventions from Git history that does not yet exist.

Review `git status`, changes since the last working commit, and the staged diff before committing. Include untracked files in the review. Use `git diff HEAD` once a first commit exists; before that, inspect staged files directly. Stage intentional changes only. Preserve unrelated user work and do not rewrite history or perform destructive Git operations without authorization.

Do not publish branches or open pull requests unless requested. When a review is requested, describe behaviour, architectural decisions, verification, and remaining concerns; include UI screenshots when useful.

## Feature Documentation and Concerns

Keep durable documentation alongside the implementation, normally under `docs/`, with entry points linked from the project README once created. Document major features in their feature commits rather than relying solely on commit bodies or chat history.

Explain user-visible behaviour, design intent, module ownership, interfaces, data flow, and how to build or exercise the feature. For architectural decisions, record motivation, alternatives considered, tradeoffs, and consequences. Document numerical assumptions, supported entity/constraint types, serialization changes, failure handling, and integration limitations when applicable.

Keep concerns actionable: state the affected behaviour, conditions that trigger the limitation, impact, and possible resolution. Clearly distinguish implemented functionality, planned work, and unverified assumptions. Prefer explanations of non-obvious reasoning over comments that restate code.

Do not invent working commands before build targets exist. Once established, document the actual CMake configure/build commands, focused test commands, and Blender installation/debug workflow, and update them when they change.

## Proportionate Testing at Commit Boundaries

Test before each completed commit, **not after every individual edit**. Group related edits into a reviewable unit, inspect the diff against the last working commit, and run the checks appropriate to that unit. Recheck after fixes that invalidate the earlier result.

- Native build/bindings: compile affected targets and check import in the supported Blender.
- Geometry/features: check relevant dimensions, radii, analytical volume, validity, and failure cases.
- Constraints: check relevant solving, diagnostic, dragging, and cancellation scenarios.
- Presentation/selection: perform focused Blender checks for display, picking, and material mapping.
- Persistence/history: check relevant save/reopen, undo/redo, recomputation, duplication identities, and reference preservation.
- Exchange: check relevant STEP round trips.
- Documentation/configuration: review and perform applicable lightweight validation; do not invent runtime tests for prose.

Do not require every small change to introduce tests, impose arbitrary coverage targets, or rerun unrelated suites. Use a small representative model set and add regression cases for meaningful bugs or fragile boundaries. Before integrating a major branch, verify its end-to-end workflow.

Support watch-internal through wardrobe-sized geometry. Default to a configurable per-document geometric error budget of **0.001 mm**. Separate modelling tolerance, exchange validation, and display tessellation; never silently relax the budget. Use double precision and part-local coordinates. Before the first usable release, verify small, ordinary mechanical, and furniture-scale bracket workflows.

Measure evaluation, tessellation, picking, and Blender-transfer costs separately when investigating performance. Do not add optimization projects or dependencies without a measured bottleneck or missing capability.

## Sub-Agent Delegation and Context Management

Dispatch isolated, substantial features or investigations to sub-agents when they can be completed against clear interfaces with limited overlapping ownership. Use delegation to preserve the main agent’s context and token budget, not to create unnecessary coordination work.

- The coordinating agent owns the overall architecture, dependency ordering, integration, and acceptance criteria.
- Give each agent a bounded objective, relevant context, owned files/modules, interface contracts, constraints, and expected verification. Do not make every agent independently rediscover the whole project.
- Keep unrelated work parallel; keep tightly coupled or sequential changes together. Do not delegate trivial edits or split a feature so finely that coordination exceeds the implementation effort.
- Prefer separate worktrees when agents need true filesystem isolation; otherwise assign non-overlapping ownership and coordinate shared-file changes explicitly.
- Agents sharing a working tree must not switch its branch, stage or commit another agent’s files, or rewrite shared history. Delegate Git mutations explicitly; do not let agents create competing commits or branches by default.
- Request concise handoffs containing implemented behaviour, interfaces changed, files affected, checks/results, unresolved concerns, and follow-up needs. Store lengthy rationale in repository documentation, not repeated message dumps.
- Review each handoff and diff, reconcile interface changes, and run proportionate integration checks before the coordinating commit.
- Reuse existing agent context for follow-up work where useful. Do not spawn duplicate investigations or unnecessary nested agent trees.

## Communication

Use direct, technical language. State facts, assumptions, findings, and limitations explicitly. Do not use praise, platitudes, feigned emotion, or personification.
