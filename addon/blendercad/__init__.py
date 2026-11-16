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
    from . import runtime as _runtime
    _runtime_root = _runtime.prepare()
    from . import _native
    _runtime.configure(_native, _runtime_root)
    _native_error = ""
except (ImportError, OSError, RuntimeError, ValueError) as _error:
    _native = None
    _native_error = str(_error)


def _native_status() -> str:
    if _native is None:
        return f"Native module unavailable: {_native_error}"
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
            from . import feature_editor, sketch_editor
            return (_native is not None and context.scene is not None
                    and feature_editor.active is None and sketch_editor.active is None
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
            from . import sketch_editor, feature_editor
            self.layout.label(text=_native_status())
            if _native is None:
                return
            if sketch_editor.active is not None:
                self.layout.label(text="Editing sketch · Enter confirm · Esc cancel")
                self.layout.enabled = False
            elif feature_editor.active is not None:
                self.layout.label(text="Previewing feature · Enter confirm · Esc cancel")
                self.layout.enabled = False

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
            self.layout.prop(context.scene, "blendercad_selection_mode", expand=True)
            from . import presentation
            state = presentation.runtime(context.scene)
            if state.error:
                self.layout.label(text=state.error, icon="ERROR")
            if state.lifecycle_error:
                self.layout.label(text=state.lifecycle_error, icon="ERROR")
            if state.serialized != serialized or state.scale != presentation.unit_scale(context.scene):
                self.layout.label(text="Display needs recompute", icon="ERROR")
            for parameter in document.parameters:
                row = self.layout.row(align=True)
                row.label(text=f"{parameter.name}: {parameter.value:g} {parameter.unit}")
                edit = row.operator("blendercad.parameter", text="Edit")
                edit.name = parameter.name
                if parameter.expression is not None:
                    self.layout.label(text=f"= {parameter.expression}")
            history = self.layout.box()
            row = history.row(align=True)
            row.label(text="Feature History")
            if document.rollback_before is not None:
                row.operator("blendercad.rollback", text="Resume Full History")
            reports = {item.feature_id: item for item in state.report.features} if state.report and state.serialized == serialized else {}
            for index, feature in enumerate(document.features):
                item = history.box()
                report = reports.get(feature.id)
                status = report.state.name.replace("_", " ").title() if report else "Needs recompute"
                item.label(text=f"{index + 1}. {feature.name} · {status}")
                row = item.row(align=True)
                if feature.type == "fillet":
                    operator = row.operator("blendercad.set_fillet_radius", text="Radius")
                    operator.feature_id = feature.id
                    if document.rollback_before == feature.id:
                        operator = row.operator("blendercad.repair_fillet_edges", text="Repair and Resume")
                        operator.feature_id = feature.id
                else:
                    operator = row.operator("blendercad.set_feature_depth", text="Depth")
                    operator.feature_id = feature.id
                operator = row.operator("blendercad.preview_feature", text="Handle")
                operator.feature_id = feature.id
                operator.operation = "RADIUS" if feature.type == "fillet" else "DEPTH"
                operator = row.operator("blendercad.suppress_feature", text="Unsuppress" if feature.suppressed else "Suppress")
                operator.feature_id, operator.suppressed = feature.id, not feature.suppressed
                operator = row.operator("blendercad.rollback", text="Edit Inputs" if feature.type == "fillet" else "Before")
                operator.feature_id = feature.id
                row = item.row(align=True)
                for offset, text in ((-1, "Earlier"), (1, "Later")):
                    cell = row.row(align=True)
                    cell.enabled = 0 <= index + offset < len(document.features)
                    operator = cell.operator("blendercad.move_feature", text=text)
                    operator.feature_id, operator.offset = feature.id, offset
                operator = row.operator("blendercad.delete_feature", text="Delete")
                operator.feature_id = feature.id
                if report and report.diagnostic:
                    item.label(text=report.diagnostic, icon="ERROR")
            sketch_box = self.layout.box()
            sketch_box.label(text="Sketches")
            row = sketch_box.row(align=True)
            for plane in ("XY", "XZ", "YZ", "CURSOR"):
                operator = row.operator("blendercad.edit_sketch", text="Cursor" if plane == "CURSOR" else plane)
                operator.plane = plane
            for sketch in document.sketches:
                row = sketch_box.row(align=True)
                row.label(text=sketch.name)
                operator = row.operator("blendercad.edit_sketch", text="Edit")
                operator.sketch_id = sketch.id
                operator = row.operator("blendercad.extrude_sketch", text="Extrude")
                operator.sketch_id = sketch.id
                for mode, label in (("EXTRUDE", "Preview"), ("CUT", "Preview Cut")):
                    operator = row.operator("blendercad.preview_feature", text=label)
                    operator.sketch_id, operator.operation = sketch.id, mode
            resolutions = {entry.reference_id: entry.resolution for entry in state.report.references} if state.report and state.serialized == serialized else {}
            for reference in document.references:
                box = self.layout.box()
                resolution = resolutions.get(reference.id)
                label = resolution.state.name.replace("_", " ").title() if resolution else "Needs recompute"
                box.label(text=f"{reference.name}: {label}")
                if resolution and resolution.state != _native.ReferenceState.RESOLVED:
                    box.label(text=resolution.diagnostic, icon="ERROR")
                operator = box.operator("blendercad.save_reference", text="Repair From Selection")
                operator.reference_id = reference.id
                operator = box.operator("blendercad.erase_reference", text="Remove Reference")
                operator.reference_id = reference.id
            host = presentation.host_for(context.active_object)
            if host is not None:
                box = self.layout.box()
                box.label(text=f"Solid: {host.get('blendercad_state', 'Needs recompute')}")
                if host.get("blendercad_diagnostic"):
                    box.label(text=host["blendercad_diagnostic"], icon="ERROR")
                if host.get(presentation.DOC) == document.id:
                    dimensions = {_native.SketchConstraintKind.DISTANCE, _native.SketchConstraintKind.LENGTH,
                                  _native.SketchConstraintKind.RADIUS, _native.SketchConstraintKind.DIAMETER,
                                  _native.SketchConstraintKind.DISTANCE_X, _native.SketchConstraintKind.DISTANCE_Y}
                    seen = set()
                    reports = {item.feature_id: item for item in state.report.features} if state.report and state.serialized == serialized else {}
                    for feature in document.features:
                        if feature.sketch_id is None or feature.body_id != host.get(presentation.BODY) or feature.sketch_id in seen:
                            continue
                        seen.add(feature.sketch_id)
                        sketch = next(item for item in document.sketches if item.id == feature.sketch_id)
                        profile = box.box()
                        profile.label(text=f"Sketch: {sketch.name}")
                        entity_names = {entity.id: f"{'Line' if isinstance(entity.geometry, _native.Line2) else 'Circle'} {index + 1}"
                                        for index, entity in enumerate(sketch.problem.entities)}
                        report = reports.get(feature.id)
                        if report is not None and report.sketch_report is not None:
                            solved = report.sketch_report
                            profile.label(text=f"{solved.state.name.replace('_', ' ').title()} · {solved.remaining_degrees_of_freedom} DOF")
                        for constraint in sketch.problem.constraints:
                            if constraint.kind not in dimensions:
                                continue
                            label = constraint.kind.name.replace("_", " ").title()
                            target = constraint.targets[0]
                            if target.entity:
                                label += f" ({entity_names[target.entity]})"
                            if constraint.parameter is not None:
                                value = document.length_value(constraint.parameter)
                                text = f"{label}: {constraint.parameter} = {value:g} mm"
                            else:
                                text = f"{label}: {constraint.value:g} mm"
                            row = profile.row(align=True)
                            row.label(text=text)
                            edit = row.operator("blendercad.set_sketch_dimension", text="Edit")
                            edit.sketch_id, edit.constraint_id = sketch.id, constraint.id
                display = presentation.display_for(context.scene, host)
                if display is not None:
                    box.template_ID(display, "active_material", new="material.new")
                measure = presentation.current_measure(context.scene, host)
                if measure is not None:
                    box.label(text=f"CAD {measure.element.kind.name.lower()} selection")
                    if measure.length_mm is not None:
                        box.label(text=f"Length: {measure.length_mm:.8g} mm")
                    if measure.area_mm2 is not None:
                        box.label(text=f"Area: {measure.area_mm2:.8g} mm²")
                    if measure.radius_mm is not None:
                        box.label(text=f"Radius: {measure.radius_mm:.8g} mm")
                    if measure.element.kind == _native.TopologyKind.VERTEX:
                        value = measure.center_mm
                        box.label(text=f"Local point: {value.x:.6g}, {value.y:.6g}, {value.z:.6g} mm")
                    if state.reference_error:
                        box.label(text=state.reference_error, icon="ERROR")
                from . import highlight
                if highlight.last_error:
                    box.label(text="Selection overlay unavailable", icon="ERROR")
                box.operator("blendercad.duplicate_solid")
                box.operator("blendercad.fillet")
                operator = box.operator("blendercad.preview_feature", text="Preview Selected Edge Fillet")
                operator.operation = "FILLET"
                box.operator("blendercad.save_reference")
                box.operator("blendercad.frame_solid")
                box.operator("blendercad.export_step")
                box.operator("blendercad.delete_solid")
                box.operator("blendercad.convert_to_regular_mesh")

    return (BLENDERCAD_OT_new_document, BLENDERCAD_PT_status)


_registered_classes = ()


def register():
    import bpy
    from bpy.props import EnumProperty, StringProperty
    from . import capabilities, highlight, lifecycle, operators, presentation, sketch_editor, feature_editor

    global _registered_classes
    bpy.types.Scene.blendercad_document_snapshot = StringProperty(
        name="BlenderCAD Document Snapshot",
        description="Versioned design definition owned by BlenderCAD",
        default="",
        options={"HIDDEN"},
        update=presentation.invalidate_snapshot,
    )
    bpy.types.Scene.blendercad_selection_mode = EnumProperty(
        name="CAD Selection", items=(("FACE", "Face", "Select CAD faces"),
                                     ("EDGE", "Edge", "Select CAD edges"),
                                     ("VERTEX", "Vertex", "Select CAD vertices")), default="FACE",
    )
    _registered_classes = _classes()
    try:
        for cls in _registered_classes:
            bpy.utils.register_class(cls)
        operators.register()
        sketch_editor.register()
        feature_editor.register()
        capabilities.register()
        presentation.register()
        lifecycle.register()
        highlight.register()
    except Exception:
        # Each module unregisters only the hooks it installed.
        highlight.unregister()
        feature_editor.unregister()
        sketch_editor.unregister()
        lifecycle.unregister()
        presentation.unregister()
        capabilities.unregister()
        operators.unregister()
        for cls in reversed(_registered_classes):
            if hasattr(bpy.types, cls.__name__):
                bpy.utils.unregister_class(cls)
        del bpy.types.Scene.blendercad_document_snapshot
        del bpy.types.Scene.blendercad_selection_mode
        _registered_classes = ()
        raise


def unregister():
    import bpy
    from . import capabilities, highlight, lifecycle, operators, presentation, sketch_editor, feature_editor

    global _registered_classes
    highlight.unregister()
    feature_editor.unregister()
    sketch_editor.unregister()
    lifecycle.unregister()
    presentation.unregister()
    capabilities.unregister()
    operators.unregister()
    for cls in reversed(_registered_classes):
        bpy.utils.unregister_class(cls)
    _registered_classes = ()
    if hasattr(bpy.types.Scene, "blendercad_document_snapshot"):
        del bpy.types.Scene.blendercad_document_snapshot
    if hasattr(bpy.types.Scene, "blendercad_selection_mode"):
        del bpy.types.Scene.blendercad_selection_mode
