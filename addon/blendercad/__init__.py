"""BlenderCAD addon entry point."""

import json

bl_info = {
    "name": "BlenderCAD",
    "author": "BlenderCAD contributors",
    "version": (0, 1, 0),
    "blender": (5, 2, 0),
    "location": "3D View > Sidebar > BlenderCAD",
    "description": "Parametric CAD foundation for Blender",
    "category": "3D View",
}

try:
    from . import _native
except ImportError:
    _native = None


def _native_status() -> str:
    if _native is None:
        return "Native module is not installed"
    return f"Native core {_native.blendercad_version()} · OCCT {_native.occt_version()}"


def _classes():
    import bpy

    class BLENDERCAD_OT_new_document(bpy.types.Operator):
        bl_idname = "blendercad.new_document"
        bl_label = "New CAD Document"
        bl_description = "Create a persistent BlenderCAD design document in this scene"
        bl_options = {"REGISTER", "UNDO"}

        @classmethod
        def poll(cls, context):
            return (_native is not None and context.scene is not None
                    and not context.scene.blendercad_document_snapshot)

        def execute(self, context):
            document = _native.Document()
            context.scene.blendercad_document_snapshot = json.dumps(
                document.to_snapshot(), separators=(",", ":"), sort_keys=True
            )
            from . import presentation
            presentation.sync(context.scene)
            self.report({"INFO"}, f"Created CAD document {document.id[:8]}")
            return {"FINISHED"}

    class BLENDERCAD_PT_status(bpy.types.Panel):
        bl_label = "BlenderCAD"
        bl_idname = "BLENDERCAD_PT_status"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "BlenderCAD"

        def draw(self, context):
            self.layout.label(text=_native_status())
            if _native is None:
                return

            serialized = context.scene.blendercad_document_snapshot
            if not serialized:
                self.layout.operator("blendercad.new_document", icon="FILE_NEW")
                return
            try:
                document = _native.Document.from_snapshot(json.loads(serialized))
            except (ValueError, KeyError, TypeError) as error:
                self.layout.label(text=f"Document snapshot error: {error}", icon="ERROR")
                return

            self.layout.label(text=f"Document {document.id[:8]} · revision {document.revision}")
            self.layout.label(text=f"Parameters: {len(document.parameters)}")
            self.layout.label(text=f"Sketches: {len(document.sketches)}")
            self.layout.label(text=f"Features: {len(document.features)}")
            self.layout.label(text=f"Model tolerance: {document.geometric_tolerance_mm:g} mm")
            self.layout.operator("blendercad.add_rectangle_solid", icon="MESH_CUBE")
            self.layout.operator("blendercad.parameter")
            self.layout.operator("blendercad.recompute", icon="FILE_REFRESH")
            from . import presentation
            state = presentation.runtime(context.scene)
            if state.error:
                self.layout.label(text=state.error, icon="ERROR")
            if state.serialized != serialized:
                self.layout.label(text="Display needs recompute", icon="ERROR")
            for parameter in document.parameters:
                self.layout.label(text=f"{parameter.name}: {parameter.value:g} {parameter.unit}")
            for feature in document.features:
                row = self.layout.row(align=True)
                row.label(text=feature.name)
                operator = row.operator("blendercad.set_feature_depth", text="Depth")
                operator.feature_id = feature.id
            host = presentation.host_for(context.active_object)
            if host is not None:
                box = self.layout.box()
                box.label(text=f"Solid: {host.get('blendercad_state', 'Needs recompute')}")
                if host.get("blendercad_diagnostic"):
                    box.label(text=host["blendercad_diagnostic"], icon="ERROR")
                display = presentation.display_for(context.scene, host)
                if display is not None:
                    box.template_ID(display, "active_material", new="material.new")
                box.operator("blendercad.duplicate_solid")
                box.operator("blendercad.frame_solid")
                box.operator("blendercad.delete_solid")
                box.operator("blendercad.convert_to_regular_mesh")

    return (BLENDERCAD_OT_new_document, BLENDERCAD_PT_status)


_registered_classes = ()


def register():
    import bpy
    from bpy.props import StringProperty
    from . import capabilities, operators, presentation

    global _registered_classes
    bpy.types.Scene.blendercad_document_snapshot = StringProperty(
        name="BlenderCAD Document Snapshot",
        description="Versioned design definition owned by BlenderCAD",
        default="",
        options={"HIDDEN"},
        update=presentation.invalidate_snapshot,
    )
    _registered_classes = _classes()
    try:
        for cls in _registered_classes:
            bpy.utils.register_class(cls)
        operators.register()
        capabilities.register()
        presentation.register()
    except Exception:
        # Each module unregisters only the hooks it installed.
        presentation.unregister()
        capabilities.unregister()
        operators.unregister()
        for cls in reversed(_registered_classes):
            if hasattr(bpy.types, cls.__name__):
                bpy.utils.unregister_class(cls)
        del bpy.types.Scene.blendercad_document_snapshot
        _registered_classes = ()
        raise


def unregister():
    import bpy
    from . import capabilities, operators, presentation

    global _registered_classes
    presentation.unregister()
    capabilities.unregister()
    operators.unregister()
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes = ()
    if hasattr(bpy.types.Scene, "blendercad_document_snapshot"):
        del bpy.types.Scene.blendercad_document_snapshot
