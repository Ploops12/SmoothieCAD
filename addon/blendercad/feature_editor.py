"""Transactional solid-feature input tools; no Blender data changes until commit.

The native draft owns modelling. This adapter owns input, source guards and
temporary GPU/cursor/timer lifetimes. The ghost result is not selectable geometry.
"""

import math
import threading

import bpy
from bpy.app.handlers import persistent
from bpy.props import EnumProperty, FloatProperty, StringProperty
from mathutils import Matrix, Vector

from . import _native as n
from . import commands, feature_commands, feature_view, presentation as p

active = None
_cancel_pending = False


def authorize_commit(scene, owner=None):
    if active is not None:
        if _cancel_pending:
            raise ValueError("Feature preview was invalidated")
        if owner is not active:
            raise ValueError("Finish or cancel the current feature preview first")
        active.validate(scene)
    elif owner is not None:
        raise ValueError("Feature preview is no longer active")


def _matrix_key(matrix):
    return tuple(value for row in matrix for value in row)


def _vector(value):
    return Vector((value.x, value.y, value.z))


class BLENDERCAD_OT_preview_feature(bpy.types.Operator):
    bl_idname = "blendercad.preview_feature"
    bl_label = "Preview CAD Feature"
    bl_description = "Preview a CAD result; drag its handle or type a dimension, Enter confirms and Esc cancels"
    bl_options = {"UNDO", "BLOCKING"}
    operation: EnumProperty(items=(("EXTRUDE", "Extrude", "Extrude a saved sketch"),
                                   ("CUT", "Cut", "Subtract a saved sketch from the selected Solid"),
                                   ("FILLET", "Fillet", "Round selected CAD edges"),
                                   ("DEPTH", "Depth", "Adjust an existing extrusion input"),
                                   ("RADIUS", "Radius", "Adjust an existing fillet input")))
    sketch_id: StringProperty(options={"HIDDEN"})
    feature_id: StringProperty(options={"HIDDEN"})
    value_mm: FloatProperty(name="Initial value (mm)", default=5)
    parameter: StringProperty(name="Initial length parameter")

    @classmethod
    def poll(cls, context):
        from . import sketch_editor
        return (n is not None and active is None and sketch_editor.active is None
                and not bpy.app.background and context.scene is not None
                and bool(context.scene.blendercad_document_snapshot) and context.mode == "OBJECT"
                and context.area is not None and context.area.type == "VIEW_3D")

    def validate(self, scene=None):
        if _cancel_pending or active is not self:
            raise ValueError("Feature preview was invalidated")
        if scene is not None and scene != self.scene:
            raise ValueError("Feature preview belongs to another scene")
        if (self.scene.blendercad_document_snapshot != self.serialized
                or p.unit_scale(self.scene) != self.scale or p.runtime(self.scene) is not self.source_runtime):
            raise ValueError("Document or scene units changed during feature preview")
        p._validate_associations(self.scene, self.document_id)
        owners = p.hosts(self.scene, self.document_id)
        placements = {obj.as_pointer(): _matrix_key(obj.matrix_world) for obj in owners}
        if placements != self.source_placements:
            raise ValueError("Solid ownership or placement changed during feature preview")
        if (self.window.scene != self.scene or self.area not in tuple(self.window.screen.areas)
                or self.area.type != "VIEW_3D" or self.area.spaces.active != self.space):
            raise ValueError("Feature viewport was closed or replaced")
        if bpy.context.mode != "OBJECT":
            raise ValueError("Feature preview requires Object Mode")

    def _operation(self, context, document):
        kind = self.operation.lower()
        host = None
        placement = Matrix.Identity(4)
        options = {}
        if kind in {"depth", "radius"}:
            feature = document.get_feature(self.feature_id)
            host = next((obj for obj in p.hosts(context.scene, document.id) if obj[p.BODY] == feature.body_id), None)
            if host is None:
                raise ValueError("Feature has no owning Solid")
            options["feature_id"] = self.feature_id
        elif kind == "fillet":
            _document, hosts = commands.selected(context, "edit_definition")
            host, edges = commands._fillet_picks(context, document, hosts)
            options["edges"] = tuple(edges)
            options["input_id"] = next(item.id for item in reversed(document.features) if item.body_id == host[p.BODY])
        else:
            options["sketch_id"] = self.sketch_id
            if kind == "cut":
                _document, hosts = commands.selected(context, "edit_definition")
                if len(hosts) != 1 or p.host_for(context.active_object) != hosts[0]:
                    raise ValueError("Select exactly one active Solid to cut")
                host = hosts[0]
                options["input_id"] = next(item.id for item in reversed(document.features) if item.body_id == host[p.BODY])
            else:
                host, placement = commands.sketch_placement(context, document, self.sketch_id)
        if host is not None:
            from .selection import _rigid
            _rigid(host)
            placement = host.matrix_world.copy()
        return feature_commands.Operation(kind, **options), placement

    def _handle_frame(self):
        feature = self.session.document.get_feature(self.session.feature_id)
        if feature.sketch_id is not None:
            problem = self.session.evaluator.evaluated_sketch(self.session.document, feature.sketch_id)
            plane = problem.plane
            points = []
            for entity in problem.entities:
                geometry = entity.geometry
                if isinstance(geometry, n.Line2):
                    points.extend(((geometry.start.x, geometry.start.y), (geometry.end.x, geometry.end.y)))
                else:
                    points.append((geometry.center.x, geometry.center.y))
            center = tuple(sum(point[i] for point in points) / len(points) for i in (0, 1)) if points else (0, 0)
            x, y = _vector(plane.x_axis), _vector(plane.y_axis)
            anchor = _vector(plane.origin) + x * center[0] + y * center[1]
            direction = x.cross(y).normalized()
        else:
            arrays = self.session.arrays
            vertices = arrays["cad_vertices"].reshape((-1, 3)) if arrays is not None else []
            anchor = Vector(tuple((vertices.min(axis=0) + vertices.max(axis=0)) / 2)) if len(vertices) else Vector()
            # A scalar radius handle, not a claim of face push/pull or a blend
            # location. Choose a visible local axis for a useful screen lever.
            candidates = [self.placement.to_3x3() @ Vector(axis) for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
            world_anchor = self.placement @ (anchor * self.scale)
            origin = feature_view.project(self.region, self.space.region_3d, world_anchor)
            def score(axis):
                point = feature_view.project(self.region, self.space.region_3d,
                                             world_anchor + axis * self.scale * max(self.session.value_mm, 1))
                return (point - origin).length_squared if point is not None and origin is not None else 0
            return world_anchor, max(candidates, key=score).normalized()
        return self.placement @ (anchor * self.scale), (self.placement.to_3x3() @ direction).normalized()

    def invoke(self, context, event):
        global active, _cancel_pending
        try:
            from . import sketch_editor
            sketch_editor.authorize_commit(context.scene)
            authorize_commit(context.scene)
            commands.capabilities.require_capability(context.selected_objects, "edit_definition")
            context.view_layer.update()
            document = p.read_document(context.scene)
            p._validate_associations(context.scene, document.id)
            self.scene, self.window, self.area = context.scene, context.window, context.area
            self.space = self.area.spaces.active
            self.region = next(region for region in self.area.regions if region.type == "WINDOW")
            self.serialized, self.scale = self.scene.blendercad_document_snapshot, p.unit_scale(self.scene)
            self.document_id = document.id
            self.source_runtime = p.runtime(self.scene)
            self.source_placements = {obj.as_pointer(): _matrix_key(obj.matrix_world) for obj in p.hosts(self.scene, document.id)}
            operation, self.placement = self._operation(context, document)
            value = self.parameter or self.value_mm
            if self.operation == "FILLET" and not self.properties.is_property_set("value_mm") and not self.parameter:
                value = 1.0
            if self.operation in {"DEPTH", "RADIUS"}:
                value = None
            self.session = feature_commands.Session(document, self.source_runtime.evaluator, operation, value)
            self.initial = self.session.fork()
            self.anchor, self.direction = self._handle_frame()
            self.drag, self.input_text, self.text_baseline = None, None, None
            self.message, self.overlay, self.overlay_arrays = "", None, None
            self.draw_error, self.draw_count = "", 0
            self.handlers = []
            active, _cancel_pending = self, False
            self.handlers.append(bpy.types.SpaceView3D.draw_handler_add(self.draw_3d, (), "WINDOW", "POST_VIEW"))
            self.handlers.append(bpy.types.SpaceView3D.draw_handler_add(self.draw_2d, (), "WINDOW", "POST_PIXEL"))
            self.timer = context.window_manager.event_timer_add(0.15, window=self.window)
            self.manager = context.window_manager
            self.window.cursor_modal_set("CROSSHAIR")
            self.manager.modal_handler_add(self)
            self.area.tag_redraw()
            return {"RUNNING_MODAL"}
        except Exception as error:
            self.finish()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def finish(self):
        global active, _cancel_pending
        for handle in getattr(self, "handlers", ()):
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
            except (ValueError, ReferenceError, RuntimeError):
                pass
        self.handlers = []
        if getattr(self, "timer", None) is not None:
            try:
                self.manager.event_timer_remove(self.timer)
            except (ValueError, ReferenceError, RuntimeError):
                pass
            self.timer = None
        try:
            if hasattr(self, "window"):
                self.window.cursor_modal_restore()
            if hasattr(self, "area"):
                self.area.tag_redraw()
        except (ReferenceError, RuntimeError):
            pass
        self.overlay, self.overlay_arrays = None, None
        self.session, self.initial, self.drag, self.text_baseline = None, None, None, None
        self.source_runtime = None
        if active is self:
            active, _cancel_pending = None, False

    def cancel(self, context):
        self.finish()

    def _in_view(self):
        return active is self and not _cancel_pending and bpy.context.area == self.area and bpy.context.scene == self.scene

    def draw_3d(self):
        if not self._in_view():
            return
        try:
            self.validate()
            arrays = self.session.arrays
            if arrays is not self.overlay_arrays:
                self.overlay = feature_view.Overlay(arrays, self.placement, self.scale) if arrays is not None else None
                self.overlay_arrays = arrays
            if self.overlay is not None:
                self.overlay.draw(self.session.valid)
            feature_view.draw_handle(self.anchor, self.direction, self.session.value_mm, self.scale, self.session.valid)
            self.draw_count += 1
            self.draw_error = ""
        except Exception as error:
            self.draw_error = f"{type(error).__name__}: {error}"

    def draw_2d(self):
        if not self._in_view():
            return
        try:
            self.validate()
            driver = f"={self.session.input} · " if isinstance(self.session.input, str) else ""
            lines = [f"{self.operation.title()} preview · {driver}{self.session.value_mm:.9g} mm",
                     "Ghost result · committed Solid unchanged | Drag handle / G adjust | D type | U unlink driver",
                     "Enter confirm · Esc cancel · R reset"]
            if self.input_text is not None:
                lines.append("Dimension (mm or =parameter): " + self.input_text + "_")
            elif self.drag:
                lines.append("Move to adjust · click/release accepts drag · Esc restores drag baseline")
            error = self.message or self.session.error or self.draw_error
            if error:
                lines.append(error)
            if not self.session.valid:
                lines.append("Invalid candidate · last valid result shown · confirmation disabled")
            feature_view.draw_text(self.region, lines, bool(error) or not self.session.valid)
            point = feature_view.project(self.region, self.space.region_3d,
                                         self.anchor + self.direction * self.session.value_mm * self.scale)
            feature_view.draw_knob(point, self.session.valid)
        except Exception as error:
            self.draw_error = f"{type(error).__name__}: {error}"

    def _begin_drag(self, event, mouse_mode=False):
        self.drag = (self.session.fork(), Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y)),
                     feature_view.drag_projection(self.region, self.space.region_3d, self.anchor, self.direction, self.scale),
                     self.session.value_mm, mouse_mode)

    def _drag_to(self, event):
        baseline, start, projection, value, _mouse_mode = self.drag
        mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        delta = (mouse - start).dot(projection) / projection.length_squared
        candidate = baseline.fork()
        candidate.set_numeric(value + delta)
        self.session = candidate
        self.message = ""

    def _type(self, text):
        candidate = self.text_baseline.fork()
        if text.startswith("="):
            if not text[1:].strip():
                raise ValueError("Enter a named length parameter after =")
            candidate.set_input(text[1:].strip())
        else:
            value = float(text)
            if not math.isfinite(value):
                raise ValueError("Enter a finite millimetre value")
            candidate.set_numeric(value)
        self.session = candidate

    def modal(self, context, event):
        if active is not self:
            return {"CANCELLED"}
        try:
            context.view_layer.update()
            self.validate(context.scene)
            if event.type == "TIMER":
                return {"RUNNING_MODAL"}
            self.area.tag_redraw()
            if self.input_text is not None:
                if event.value == "PRESS":
                    if event.type == "ESC":
                        self.session, self.input_text, self.text_baseline = self.text_baseline, None, None
                    elif event.type in {"RET", "NUMPAD_ENTER"}:
                        self._type(self.input_text)
                        self.input_text, self.text_baseline = None, None
                    elif event.type == "BACK_SPACE":
                        self.input_text = self.input_text[:-1]
                    elif event.type == "A" and event.ctrl:
                        self.input_text = ""
                    elif event.unicode and event.unicode.isprintable():
                        self.input_text += event.unicode
                    self.message = ""
                return {"RUNNING_MODAL"}
            if self.drag:
                if event.type == "ESC" and event.value == "PRESS":
                    self.session, self.drag = self.drag[0], None
                    self.message = ""
                elif event.type == "MOUSEMOVE":
                    self._drag_to(event)
                elif event.type == "LEFTMOUSE" and event.value == ("RELEASE" if self.drag[4] else "PRESS"):
                    self.drag = None
                return {"RUNNING_MODAL"}
            if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM", "NDOF_MOTION"}:
                return {"PASS_THROUGH"}
            if event.value != "PRESS":
                return {"RUNNING_MODAL"}
            self.message = ""
            if event.type in {"ESC", "RIGHTMOUSE"}:
                self.finish()
                return {"CANCELLED"}
            if event.type in {"RET", "NUMPAD_ENTER"}:
                if not self.session.valid:
                    raise ValueError(self.session.error or "Repair the invalid preview before confirmation")
                placements = {self.session.body_id: self.placement} if self.operation == "EXTRUDE" else None
                commands.commit(self.scene, self.session.document, feature_session=self, placements=placements)
                commands._select(context, [self.session.body_id])
                self.finish()
                return {"FINISHED"}
            if event.type == "G":
                self._begin_drag(event)
            elif event.type == "LEFTMOUSE":
                endpoint = feature_view.project(self.region, self.space.region_3d,
                                               self.anchor + self.direction * self.session.value_mm * self.scale)
                mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
                if endpoint is not None and (endpoint - mouse).length <= 16:
                    self._begin_drag(event, True)
            elif event.type == "R":
                self.session = self.initial.fork()
            elif event.type == "U":
                self.session.set_input(self.session.value_mm)
            elif event.type == "D" or (event.unicode and event.unicode[0] in "0123456789-.="):
                self.text_baseline = self.session.fork()
                self.input_text = "" if event.type == "D" else event.unicode
            return {"RUNNING_MODAL"}
        except (ValueError, RuntimeError, KeyError, TypeError, OverflowError, ReferenceError) as error:
            self.message = str(error)
            try:
                self.validate(context.scene)
            except (ValueError, RuntimeError, ReferenceError) as invalid:
                self.finish()
                self.report({"WARNING"}, str(invalid))
                return {"CANCELLED"}
            return {"RUNNING_MODAL"}
        except Exception as error:
            self.finish()
            self.report({"ERROR"}, f"Feature preview stopped: {type(error).__name__}: {error}")
            return {"CANCELLED"}


@persistent
def cancel_before_history(_unused):
    global _cancel_pending
    if threading.current_thread() is not threading.main_thread():
        _cancel_pending = True
        return
    if active is not None:
        active.finish()


_handlers = ("load_pre", "undo_pre", "redo_pre", "render_init")


def register():
    bpy.utils.register_class(BLENDERCAD_OT_preview_feature)
    for name in _handlers:
        getattr(bpy.app.handlers, name).append(cancel_before_history)


def unregister():
    cancel_before_history(None)
    for name in _handlers:
        handlers = getattr(bpy.app.handlers, name)
        if cancel_before_history in handlers:
            handlers.remove(cancel_before_history)
    if hasattr(bpy.types, "BLENDERCAD_OT_preview_feature"):
        bpy.utils.unregister_class(BLENDERCAD_OT_preview_feature)
