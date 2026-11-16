"""Blender modal adapter for detached sketch commands and disposable overlays.

No scene snapshot, CAD mesh or native feature cache changes until confirmation.
An edit owns a content/units/placement guard, not a second saved definition.
"""

import math
import threading

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Matrix, Vector

from . import _native as n
from . import commands, presentation as p, sketch_commands, sketch_view

active = None
_cancel_pending = False


def authorize_commit(scene, owner=None):
    if active is not None:
        if _cancel_pending:
            raise ValueError("Sketch edit was invalidated")
        if owner is not active:
            raise ValueError("Finish or cancel the current sketch edit first")
        active.validate(scene)
    elif owner is not None:
        raise ValueError("Sketch edit is no longer active")


def _key(reference):
    return reference.entity, reference.element.name


def _point(problem, reference):
    if not reference.entity:
        return (0.0, 0.0)
    entity = next(item for item in problem.entities if item.id == reference.entity)
    geometry = entity.geometry
    if reference.element == n.SketchElement.LINE_START:
        return geometry.start.x, geometry.start.y
    if reference.element == n.SketchElement.LINE_END:
        return geometry.end.x, geometry.end.y
    if reference.element == n.SketchElement.CIRCLE_CENTER:
        return geometry.center.x, geometry.center.y
    raise ValueError("Select a sketch endpoint or circle center")


def _plane(context, mode, scale, placement):
    plane = n.SketchPlane()
    location = (placement.inverted() @ context.scene.cursor.location) / scale
    plane.origin = n.Vector3(*location)
    axes = {"XY": ((1, 0, 0), (0, 1, 0)), "XZ": ((1, 0, 0), (0, 0, 1)),
            "YZ": ((0, 1, 0), (0, 0, 1))}
    if mode == "CURSOR":
        rotation = placement.inverted().to_3x3() @ context.scene.cursor.matrix.to_3x3()
        x, y = tuple(rotation.col[0]), tuple(rotation.col[1])
        # Blender matrices are float presentation values. Reorthonormalize in
        # double precision before constructing a native design plane.
        length = math.hypot(*x)
        x = tuple(value / length for value in x)
        dot = math.fsum(a * b for a, b in zip(x, y))
        y = tuple(b - dot * a for a, b in zip(x, y))
        length = math.hypot(*y)
        y = tuple(value / length for value in y)
    else:
        x, y = axes[mode]
    plane.x_axis, plane.y_axis = n.Vector3(*x), n.Vector3(*y)
    return plane


class BLENDERCAD_OT_edit_sketch(bpy.types.Operator):
    bl_idname = "blendercad.edit_sketch"
    bl_label = "Edit CAD Sketch"
    bl_description = "Draw, constrain and drag a planar sketch; Enter confirms, Esc cancels"
    # Mouse strokes are committed definitions, not replayable operator props.
    # Blender undo/redo restores snapshots; F9 must not attempt to replay a modal
    # history from just the plane/name fields.
    bl_options = {"UNDO", "BLOCKING"}
    sketch_id: StringProperty(options={"HIDDEN"})
    name: StringProperty(name="Sketch name", default="Sketch")
    plane: EnumProperty(name="Plane", items=(("XY", "XY", "XY at the 3D cursor"),
                                             ("XZ", "XZ", "XZ at the 3D cursor"),
                                             ("YZ", "YZ", "YZ at the 3D cursor"),
                                             ("CURSOR", "Cursor", "Plane oriented by the 3D cursor"),
                                             ("FACE", "Selected Face", "Associative support on one selected planar CAD face")), default="XY")
    reverse_support: BoolProperty(name="Into Solid", default=True,
        description="Reverse the support normal for positive-depth cuts; the local Y direction reverses too")

    @classmethod
    def poll(cls, context):
        from . import feature_editor
        return (n is not None and active is None and feature_editor.active is None and context.scene is not None
                and bool(context.scene.blendercad_document_snapshot) and context.mode == "OBJECT"
                and context.area is not None and context.area.type == "VIEW_3D")

    def validate(self, scene=None):
        if scene is not None and scene != self.scene:
            raise ValueError("Sketch edit belongs to another scene")
        if self.scene.blendercad_document_snapshot != self.serialized or p.unit_scale(self.scene) != self.scale:
            raise ValueError("Document or scene units changed during sketch editing")
        if self.host is not None and self.host.matrix_world != self.placement:
            raise ValueError("Solid placement changed during sketch editing")
        if hasattr(self, "window") and all(area != self.area for area in self.window.screen.areas):
            raise ValueError("Sketch viewport was closed or replaced")
        if self.area.type != "VIEW_3D" or self.area.spaces.active != self.space:
            raise ValueError("Sketch viewport was closed or replaced")

    def invoke(self, context, event):
        global active, _cancel_pending
        try:
            from . import feature_editor
            feature_editor.authorize_commit(context.scene)
            commands.capabilities.require_capability(context.selected_objects, "edit_definition")
            document = p.read_document(context.scene)
            self.scene, self.area = context.scene, context.area
            self.region = next(region for region in self.area.regions if region.type == "WINDOW")
            self.space = self.area.spaces.active
            self.scale, self.serialized = p.unit_scale(self.scene), self.scene.blendercad_document_snapshot
            support = commands.selected_face_support(context, self.reverse_support) if not self.sketch_id and self.plane == "FACE" else None
            self.host, self.placement = commands.sketch_placement(context, document, self.sketch_id)
            plane = None if self.sketch_id or support is not None else _plane(context, self.plane, self.scale, self.placement)
            name = self.name
            if not self.sketch_id and name == "Sketch":
                names = {item.name for item in document.sketches}
                number = 1
                while f"Sketch {number}" in names:
                    number += 1
                name = f"Sketch {number}"
            self.session = sketch_commands.Session(document, self.sketch_id, name, plane,
                support=support, evaluator=p.runtime(self.scene).evaluator)
            self.sketch_name = next(item.name for item in self.session.document.sketches if item.id == self.session.sketch_id)
            self.selected, self.constraint = [], ""
            self.tool = "SELECT" if self.sketch_id else "LINE"
            self.start, self.mouse_uv, self.drag = None, None, None
            self.shift = False
            self.pending_drag, self.input_action, self.input_text, self.message = None, None, "", ""
            view = self.space.region_3d
            self.previous_view = (view.view_rotation.copy(), view.view_location.copy(), view.view_distance,
                                  view.view_perspective, self.space.clip_start, self.space.clip_end)
            plane = self.session.display_problem.plane
            x = Vector((plane.x_axis.x, plane.x_axis.y, plane.x_axis.z))
            y = Vector((plane.y_axis.x, plane.y_axis.y, plane.y_axis.z))
            rotation = self.placement.to_3x3() @ Matrix((x, y, x.cross(y))).transposed()
            view.view_rotation = rotation.to_quaternion()
            view.view_location = sketch_view.world_point(plane, (0, 0), self.placement, self.scale)
            view.view_perspective = "ORTHO"
            positions = [sketch_view.world_point(plane, uv, self.placement, self.scale)
                         for entity in self.session.display_problem.entities
                         for uv in self._entity_points(entity)]
            if positions:
                low = Vector(tuple(min(point[axis] for point in positions) for axis in range(3)))
                high = Vector(tuple(max(point[axis] for point in positions) for axis in range(3)))
                view.view_location = (low + high) / 2
                extent = max((point - view.view_location).length for point in positions)
            else:
                extent = 50 * self.scale
            if not positions and self.host is not None:
                display = p.display_for(self.scene, self.host)
                if display is not None:
                    bounds = [display.matrix_world @ Vector(corner) for corner in display.bound_box]
                    center = sum(bounds, Vector()) / len(bounds)
                    extent = max((point - center).length for point in bounds)
                    if support is not None:
                        normal = (self.placement.to_3x3() @ x.cross(y)).normalized()
                        view.view_location = center - normal * (center - view.view_location).dot(normal)
            view.view_distance = max(extent * 2.5, document.geometric_tolerance_mm * self.scale * 20, 1e-7)
            self.space.clip_start = max(1e-6, view.view_distance * 0.01)
            self.space.clip_end = max(0.01, view.view_distance * 10)
            active = self
            _cancel_pending = False
            self.handlers = []
            self.handlers.append(bpy.types.SpaceView3D.draw_handler_add(self.draw_3d, (), "WINDOW", "POST_VIEW"))
            self.handlers.append(bpy.types.SpaceView3D.draw_handler_add(self.draw_2d, (), "WINDOW", "POST_PIXEL"))
            context.window.cursor_modal_set("CROSSHAIR")
            self.window = context.window
            context.window_manager.modal_handler_add(self)
            self.area.tag_redraw()
            return {"RUNNING_MODAL"}
        except (ValueError, RuntimeError, TypeError, ReferenceError, OverflowError) as error:
            self.finish()
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception as error:
            self.finish()
            self.report({"ERROR"}, f"Sketch editor stopped: {type(error).__name__}: {error}")
            return {"CANCELLED"}

    @staticmethod
    def _entity_points(entity):
        geometry = entity.geometry
        if isinstance(geometry, n.Line2):
            return ((geometry.start.x, geometry.start.y), (geometry.end.x, geometry.end.y))
        return ((geometry.center.x + geometry.radius, geometry.center.y),
                (geometry.center.x - geometry.radius, geometry.center.y))

    def finish(self):
        global active, _cancel_pending
        for handle in getattr(self, "handlers", ()):
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
            except (ValueError, ReferenceError, RuntimeError):
                pass
        self.handlers = []
        try:
            if hasattr(self, "previous_view"):
                rotation, location, distance, perspective, clip, clip_end = self.previous_view
                view = self.space.region_3d
                view.view_rotation, view.view_location = rotation, location
                view.view_distance, view.view_perspective = distance, perspective
                self.space.clip_start = clip
                self.space.clip_end = clip_end
            if hasattr(self, "area"):
                self.area.tag_redraw()
        except (ReferenceError, RuntimeError):
            pass
        # A closed/replaced Area can invalidate the view before the Window is
        # destroyed. Cursor cleanup must not be skipped by that earlier error.
        try:
            if hasattr(self, "window"):
                self.window.cursor_modal_restore()
        except (ReferenceError, RuntimeError):
            pass
        # Undo stores the committed scene snapshot, not speculative kernel
        # caches. Do not retain a private evaluator in a completed operator.
        session = getattr(self, "session", None)
        if session is not None:
            session.evaluator = None
        self.drag, self.pending_drag = None, None
        if active is self:
            active = None
            _cancel_pending = False

    def cancel(self, context):
        self.finish()

    def _in_view(self):
        return not _cancel_pending and bpy.context.area == self.area and bpy.context.scene == self.scene

    def draw_3d(self):
        if self._in_view():
            try:
                self.validate()
                sketch_view.draw_3d(self.session, self.placement, self.scale, self.selected,
                                    self.constraint, self.rubber())
            except (ValueError, RuntimeError, ReferenceError):
                return

    def draw_2d(self):
        if self._in_view():
            try:
                self.validate()
                report = self.session.report
                state = f"{report.state.name.replace('_', ' ').title()} · {report.remaining_degrees_of_freedom} DOF" if report else "Unresolved"
                help_text = "S select | L/C/R draw | G drag | D dimension | H/V/P/O/K constraints | Tab cycle | X delete"
                status = self.message or self.session.error or "Enter confirm · Esc cancel · Right-click clears drawing"
                frame = "Solid-local frame" if self.host is not None else "Document frame"
                if any(item.id == self.session.sketch_id and item.support is not None for item in self.session.document.sketches):
                    frame = "Face-attached · " + frame
                lines = [f"{self.sketch_name} · {self.tool.title()} · {state} · {frame}", help_text, status]
                if self.constraint:
                    constraints = self.session.document.get_sketch(self.session.sketch_id).constraints
                    for index, constraint in enumerate(constraints):
                        if constraint.id != self.constraint:
                            continue
                        label = f"Constraint {index + 1}: {constraint.kind.name.replace('_', ' ').title()}"
                        if constraint.parameter is not None:
                            label += f" · {constraint.parameter} = {self.session.document.length_value(constraint.parameter):g} mm"
                        elif constraint.kind.name in {"LENGTH", "RADIUS", "DIAMETER", "DISTANCE", "DISTANCE_X", "DISTANCE_Y"}:
                            label += f" · {constraint.value:g} mm"
                        if report and constraint.id in report.conflicting_constraints:
                            label += " · Conflicting"
                        if report and constraint.id in report.redundant_constraints:
                            label += " · Redundant"
                        lines.append(label)
                        break
                if self.input_action is not None:
                    lines.append(f"Dimension in mm (or =parameter): {self.input_text}_")
                sketch_view.draw_2d(self.session, self.placement, self.scale, self.region,
                                    self.space.region_3d, self.constraint, lines)
            except (ValueError, RuntimeError, ReferenceError):
                return

    def hit(self, mouse, points_only=False):
        return sketch_view.hit(self.session.display_problem, self.placement, self.scale,
                               self.region, self.space.region_3d, mouse,
                               self.session.document.get_sketch(self.session.sketch_id), points_only)

    def cursor(self, event):
        mouse = event.mouse_x - self.region.x, event.mouse_y - self.region.y
        uv = sketch_view.plane_point(self.region, self.space.region_3d, mouse,
                                    self.session.display_problem.plane, self.placement, self.scale)
        return mouse, uv

    def rubber(self):
        if self.start is None or self.mouse_uv is None:
            return ()
        first, _reference = self.start
        last = self.mouse_uv
        if self.tool == "LINE":
            if self.shift:
                last = (last[0], first[1]) if abs(last[0] - first[0]) >= abs(last[1] - first[1]) else (first[0], last[1])
            return ((first, last),)
        if self.tool == "RECTANGLE":
            return ((first, (last[0], first[1]), last, (first[0], last[1]), first),)
        if self.tool == "CIRCLE":
            radius = math.dist(first, last)
            return (tuple((first[0] + radius * math.cos(i * math.tau / 64),
                           first[1] + radius * math.sin(i * math.tau / 64)) for i in range(65)),)
        return ()

    def _draw_click(self, mouse, uv, shift):
        if uv is None:
            raise ValueError("Sketch plane is outside this view or parallel to the cursor ray")
        hit = self.hit(mouse, True)
        reference = hit[1] if hit is not None else None
        if hit is not None:
            uv = hit[2]
        if self.start is None:
            self.start = uv, reference
            return
        first, first_reference = self.start
        if self.tool == "LINE":
            horizontal = vertical = False
            if shift:
                horizontal = abs(uv[0] - first[0]) >= abs(uv[1] - first[1])
                vertical = not horizontal
                uv = (uv[0], first[1]) if horizontal else (first[0], uv[1])
            entity = self.session.add_line(first, uv, first_reference, reference, horizontal, vertical)
            self.start = uv, n.SketchReference(entity, n.SketchElement.LINE_END)
        elif self.tool == "CIRCLE":
            self.session.add_circle(first, math.dist(first, uv), first_reference)
            self.start = None
        elif self.tool == "RECTANGLE":
            self.session.add_rectangle(first, uv, first_reference)
            self.start = None

    def _select(self, hit, extend):
        self.constraint = ""
        if not extend:
            self.selected = []
        if hit is None:
            return
        if hit[0] == "DIMENSION":
            self.selected = []
            self.constraint = hit[1]
            return
        reference = hit[1]
        key = _key(reference)
        if any(_key(item) == key for item in self.selected):
            self.selected = [item for item in self.selected if _key(item) != key]
        else:
            self.selected.append(reference)

    def _begin_drag(self, mouse, uv, reference=None, release=False):
        if uv is None:
            raise ValueError("Cannot drag on a parallel or clipped sketch plane")
        if self.constraint:
            raw = next(item for item in self.session.document.get_sketch(self.session.sketch_id).constraints if item.id == self.constraint)
            value = self.session.document.length_value(raw.parameter) if raw.parameter is not None else raw.value
            offset_uv = sketch_view.plane_point(self.region, self.space.region_3d, (mouse[0] + 20, mouse[1]),
                        self.session.display_problem.plane, self.placement, self.scale)
            if offset_uv is None:
                raise ValueError("Cannot determine dimension drag scale in this view")
            self.drag = self.session.fork(), mouse, uv, None, self.constraint, value, math.dist(uv, offset_uv) / 20, release
        else:
            reference = reference or (self.selected[-1] if self.selected else None)
            if reference is None or not reference.entity or reference.element == n.SketchElement.GEOMETRY:
                raise ValueError("Select an endpoint, circle center or dimension to drag")
            self.drag = self.session.fork(), mouse, uv, reference, "", 0, 0, release

    def _move_drag(self, mouse, uv):
        if uv is None:
            return
        baseline, first_mouse, first_uv, reference, dimension, value, per_pixel, _release = self.drag
        candidate = baseline.fork()
        if dimension:
            candidate.drag_dimension(dimension, value + (mouse[0] - first_mouse[0]) * per_pixel)
        else:
            original = _point(baseline.display_problem, reference)
            candidate.move_point(reference, (original[0] + uv[0] - first_uv[0], original[1] + uv[1] - first_uv[1]))
        self.session = candidate
        self.message = ""

    def _dimension_input(self, kind=None):
        if self.constraint:
            self.input_action = "edit", self.constraint, None
        else:
            targets = list(self.selected)
            if kind is None:
                if len(targets) == 1 and targets[0].element == n.SketchElement.GEOMETRY:
                    entity = next(item for item in self.session.display_problem.entities if item.id == targets[0].entity)
                    kind = n.SketchConstraintKind.LENGTH if isinstance(entity.geometry, n.Line2) else n.SketchConstraintKind.RADIUS
                elif len(targets) == 2 and all(item.element != n.SketchElement.GEOMETRY for item in targets):
                    kind = n.SketchConstraintKind.DISTANCE
                else:
                    raise ValueError("Select one line/circle or two points for a dimension")
            self.input_action = "add", kind, targets
        self.input_text = ""

    def _input_event(self, event):
        if event.value != "PRESS":
            return
        if event.type == "ESC":
            self.input_action, self.input_text = None, ""
        elif event.type in {"RET", "NUMPAD_ENTER"}:
            value = self.input_text[1:] if self.input_text.startswith("=") else float(self.input_text)
            action, target, references = self.input_action
            if action == "edit":
                self.session.set_dimension(target, value)
            else:
                self.constraint = self.session.add_constraint(target, references,
                    value=0 if isinstance(value, str) else value,
                    parameter=value if isinstance(value, str) else None)
                self.selected = []
            self.input_action, self.input_text = None, ""
        elif event.type == "BACK_SPACE":
            self.input_text = self.input_text[:-1]
        else:
            text = event.unicode
            if not text:
                text = {"ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
                        "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9", "PERIOD": ".",
                        "MINUS": "-", "EQUAL": "="}.get(event.type, "")
                if not text and len(event.type) == 1:
                    text = event.type if event.shift else event.type.lower()
            if text and text.isprintable() and len(self.input_text) < 1024:
                self.input_text += text

    def modal(self, context, event):
        if active is not self or _cancel_pending:
            self.finish()
            return {"CANCELLED"}
        try:
            self.validate(context.scene)
            mouse, uv = self.cursor(event)
            self.mouse_uv = uv
            self.shift = event.shift
            if self.tool != "SELECT" and self.drag is None and self.input_action is None:
                snap = self.hit(mouse, True)
                if snap is not None:
                    self.mouse_uv = snap[2]
            self.area.tag_redraw()
            if self.input_action is not None:
                self._input_event(event)
                return {"RUNNING_MODAL"}
            if event.type == "ESC" and event.value == "PRESS":
                if self.drag is not None:
                    self.session = self.drag[0]
                    self.drag, self.pending_drag = None, None
                    return {"RUNNING_MODAL"}
                self.finish()
                return {"CANCELLED"}
            if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
                commands.commit(self.scene, self.session.document, sketch_session=self)
                self.finish()
                return {"FINISHED"}
            if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM", "NDOF_MOTION"}:
                return {"PASS_THROUGH"}
            if self.drag is not None:
                if event.type == "MOUSEMOVE":
                    self._move_drag(mouse, uv)
                if event.type == "LEFTMOUSE" and event.value == ("RELEASE" if self.drag[-1] else "PRESS"):
                    self.drag, self.pending_drag = None, None
                return {"RUNNING_MODAL"}
            if event.type == "MOUSEMOVE" and self.pending_drag is not None:
                first_mouse, first_uv, reference = self.pending_drag
                if math.dist(mouse, first_mouse) > 4:
                    self._begin_drag(first_mouse, first_uv, reference, True)
                    self._move_drag(mouse, uv)
                return {"RUNNING_MODAL"}
            if event.type == "LEFTMOUSE" and event.value == "RELEASE":
                self.pending_drag = None
            inside = 0 <= mouse[0] < self.region.width and 0 <= mouse[1] < self.region.height
            if event.type == "LEFTMOUSE" and event.value == "PRESS" and inside:
                self.message = ""
                if self.tool == "SELECT":
                    hit = self.hit(mouse)
                    self._select(hit, event.shift)
                    if hit is not None and hit[0] == "POINT" and hit[1].entity and not event.shift:
                        self.pending_drag = mouse, uv, hit[1]
                else:
                    self._draw_click(mouse, uv, event.shift)
            elif event.type == "RIGHTMOUSE" and event.value == "PRESS":
                self.start = None
                self.tool = "SELECT"
            elif event.value == "PRESS":
                self.message = ""
                if event.type == "TAB":
                    constraints = self.session.document.get_sketch(self.session.sketch_id).constraints
                    if not constraints:
                        raise ValueError("This sketch has no constraints to select")
                    identifiers = [item.id for item in constraints]
                    index = identifiers.index(self.constraint) if self.constraint in identifiers else (0 if event.shift else -1)
                    self.constraint = identifiers[(index + (-1 if event.shift else 1)) % len(identifiers)]
                    self.selected = []
                elif event.type in {"L", "C", "R", "S"}:
                    self.tool = {"L": "LINE", "C": "CIRCLE", "R": "RECTANGLE", "S": "SELECT"}[event.type]
                    self.start = None
                elif event.type == "G":
                    self._begin_drag(mouse, uv)
                elif event.type in {"D", "A", "B"}:
                    kind = {"A": n.SketchConstraintKind.DISTANCE_X, "B": n.SketchConstraintKind.DISTANCE_Y}.get(event.type)
                    if event.type == "D" and event.shift:
                        kind = n.SketchConstraintKind.DIAMETER
                    self._dimension_input(kind)
                elif event.type in {"H", "V", "P", "O", "K"}:
                    kind = {"H": n.SketchConstraintKind.HORIZONTAL, "V": n.SketchConstraintKind.VERTICAL,
                            "P": n.SketchConstraintKind.PARALLEL, "O": n.SketchConstraintKind.PERPENDICULAR,
                            "K": n.SketchConstraintKind.COINCIDENT}[event.type]
                    self.constraint = self.session.add_constraint(kind, self.selected)
                    self.selected = []
                elif event.type in {"X", "DEL"}:
                    if self.constraint:
                        self.session.erase_constraint(self.constraint)
                    elif self.selected:
                        self.session.erase_targets(self.selected)
                    self.selected, self.constraint = [], ""
            return {"RUNNING_MODAL"}
        except (ValueError, RuntimeError, TypeError, ReferenceError, StopIteration, OverflowError) as error:
            self.message = str(error)
            # Invalidated scene/placement cannot be repaired inside a detached
            # session. Cancel without overwriting the external state.
            try:
                self.validate(context.scene)
            except (ValueError, ReferenceError):
                self.finish()
                self.report({"WARNING"}, str(error))
                return {"CANCELLED"}
            self.area.tag_redraw()
            return {"RUNNING_MODAL"}
        except Exception as error:
            self.finish()
            self.report({"ERROR"}, f"Sketch editor stopped: {type(error).__name__}: {error}")
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
    bpy.utils.register_class(BLENDERCAD_OT_edit_sketch)
    for name in _handlers:
        getattr(bpy.app.handlers, name).append(cancel_before_history)


def unregister():
    cancel_before_history(None)
    for name in _handlers:
        handlers = getattr(bpy.app.handlers, name)
        if cancel_before_history in handlers:
            handlers.remove(cancel_before_history)
    if hasattr(bpy.types, "BLENDERCAD_OT_edit_sketch"):
        bpy.utils.unregister_class(BLENDERCAD_OT_edit_sketch)
    sketch_view.clear()
