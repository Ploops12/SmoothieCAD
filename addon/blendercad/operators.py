"""Blender UI adapters. Modelling behavior belongs to the command service."""

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from mathutils import Vector

from . import commands, presentation as p


def frame_solids(context, hosts):
    if context.area is None or context.area.type != "VIEW_3D":
        return
    context.view_layer.update()
    points = [display.matrix_world @ Vector(corner) for host in hosts
              if (display := p.display_for(context.scene, host)) is not None
              for corner in display.bound_box]
    if not points:
        return
    low = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    center = (low + high) / 2
    radius = max((high - low).length / 2, 1e-6)
    space = context.area.spaces.active
    space.region_3d.view_location = center
    space.region_3d.view_distance = radius * 3
    space.clip_start = min(space.clip_start, max(radius * 0.01, 1e-6))


class CadOperator:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and bool(context.scene.blendercad_document_snapshot) and context.mode == "OBJECT"

    def execute(self, context):
        try:
            self.run(context)
        except (ValueError, RuntimeError, KeyError, TypeError, OverflowError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDERCAD_OT_add_rectangle_solid(CadOperator, bpy.types.Operator):
    bl_idname = "blendercad.add_rectangle_solid"
    bl_label = "Add Rectangle Solid"
    bl_description = "Create a constrained XY rectangle sketch and extrusion in this CAD document"
    width_mm: FloatProperty(name="Width (mm)", default=30, min=0.001)
    height_mm: FloatProperty(name="Height (mm)", default=20, min=0.001)
    depth_mm: FloatProperty(name="Depth (mm)", default=5, min=0.001)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def run(self, context):
        commands.add_rectangle(context, self.width_mm, self.height_mm, self.depth_mm)
        frame_solids(context, p.selected_hosts(context))


class BLENDERCAD_OT_recompute(CadOperator, bpy.types.Operator):
    bl_idname = "blendercad.recompute"
    bl_label = "Recompute CAD Document"

    def run(self, context):
        commands.capabilities.require_capability(context.selected_objects, "recompute")
        p.sync(context.scene)


class BLENDERCAD_OT_set_feature_depth(CadOperator, bpy.types.Operator):
    bl_idname = "blendercad.set_feature_depth"
    bl_label = "Edit Extrusion Depth"
    feature_id: StringProperty(options={"HIDDEN"})
    depth_mm: FloatProperty(name="Depth (mm)", default=5)
    parameter: StringProperty(name="Named length parameter", description="Leave empty to use the numeric depth")

    def invoke(self, context, event):
        try:
            depth = p.read_document(context.scene).get_feature(self.feature_id).depth
            if isinstance(depth, str):
                self.parameter = depth
            else:
                self.depth_mm = depth
        except (ValueError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def run(self, context):
        commands.set_depth(context, self.feature_id, self.parameter if self.parameter else self.depth_mm)


class BLENDERCAD_OT_parameter(CadOperator, bpy.types.Operator):
    bl_idname = "blendercad.parameter"
    bl_label = "Set CAD Parameter"
    name: StringProperty(name="Name", default="thickness")
    value: FloatProperty(name="Value", default=5)
    create: BoolProperty(name="Create new length parameter", default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def run(self, context):
        commands.set_parameter(context, self.name, self.value, "mm" if self.create else None)


class SolidOperator(CadOperator):
    @classmethod
    def poll(cls, context):
        return (super().poll(context) and bool(context.selected_objects)
                and all(p.host_for(obj) is not None for obj in context.selected_objects))


class BLENDERCAD_OT_duplicate_solid(SolidOperator, bpy.types.Operator):
    bl_idname = "blendercad.duplicate_solid"
    bl_label = "Duplicate CAD Solid"
    bl_description = "Copy selected body definitions with new persistent identities; named parameters remain shared"

    def run(self, context):
        commands.duplicate(context)


class BLENDERCAD_OT_frame_solid(SolidOperator, bpy.types.Operator):
    bl_idname = "blendercad.frame_solid"
    bl_label = "Frame CAD Solid"
    bl_options = {"REGISTER"}

    def run(self, context):
        frame_solids(context, p.selected_hosts(context))


class BLENDERCAD_OT_delete_solid(SolidOperator, bpy.types.Operator):
    bl_idname = "blendercad.delete_solid"
    bl_label = "Delete CAD Solid"
    bl_description = "Delete selected body feature chains; keep unused sketches for explicit later reuse"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def run(self, context):
        commands.delete(context)


class BLENDERCAD_OT_convert_to_regular_mesh(SolidOperator, bpy.types.Operator):
    bl_idname = "blendercad.convert_to_regular_mesh"
    bl_label = "Convert to Regular Mesh"
    bl_description = "End CAD behavior for selected bodies, replacing their hosts with current display meshes"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def run(self, context):
        commands.convert(context)


class BLENDERCAD_OT_pick_solid(bpy.types.Operator):
    bl_idname = "blendercad.pick_solid"
    bl_label = "Select CAD Solid"
    bl_options = {"INTERNAL"}
    extend: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and context.region.type == "WINDOW" and context.mode == "OBJECT"

    def invoke(self, context, event):
        from bpy_extras import view3d_utils
        position = event.mouse_region_x, event.mouse_region_y
        origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, position)
        direction = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, position)
        hit = p.pick_ray(context.scene, origin, direction, context.evaluated_depsgraph_get())
        if hit is None:
            return {"PASS_THROUGH"}
        host, _pick = hit
        if not self.extend:
            for obj in context.selected_objects:
                obj.select_set(False)
        host.select_set(not host.select_get() if self.extend else True)
        context.view_layer.objects.active = host
        return {"FINISHED"}


_classes = (BLENDERCAD_OT_add_rectangle_solid, BLENDERCAD_OT_recompute,
            BLENDERCAD_OT_set_feature_depth, BLENDERCAD_OT_parameter,
            BLENDERCAD_OT_duplicate_solid, BLENDERCAD_OT_frame_solid, BLENDERCAD_OT_delete_solid,
            BLENDERCAD_OT_convert_to_regular_mesh, BLENDERCAD_OT_pick_solid)
_keymaps = []
_registered = []


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
        _registered.append(cls)
    config = bpy.context.window_manager.keyconfigs.addon
    if config:
        keymap = config.keymaps.new(name="Object Mode", space_type="EMPTY")
        item = keymap.keymap_items.new("blendercad.frame_solid", "NUMPAD_PERIOD", "PRESS")
        _keymaps.append((keymap, item))
        for extend in (False, True):
            item = keymap.keymap_items.new("blendercad.pick_solid", "LEFTMOUSE", "PRESS", shift=extend)
            item.properties.extend = extend
            _keymaps.append((keymap, item))


def unregister():
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()
    for cls in reversed(_registered):
        bpy.utils.unregister_class(cls)
    _registered.clear()
