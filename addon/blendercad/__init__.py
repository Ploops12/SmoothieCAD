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
            self.report({"INFO"}, f"Created CAD document {document.id[:8]}")
            return {"FINISHED"}

    class BLENDERCAD_PT_status(bpy.types.Panel):
        bl_label = "BlenderCAD"
        bl_idname = "BLENDERCAD_PT_status"
        bl_space_type = "VIEW_3D"
        bl_region_type = "UI"
        bl_category = "BlenderCAD"

        def draw(self, context):
            self.layout.label(text="CAD foundation loaded")
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
            self.layout.label(text=f"Model tolerance: {document.geometric_tolerance_mm:g} mm")

    return (BLENDERCAD_OT_new_document, BLENDERCAD_PT_status)


_registered_classes = ()


def register():
    import bpy
    from bpy.props import StringProperty

    global _registered_classes
    bpy.types.Scene.blendercad_document_snapshot = StringProperty(
        name="BlenderCAD Document Snapshot",
        description="Versioned design definition owned by BlenderCAD",
        default="",
        options={"HIDDEN"},
    )
    _registered_classes = _classes()
    try:
        for cls in _registered_classes:
            bpy.utils.register_class(cls)
    except Exception:
        for cls in reversed(_registered_classes):
            if hasattr(bpy.types, cls.__name__):
                bpy.utils.unregister_class(cls)
        del bpy.types.Scene.blendercad_document_snapshot
        _registered_classes = ()
        raise


def unregister():
    import bpy

    global _registered_classes
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes = ()
    if hasattr(bpy.types.Scene, "blendercad_document_snapshot"):
        del bpy.types.Scene.blendercad_document_snapshot
