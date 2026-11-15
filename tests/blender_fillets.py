"""Packaged fillet selection, history repair, duplication, persistence and undo."""

import argparse
import importlib
import math
from pathlib import Path
import sys
import tempfile
import zipfile

import bpy

parser = argparse.ArgumentParser()
parser.add_argument("--addon-zip", required=True)
options = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
LOSS = 1 - math.pi / 4


def close(actual, expected):
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-8), (actual, expected)


def select_owner(scene, body):
    document = p.read_document(scene)
    host = next(item for item in p.hosts(scene, document.id) if item[p.BODY] == body)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    host.select_set(True)
    bpy.context.view_layer.objects.active = host
    return host


def select_index(scene, body, kind, index):
    host = select_owner(scene, body)
    packet = p.current_packet(scene, host)
    pick = p.resolve(scene, host, kind, index, packet.token)
    state = p.runtime(scene)
    state.last_pick = pick
    state.selected_picks = [pick]
    return pick


def select_target(scene, target):
    document = p.read_document(scene)
    resolution = p.runtime(scene).evaluator.resolve_reference(document, target)
    assert resolution.state == n.ReferenceState.RESOLVED, resolution.state
    assert len(resolution.candidates) == 1
    candidate = resolution.candidates[0]
    return select_index(scene, target.body_id, candidate.kind.name, candidate.index)


def rejected(operator, **kwargs):
    scene = bpy.context.scene
    baseline = scene.blendercad_document_snapshot
    state = p.runtime(scene)
    evaluator = state.evaluator
    try:
        result = operator(**kwargs)
    except RuntimeError:
        pass  # Blender may raise when an operator reports ERROR in background.
    else:
        assert result == {"CANCELLED"}, result
    assert scene.blendercad_document_snapshot == baseline
    assert p.runtime(scene).evaluator is evaluator


with tempfile.TemporaryDirectory() as directory:
    with zipfile.ZipFile(options.addon_zip) as archive:
        archive.extractall(directory)
    sys.path.insert(0, directory)
    addon = importlib.import_module("blendercad")
    addon.register()
    try:
        from blendercad import commands, presentation as p
        n = addon._native
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.blendercad.new_document()
        bpy.ops.blendercad.add_rectangle_solid(width_mm=30, height_mm=20, depth_mm=5)
        bpy.ops.blendercad.parameter(name="round_radius", value=1, create=True)
        scene = bpy.context.scene
        document = p.read_document(scene)
        base = document.features[0].id
        body = document.get_feature(base).body_id
        lines = document.get_sketch(document.get_feature(base).sketch_id).entities
        edges = [n.PersistentReference(body, n.TopologyKind.EDGE, n.ReferenceAnchor(base, "edge:sweep:" + line.id + ":start"))
                 for line in lines]
        stale_pick = select_target(scene, edges[0])
        assert bpy.ops.blendercad.fillet(parameter="round_radius") == {"FINISHED"}
        document = p.read_document(scene)
        fillet = document.features[-1].id
        assert document.get_feature(fillet).type == "fillet"
        assert document.get_feature(fillet).radius == "round_radius"
        assert document.get_feature(fillet).input_id == base
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 3000 - LOSS * 5)
        assert bpy.ops.blendercad.parameter(name="round_radius", value=2) == {"FINISHED"}
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 3000 - LOSS * 4 * 5)
        evaluator = p.runtime(scene).evaluator
        host = select_owner(scene, body)
        face = next(i for i in range(p.current_packet(scene, host).arrays["face_count"]) if (
            (radius := evaluator.measure_element(document, body, n.TopologyElement(n.TopologyKind.FACE, i)).radius_mm) is not None
            and math.isclose(radius, 2, rel_tol=1e-9)))
        select_index(scene, body, "FACE", face)
        reference_id = commands.save_reference(bpy.context, "Fillet surface")
        assert bpy.ops.blendercad.set_fillet_radius(feature_id=fillet, radius_mm=1.5, parameter="") == {"FINISHED"}
        document = p.read_document(scene)
        close(p.runtime(scene).evaluator.measure_reference(document, document.get_reference(reference_id).target).radius_mm, 1.5)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), 3000 - LOSS * 1.5 ** 2 * 5)
        # Selection indices from a previous publication cannot create another
        # feature, even if the body and analytic edge still exist.
        baseline = scene.blendercad_document_snapshot
        p.runtime(scene).last_pick = stale_pick
        p.runtime(scene).selected_picks = [stale_pick]
        try:
            commands.add_fillet(bpy.context, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("Fillet accepted stale CAD picking data")
        assert scene.blendercad_document_snapshot == baseline
        rejected(bpy.ops.blendercad.move_feature, feature_id=fillet, offset=-1)
        assert bpy.ops.blendercad.set_fillet_radius(feature_id=fillet, radius_mm=100, parameter="") == {"FINISHED"}
        assert p.runtime(scene).report.features[-1].state == n.EvaluationState.FAILED
        assert not p.display_for(scene, host)["blendercad_current"]
        try:
            p.current_packet(scene, host)
        except ValueError:
            pass
        else:
            raise AssertionError("Failed fillet exposed current picking data")
        assert bpy.ops.blendercad.set_fillet_radius(feature_id=fillet, radius_mm=1.5, parameter="") == {"FINISHED"}
        assert bpy.ops.blendercad.suppress_feature(feature_id=fillet, suppressed=True) == {"FINISHED"}
        assert p.runtime(scene).report.features[-1].state == n.EvaluationState.SUPPRESSED
        assert bpy.ops.blendercad.suppress_feature(feature_id=fillet, suppressed=False) == {"FINISHED"}

        # Broken inline intent is retained. Roll back to the predecessor, pick
        # a replacement current edge, repair explicitly, then expose history.
        document = p.read_document(scene)
        unknown = n.PersistentReference(body, n.TopologyKind.EDGE, n.ReferenceAnchor(base, "missing:repairable:edge"))
        document.set_fillet_edges(fillet, [unknown])
        commands.commit(scene, document)
        assert p.runtime(scene).report.features[-1].state == n.EvaluationState.FAILED
        assert bpy.ops.blendercad.rollback(feature_id=fillet) == {"FINISHED"}
        close(p.runtime(scene).evaluator.volume_mm3(p.read_document(scene), body), 3000)
        replacement_pick = select_target(scene, edges[1])
        replacement = p.persistent_target(scene, bpy.context.active_object, replacement_pick)
        assert bpy.ops.blendercad.repair_fillet_edges(feature_id=fillet) == {"FINISHED"}
        document = p.read_document(scene)
        assert document.rollback_before is None, "Explicit repair did not resume the complete feature history"
        assert document.get_feature(fillet).edges[0].anchor.key == replacement.anchor.key
        assert p.runtime(scene).report.features[-1].state == n.EvaluationState.UP_TO_DATE
        expected = 3000 - LOSS * 1.5 ** 2 * 5
        close(p.runtime(scene).evaluator.volume_mm3(document, body), expected)
        assert p.runtime(scene).evaluator.resolve_reference(document, document.get_reference(reference_id).target).state != n.ReferenceState.RESOLVED, (
            "Changing a fillet's source edge silently substituted a different generated face")
        step_path = str(Path(directory) / "filleted-blender-part.step")
        p.runtime(scene).evaluator.write_step(document, body, step_path)
        kernel = n.new_geometry_adapter()
        exchanged = kernel.read_step(step_path)
        close(kernel.volume_mm3(exchanged), expected)

        # A second fillet consumes an actual generated circular edge. Its
        # inline key includes prior feature provenance, exercising semantic
        # remapping during independent body duplication rather than only the
        # simple base-extrusion UUID case.
        host = select_owner(scene, body)
        packet = p.current_packet(scene, host)
        generated_edge = None
        generated_diagnostics = []
        for index in range(len(packet.arrays["edge_offsets"]) - 1):
            measure = p.runtime(scene).evaluator.measure_element(document, body, n.TopologyElement(n.TopologyKind.EDGE, index))
            if measure.radius_mm is not None and math.isclose(measure.radius_mm, 1.5, rel_tol=1e-9):
                try:
                    target = p.runtime(scene).evaluator.capture_reference(document, body, n.TopologyKind.EDGE, index)
                except ValueError as error:
                    generated_diagnostics.append((index, str(error)))
                    continue
                if target.anchor.feature_id == fillet:
                    generated_edge = index
                    break
                generated_diagnostics.append((index, target.anchor.feature_id, target.anchor.key))
        assert generated_edge is not None, ("No generated circular edge had identifiable provenance", generated_diagnostics)
        select_index(scene, body, "EDGE", generated_edge)
        assert bpy.ops.blendercad.fillet(radius_mm=0.2, parameter="") == {"FINISHED"}
        document = p.read_document(scene)
        second = document.features[-1].id
        assert document.get_feature(second).input_id == fillet
        assert p.runtime(scene).report.features[-1].state == n.EvaluationState.UP_TO_DATE
        complex_volume = p.runtime(scene).evaluator.volume_mm3(document, body)
        assert 0 < complex_volume < expected
        original_features = {item.id for item in document.features}
        original_entities = {entity.id for sketch in document.sketches for entity in document.get_sketch(sketch.id).entities}
        assert bpy.ops.blendercad.duplicate_solid() == {"FINISHED"}
        clone = bpy.context.active_object
        clone_body = clone[p.BODY]
        assert clone_body != body
        document = p.read_document(scene)
        clone_features = [item for item in document.features if item.body_id == clone_body]
        assert len(clone_features) == 3 and [item.type for item in clone_features] == ["extrusion", "fillet", "fillet"]
        clone_ids = {item.id for item in clone_features}
        for feature in clone_features[1:]:
            assert feature.input_id in clone_ids
            for target in feature.edges:
                assert target.body_id == clone_body and target.anchor.feature_id in clone_ids
                assert target.anchor.feature_id not in original_features
                assert all(identifier not in target.anchor.key for identifier in original_entities | original_features), (
                    "Duplicated inline provenance retained a source-body UUID")
        assert all(item.state == n.EvaluationState.UP_TO_DATE for item in p.runtime(scene).report.features)
        close(p.runtime(scene).evaluator.volume_mm3(document, clone_body), complex_volume)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), complex_volume)
        assert len(document.references) == 1 and document.references[0].target.body_id == body
        clone_first, clone_second = clone_features[1].id, clone_features[2].id
        clone_radius = clone_features[2].radius
        snapshot = scene.blendercad_document_snapshot
        filepath = str(Path(directory) / "fillets.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        bpy.ops.wm.open_mainfile(filepath=filepath)
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert scene.blendercad_document_snapshot == snapshot
        close(p.runtime(scene).evaluator.volume_mm3(document, body), complex_volume)
        close(p.runtime(scene).evaluator.volume_mm3(document, clone_body), complex_volume)
        select_owner(scene, clone_body)
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message="Fillet radius baseline")
        assert bpy.ops.blendercad.set_fillet_radius(feature_id=clone_second, radius_mm=0.25, parameter="") == {"FINISHED"}
        bpy.ops.ed.undo_push(message="Fillet radius edit")
        document = p.read_document(scene)
        assert document.get_feature(clone_second).radius == 0.25
        close(p.runtime(scene).evaluator.volume_mm3(document, body), complex_volume)
        assert bpy.ops.ed.undo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert document.get_feature(clone_second).radius == clone_radius
        close(p.runtime(scene).evaluator.volume_mm3(document, clone_body), complex_volume)
        assert bpy.ops.ed.redo() == {"FINISHED"}
        scene = bpy.context.scene
        document = p.read_document(scene)
        assert document.get_feature(clone_second).radius == 0.25
        select_owner(scene, clone_body)
        rejected(bpy.ops.blendercad.delete_feature, feature_id=clone_first, cascade=False)
        assert bpy.ops.blendercad.delete_feature(feature_id=clone_first, cascade=True) == {"FINISHED"}
        document = p.read_document(scene)
        assert len([item for item in document.features if item.body_id == clone_body]) == 1
        close(p.runtime(scene).evaluator.volume_mm3(document, clone_body), 3000)
        close(p.runtime(scene).evaluator.volume_mm3(document, body), complex_volume)
        print("Blender fillets: persistent edge picks, radius parameters, stale guards, history/rollback/repair, generated-edge duplication, STEP, save/reopen and undo/redo passed")
    finally:
        addon.unregister()
