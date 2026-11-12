"""Solid operation policy and focused Blender shortcut guards.

The document remains the authority for CAD edits. This module only prevents
selected Blender object operations from mutating a CAD host or its display.
"""

INCOMPATIBLE_MESSAGE = "Incompatible operation - Convert to regular mesh first"

_SUPPORTED = frozenset({
    "translate", "rotate", "duplicate", "delete", "convert",
    "edit_definition", "recompute", "pick",
})
_KEYMAP_ITEMS = []
_REGISTERED_CLASSES = ()


def _prop(obj, key, default=None):
    try:
        return obj.get(key, default)
    except (AttributeError, ReferenceError, TypeError):
        return default


def _is_solid_host(obj):
    try:
        import bpy
        return (obj is not None and obj.type == "EMPTY"
                and _prop(obj, "blendercad_role") == "SOLID")
    except (ImportError, AttributeError, ReferenceError):
        return False


def _is_display(obj):
    try:
        return (obj is not None and obj.type == "MESH"
                and _prop(obj, "blendercad_role") == "DISPLAY")
    except (AttributeError, ReferenceError):
        return False


def host_for(obj):
    """Return the owning Solid host for a host or managed display object."""
    if _is_solid_host(obj):
        return obj
    if _is_display(obj):
        try:
            parent = obj.parent
            if not _is_solid_host(parent):
                return None
            for key in ("blendercad_document_id", "blendercad_body_id"):
                child_value = _prop(obj, key)
                host_value = _prop(parent, key)
                if child_value is not None and host_value is not None and child_value != host_value:
                    return None
            return parent
        except (AttributeError, ReferenceError):
            return None
    return None


def _is_cad_object(obj):
    return _prop(obj, "blendercad_role") in {"SOLID", "DISPLAY"}


def selected_hosts(context):
    """Return selected CAD hosts once each, ignoring ordinary objects."""
    found = []
    seen = set()
    for obj in getattr(context, "selected_objects", ()) or ():
        host = host_for(obj)
        if host is None:
            continue
        try:
            key = host.as_pointer()
        except (AttributeError, ReferenceError):
            key = id(host)
        if key not in seen:
            seen.add(key)
            found.append(host)
    return found


def require_capability(objects, operation):
    """Raise the standard incompatibility error before an unsupported edit."""
    if operation in _SUPPORTED:
        return
    if any(_is_cad_object(obj) for obj in objects or ()):
        raise ValueError(INCOMPATIBLE_MESSAGE)


def _selection_state(context):
    selected = list(getattr(context, "selected_objects", ()) or ())
    cad = [obj for obj in selected if _is_cad_object(obj)]
    if not cad:
        return "none", selected
    if len(cad) != len(selected):
        return "mixed", selected
    if any(host_for(obj) is None for obj in cad):
        return "invalid", selected
    return "all_cad", selected


def _guard_operator_class():
    import bpy

    class BLENDERCAD_OT_capability_guard(bpy.types.Operator):
        bl_idname = "blendercad.capability_guard"
        bl_label = "Guard BlenderCAD Solid Operation"
        bl_options = {"INTERNAL"}

        action: bpy.props.StringProperty(options={"HIDDEN"})

        def invoke(self, context, event):
            return self.execute(context)

        def execute(self, context):
            state, selected = _selection_state(context)
            if state == "none":
                # Fall through to the native keymap entry. Nested invocation
                # would change modal behavior and Blender's undo boundaries.
                return {"PASS_THROUGH"}
            if state in {"mixed", "invalid"}:
                self.report({"ERROR"}, INCOMPATIBLE_MESSAGE)
                return {"CANCELLED"}

            if self.action in {"duplicate", "duplicate_linked"} or self.action.startswith("delete_"):
                # The next entry invokes the CAD operator at top level. It must
                # not run nested inside this guard: nested UNDO is suppressed.
                return {"PASS_THROUGH"}

            operation = {
                "edit_mode": "edit_mode",
                "ctrl_tab": "edit_mode",
                "scale": "scale",
                "apply_menu": "apply",
            }.get(self.action, self.action)
            try:
                require_capability(selected, operation)
            except ValueError as error:
                self.report({"ERROR"}, str(error))
                return {"CANCELLED"}
            return {"CANCELLED"}

    return BLENDERCAD_OT_capability_guard


def _install_guard(km, key_type, action, *, shift=False, ctrl=False, alt=False):
    cad_operator = None
    if action in {"duplicate", "duplicate_linked"}:
        cad_operator = "blendercad.duplicate_solid"
    elif action.startswith("delete_"):
        cad_operator = "blendercad.delete_solid"
    if cad_operator:
        command = km.keymap_items.new(cad_operator, key_type, "PRESS", shift=shift, ctrl=ctrl, alt=alt, head=True)
        _KEYMAP_ITEMS.append((km, command))
    item = km.keymap_items.new(
        "blendercad.capability_guard", key_type, "PRESS", shift=shift,
        ctrl=ctrl, alt=alt, head=True,
    )
    item.properties.action = action
    _KEYMAP_ITEMS.append((km, item))


def register():
    """Register the focused guards in Blender's active key configuration."""
    import bpy

    global _REGISTERED_CLASSES
    if _REGISTERED_CLASSES:
        return
    cls = _guard_operator_class()
    bpy.utils.register_class(cls)
    _REGISTERED_CLASSES = (cls,)

    keyconfig = getattr(bpy.context.window_manager, "keyconfigs", None)
    keyconfig = getattr(keyconfig, "active", None)
    if keyconfig is None:
        return
    maps = {km.name: km for km in keyconfig.keymaps}
    for name in ("Object Mode", "Object Non-modal"):
        km = maps.get(name)
        if km is None:
            continue
        _install_guard(km, "TAB", "edit_mode")
        _install_guard(km, "S", "scale")
        _install_guard(km, "A", "apply_menu", ctrl=True)
        _install_guard(km, "D", "duplicate", shift=True)
        _install_guard(km, "D", "duplicate_linked", alt=True)
        _install_guard(km, "X", "delete_x")
        _install_guard(km, "X", "delete_x_global", shift=True)
        _install_guard(km, "DEL", "delete_del")
        _install_guard(km, "DEL", "delete_del_global", shift=True)
    for name in ("3D View", "Object Non-modal"):
        km = maps.get(name)
        if km is not None:
            _install_guard(km, "TAB", "ctrl_tab", ctrl=True)


def unregister():
    """Remove keymap entries and capability operator class."""
    import bpy

    global _REGISTERED_CLASSES
    for km, item in reversed(_KEYMAP_ITEMS):
        try:
            km.keymap_items.remove(item)
        except (ReferenceError, RuntimeError):
            pass
    _KEYMAP_ITEMS.clear()
    for cls in reversed(_REGISTERED_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _REGISTERED_CLASSES = ()
