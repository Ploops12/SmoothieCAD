# Solid operation capability audit

## Policy and managed-object contract

The initial CAD representation separates a non-mesh `EMPTY` Solid host from
its managed mesh display child. A host is identified by
`blendercad_role="SOLID"`; a managed display is identified by
`blendercad_role="DISPLAY"`, has mesh type, and is parented to its host. Both
carry `blendercad_document_id` and `blendercad_body_id`. The display is a
regenerable view of evaluated CAD geometry, not an editable model.

`blendercad.capabilities.require_capability(objects, operation)` is the shared
preflight policy. It permits the CAD-aware operations `translate`, `rotate`,
`duplicate`, `delete`, `convert`, `edit_definition`, `recompute`, and `pick`.
Other operations on a Solid host or managed display raise
`ValueError("Incompatible operation - Convert to regular mesh first")`.
Blender shortcut guards report the same exact message and cancel before
dispatching an unsupported operation. The explicit conversion command is the
path out of CAD ownership; this policy does not silently convert or preserve a
Solid after an incompatible edit.

The managed display should be unselectable (`hide_select=True`) when
constructed. The host is an Empty and therefore has no built-in mesh Edit Mode
or mesh modifier stack. BlenderCAD does not add a separate editable mesh UX
for its display child.

## Guarded shortcuts

The addon inserts guards ahead of the default keymap entries for Object Mode
Tab (Edit Mode), Ctrl-Tab mode/pie switching, S (scale), Ctrl-A (Apply menu),
Shift-D and Alt-D (duplicate variants), and X/Delete (delete variants).
Ordinary Blender-only selections fall through to the corresponding native
keymap entry. All-CAD selections route duplicate/delete to
`blendercad.duplicate_solid` and `blendercad.delete_solid`; mixed selections
are cancelled instead of dropping the ordinary objects from the operation.
The guard precedes a direct CAD-command entry whose poll accepts only Solid
selections. CAD commands run at top level, not nested inside a guard operator:
Blender suppresses a nested operator's automatic undo boundary. Native modal
operations are likewise never invoked from inside the guard.
The implementation checks selection metadata before dispatch and does not use
post-mutation correction as enforcement. Guards are inserted into the active
key configuration at addon registration and removed on unregister; the addon
does not save changes to Blender user preferences.

Translation and rotation remain supported CAD-aware object operations; the
initial guard set does not replace Blender's translate/rotate controls.
The optional CMake `check_solid_ui` target verifies standard-workspace click,
Shift-D, scale/Tab guard dispatch, confirmation cancellation and conversion undo
through Blender's event queue. It does not cover customized key configurations.

## Coverage limits

This is a focused guard, not a universal Blender capability sandbox. It does
not guarantee interception of actions initiated through toolbar buttons,
gizmos, Properties editors, scripts, Python console, or operator search.
Additional Blender operators or customized keymaps may also expose mutation
paths that this initial audit does not cover. BlenderCAD must add guards at
supported entry points as those workflows are introduced and must not claim
protection for unguarded paths. An ordinary addon cannot prevent arbitrary
scripts or every built-in Blender operator from changing scene data.

The current guard targets the Object Mode shortcuts listed above. It does not
police every transform variant (for example, origin manipulation, clear/apply
operations exposed through other menus, or custom keymaps), object linking,
parenting, collection operations, or third-party operators. Such actions must
be assessed before they are represented as supported CAD operations. CAD
definitions and evaluated geometry remain owned by the BlenderCAD document and
geometry services; a display mesh change is never a substitute for updating
that definition.
