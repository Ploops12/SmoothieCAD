"""Main-thread Blender visibility/collection adapter; never evaluates CAD intent.

Empty parenting does not inherit these Blender settings. The host owns them;
the managed child is disposable presentation, not another visibility authority.
"""

from contextlib import contextmanager
import threading

import bpy
from bpy.app.handlers import persistent

AVAILABLE = "blendercad_available"
_suspended = 0
_rendering = False
_updating = False


@contextmanager
def suspended():
    global _suspended
    _suspended += 1
    try:
        yield
    finally:
        _suspended -= 1


def _spaces(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    yield area.spaces.active


def sync_pair(scene, host, display):
    """Mirror one validated local association; caller owns transaction rollback."""
    desired = set(host.users_collection)
    actual = set(display.users_collection)
    for collection in desired - actual:
        collection.objects.link(display)
    for collection in actual - desired:
        collection.objects.unlink(display)
    unavailable = not display.get(AVAILABLE, True)
    for key in ("hide_viewport", "hide_render"):
        value = unavailable or getattr(host, key)
        if getattr(display, key) != value:
            setattr(display, key, value)
    # Base visibility is per ViewLayer, not an Object datablock property. Do
    # not mistake collection exclusion (no Base) for a host hide_set value.
    for layer in scene.view_layers:
        if host.name in layer.objects and display.name in layer.objects:
            value = host.hide_get(view_layer=layer)
            if display.hide_get(view_layer=layer) != value:
                display.hide_set(value, view_layer=layer)
    for space in _spaces(scene):
        if space.local_view is not None:
            value = host.local_view_get(space)
            if display.local_view_get(space) != value:
                display.local_view_set(space, value)


def sync_scene(scene):
    """Synchronize only a previously validated, still-current runtime document."""
    from . import presentation as p
    state = p._runtimes.get(scene.as_pointer())
    if (state is None or state.document is None
            or state.serialized != scene.blendercad_document_snapshot):
        return
    try:
        p._validate_associations(scene, state.document.id)
        for host in p.hosts(scene, state.document.id):
            display = p.display_for(scene, host)
            if display is not None:
                sync_pair(scene, host, display)
        state.lifecycle_error = ""
    except (ValueError, RuntimeError, ReferenceError) as error:
        state.lifecycle_error = str(error)


def synchronize(scene=None):
    global _updating
    if (_suspended or _updating or threading.current_thread() is not threading.main_thread()):
        return
    _updating = True
    try:
        for item in (scene,) if scene is not None else bpy.data.scenes:
            sync_scene(item)
    finally:
        _updating = False


@persistent
def after_depsgraph(scene, _depsgraph):
    # Render workers must never mutate scene datablocks. Static render state
    # is synchronized before the render graph is built, not during rendering.
    if not _rendering:
        synchronize(scene)


@persistent
def before_save(_unused):
    if not _rendering:
        synchronize()


@persistent
def before_render(scene):
    global _rendering
    _rendering = True
    synchronize(scene)


@persistent
def after_render(_scene):
    global _rendering
    _rendering = False
    # Interactive render completion may execute off-main-thread. A timer
    # catches deferred viewport changes on Blender's main loop instead.


def _tick():
    if not _rendering:
        synchronize()
    return 0.2


_hooks = (("depsgraph_update_post", after_depsgraph), ("save_pre", before_save),
          ("render_init", before_render), ("render_complete", after_render),
          ("render_cancel", after_render))


def register():
    for name, callback in _hooks:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.2, persistent=True)


def unregister():
    global _rendering
    for name, callback in _hooks:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _rendering = False
