# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless regression tests for UI modularization and error/edge hardening.

Run via tools/run_test.sh tests/test_hardening.py.
Pass criteria: exits 0 and prints RIGFORGE_HARDENING_OK.
"""

import os
import sys
import tempfile

import bpy

TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"
)
if TOOLS_DIR not in sys.path:
    sys.path.append(TOOLS_DIR)


def check(cond, msg="check failed"):
    if not cond:
        raise AssertionError(msg)


def expect_raises(exc_types, fn, msg=""):
    try:
        fn()
    except exc_types:
        return
    raise AssertionError(msg or f"expected {exc_types} from {fn}")


def fresh_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.actions):
        for item in list(coll):
            if item.users == 0:
                coll.remove(item)


def make_mesh(name, verts, faces=()):
    mesh_data = bpy.data.meshes.new(name + "_Data")
    mesh_data.from_pydata(verts, [], faces)
    mesh_data.update()
    obj = bpy.data.objects.new(name, mesh_data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def make_armature(name):
    arm_data = bpy.data.armatures.new(name + "_Data")
    obj = bpy.data.objects.new(name, arm_data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def test_ui_modularization():
    import rigforge
    from rigforge import ui, ui_game

    panels = (
        "DATA_PT_rigforge_game_export",
        "DATA_PT_rigforge_diagnose",
        "DATA_PT_rigforge_voxel_weights",
        "DATA_PT_rigforge_nudge",
        "DATA_PT_rigforge_transfer",
        "DATA_PT_rigforge_cleanup",
    )
    for name in panels:
        check(hasattr(ui_game, name), f"missing {name} in rigforge.ui_game")
        check(hasattr(ui, name), f"missing re-export {name} in rigforge.ui")
        check(hasattr(bpy.types, name), f"panel {name} not registered in bpy.types")

    # Disable and re-enable round-trip must succeed cleanly.
    bpy.ops.preferences.addon_disable(module="rigforge")
    check(not hasattr(bpy.types, "DATA_PT_rigforge_game_export"), "panel leaked")
    bpy.ops.preferences.addon_enable(module="rigforge")
    check(hasattr(bpy.types, "DATA_PT_rigforge_game_export"), "panel not restored")
    check("rf_scripts" in rigforge.__file__, "loaded unexpected addon path")


def test_fit_metarig_reexports():
    from rigforge.auto_place import (
        fit_biped,
        fit_common,
        fit_face,
        fit_fingers,
        fit_metarig,
        fit_quadruped,
    )

    for sym in (
        "DRIVERS",
        "STALKER_SPINE",
        "fit_hero",
        "fit_stalker",
        "fit_face",
        "fit_fingers",
        "resolve_targets",
        "check_closure",
        "check_symmetry",
        "run_generate",
        "emit_module",
    ):
        check(hasattr(fit_metarig, sym), f"fit_metarig missing re-export {sym}")

    check(fit_metarig.fit_hero is fit_biped.fit_hero, "fit_hero identity mismatch")
    check(fit_metarig.fit_face is fit_face.fit_face, "fit_face identity mismatch")
    check(
        fit_metarig.fit_fingers is fit_fingers.fit_fingers,
        "fit_fingers identity mismatch",
    )
    check(
        fit_metarig.fit_stalker is fit_quadruped.fit_stalker,
        "fit_stalker identity mismatch",
    )
    check(fit_metarig.fit_line is fit_common.fit_line, "fit_line identity mismatch")
    expect_raises(
        SystemExit,
        lambda: fit_metarig.arg_value(["--out"], "--out"),
        "arg_value must abort cleanly on missing trailing value",
    )


def test_transfer_hardening():
    from rigforge.weights import transfer

    fresh_scene()
    arm = make_armature("Arm")
    verts_only = make_mesh("VertsOnly", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)], [])
    piece = make_mesh(
        "Piece",
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [(0, 1, 2)],
    )
    empty_mesh = make_mesh("EmptyMesh", [], [])

    expect_raises(
        ValueError,
        lambda: transfer.solve_transfer(arm, piece),
        "non-mesh source must fail",
    )
    expect_raises(
        ValueError,
        lambda: transfer.solve_transfer(verts_only, piece),
        "0-triangle source must fail cleanly",
    )
    expect_raises(
        ValueError,
        lambda: transfer.solve_transfer(piece, empty_mesh),
        "0-vertex piece must fail cleanly",
    )
    expect_raises(
        ValueError,
        lambda: transfer.solve_transfer(piece, piece, k=0),
        "k < 1 must fail cleanly",
    )


def test_voxel_hardening():
    from rigforge.weights import voxel

    fresh_scene()
    arm = make_armature("Arm")
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones.new("Bone")
    eb.head = (0.0, 0.0, 0.0)
    eb.tail = (0.0, 0.0, 1.0)
    eb.use_deform = True
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.mesh.primitive_cube_add(size=1.0)
    cube = bpy.context.active_object
    empty_mesh = make_mesh("EmptyVox", [], [])

    expect_raises(
        ValueError,
        lambda: voxel.voxelize(cube, cell=0.0),
        "zero cell size must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: voxel.voxelize(empty_mesh, cell=0.05),
        "empty mesh voxelize must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: voxel.bone_world_segment(arm, "MissingBone"),
        "missing bone must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: voxel.bind_weights(empty_mesh, arm),
        "0-vert target bind_weights must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: voxel.bind_weights(cube, cube),
        "non-armature rig must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: voxel.bind_weights(cube, arm, k=0),
        "k < 1 bind_weights must raise ValueError",
    )


def test_nudge_and_cleanup_hardening():
    from rigforge.operators import nudge as nudge_op
    from rigforge.weights import cleanup, nudge

    fresh_scene()
    empty_mesh = make_mesh("Empty", [], [])
    tri = make_mesh(
        "Tri",
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [(0, 1, 2)],
    )

    expect_raises(
        ValueError,
        lambda: nudge.write_weights(tri, [{}]),
        "row count mismatch must raise ValueError",
    )
    offsets, neighbors = nudge.mesh_adjacency(tri)
    expect_raises(
        ValueError,
        lambda: nudge.solve_nudge([{}, {}, {}], offsets, neighbors, {99: {"B": 1.0}}),
        "out-of-range pin vertex must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: nudge.solve_nudge([{}, {}, {}], offsets, neighbors, {}, k=0),
        "k < 1 solve_nudge must raise ValueError",
    )

    tri[nudge_op.PINS_KEY] = "{not json"
    check(nudge_op.get_pins(tri) == {}, "corrupt pin JSON must return empty dict")
    tri[nudge_op.PINS_KEY] = '[["bad", "B", 1.0], [999, "B", 1.0], [1, "Bone", 0.5]]'
    check(
        nudge_op.get_pins(tri) == {1: {"Bone": 0.5}},
        "valid pin entries must survive malformed neighbors",
    )

    expect_raises(ValueError, lambda: cleanup.limit_rows([], k=0))
    expect_raises(ValueError, lambda: cleanup.prune_rows([], threshold=-0.1))
    expect_raises(ValueError, lambda: cleanup.smooth_rows([], offsets, neighbors, -1))
    expect_raises(ValueError, lambda: cleanup.mirror_rows([], empty_mesh))
    expect_raises(ValueError, lambda: cleanup.mirror_rows([{}], tri))

    bpy.context.view_layer.objects.active = empty_mesh
    expect_raises(
        RuntimeError,
        lambda: bpy.ops.wm.rigforge_nudge(mode="PIN", bone="B"),
        "rigforge_nudge must reject empty mesh",
    )
    empty_mesh.vertex_groups.new(name="B")
    expect_raises(
        RuntimeError,
        lambda: bpy.ops.wm.rigforge_cleanup(mode="LIMIT"),
        "rigforge_cleanup must reject empty mesh",
    )


def test_export_and_diagnose_hardening():
    from rigforge.operators import diagnose, game_export

    fresh_scene()
    tri = make_mesh(
        "MeshObj",
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [(0, 1, 2)],
    )
    errs, _ = game_export.validate_export(tri, [], "FULL")
    check(any(e["code"] == "invalid_rig" for e in errs), "expected invalid_rig error")

    arm = make_armature("GenRig")
    arm.data["rig_id"] = "test"
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm.data.edit_bones.new("DEF-root")
    eb.head = (0.0, 0.0, 0.0)
    eb.tail = (0.0, 0.0, 0.5)
    bpy.ops.object.mode_set(mode="OBJECT")

    empty_mesh = make_mesh("EmptyExport", [], [])
    errs, _ = game_export.validate_export(arm, [empty_mesh], "FULL")
    check(any(e["code"] == "empty_mesh" for e in errs), "expected empty_mesh error")
    check(game_export.merge_weights(None, {"a": "b"}) == 0)

    bpy.context.view_layer.objects.active = arm
    expect_raises(
        RuntimeError,
        lambda: bpy.ops.wm.rigforge_game_export(filepath=""),
        "rigforge_game_export must reject empty filepath",
    )

    # Edit-mode newly added bone lacks pose.bones entry until exiting edit mode;
    # diagnose_metarig must handle that without raising KeyError.
    meta = make_armature("MetaEdit")
    bpy.context.view_layer.objects.active = meta
    bpy.ops.object.mode_set(mode="EDIT")
    root = meta.data.edit_bones.new("root")
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 0.0, 0.3)
    res = diagnose.diagnose_metarig(meta)
    check("errors" in res and "warnings" in res, "diagnose result malformed")
    bpy.ops.object.mode_set(mode="OBJECT")
    expect_raises(ValueError, lambda: diagnose.diagnose_metarig(tri))


def test_auto_place_and_cli_hardening():
    import auto_rig
    import genforge_adapter

    from rigforge.auto_place import detect_landmarks, unirig_hints, validate_fit

    fresh_scene()
    empty_mesh = make_mesh("EmptyDetect", [], [])
    flat_mesh = make_mesh(
        "FlatZ",
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        [(0, 1, 2)],
    )
    expect_raises(
        ValueError,
        lambda: detect_landmarks.envelope_copy(empty_mesh),
        "0-vert envelope_copy must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: detect_landmarks.envelope_copy(flat_mesh),
        "zero-height envelope_copy must raise ValueError",
    )
    # Failed envelope_copy must not leak orphan duplicate objects.
    check(
        set(bpy.data.objects.keys()) == {"EmptyDetect", "FlatZ"},
        f"orphan objects leaked: {list(bpy.data.objects.keys())}",
    )
    expect_raises(
        ValueError,
        lambda: detect_landmarks.slice_signature_axis(flat_mesh, slices=10, axis="z"),
        "zero-span slice must raise ValueError",
    )
    expect_raises(
        ValueError,
        lambda: detect_landmarks.slice_signature_axis(flat_mesh, slices=0, axis="x"),
        "slices < 1 must raise ValueError",
    )
    expect_raises(
        SystemExit,
        lambda: detect_landmarks.import_mesh("/tmp/nonexistent_rigforge_mesh_999.glb"),
    )
    expect_raises(
        SystemExit,
        lambda: validate_fit.build_metarig_from_module(
            "/tmp/nonexistent_rigforge_999.py"
        ),
    )
    expect_raises(
        ValueError,
        lambda: validate_fit.run_gate(empty_mesh, empty_mesh, {}, []),
    )

    with tempfile.TemporaryDirectory() as tmp:
        bad_hints = os.path.join(tmp, "bad.json")
        with open(bad_hints, "w", encoding="utf-8") as f:
            f.write("{not json")
        expect_raises(ValueError, lambda: unirig_hints.load_hints(bad_hints))
        nan_hints = os.path.join(tmp, "nan.json")
        with open(nan_hints, "w", encoding="utf-8") as f:
            f.write(
                '{"format": "skintokens-joints/1", "joints": [{"name": "a", "normalized": [NaN, 0, 0]}]}'
            )
        expect_raises(ValueError, lambda: unirig_hints.load_hints(nan_hints))

    expect_raises(
        auto_rig.StageFail,
        lambda: auto_rig.parse_args(["blender", "--", "mesh.glb", "--out"]),
        "missing flag value must raise StageFail",
    )
    expect_raises(
        genforge_adapter.AdapterError,
        lambda: genforge_adapter.parse(["repair-rig", "a", "b", "c", "--class"]),
        "missing --class value must raise AdapterError",
    )


def main():
    bpy.ops.preferences.addon_enable(module="rigforge")
    test_ui_modularization()
    test_fit_metarig_reexports()
    test_transfer_hardening()
    test_voxel_hardening()
    test_nudge_and_cleanup_hardening()
    test_export_and_diagnose_hardening()
    test_auto_place_and_cli_hardening()
    print("RIGFORGE_HARDENING_OK")


if __name__ == "__main__":
    main()
