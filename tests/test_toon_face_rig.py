# SPDX-FileCopyrightText: 2026 rigforge contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless test: biped_hero face rig on a procedural toon fixture (gamerig-6).

Run (from the repo root):
    tools/run_test.sh tests/test_toon_face_rig.py
    # or on the bpy wheel:
    python tools/run_test_bpy.py tests/test_toon_face_rig.py

Proves biped_hero auto-placement, face rig generation, geodesic voxel weighting,
jaw/eyelid/brow vertex displacement, no-face-rig control baseline, clean FULL +
MOBILE GLB exports (with baked face animations on FULL), optional rfcheck/weights
verification, and side-by-side talk-camera sheet rendering.

Pass criteria: exits 0, writes /tmp/gamerig_6_toon_face_sheet.png, and prints
RIGFORGE_TOON_FACE_OK.
"""

import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys

import bmesh
import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")

FULL_GLB = "/tmp/gamerig_6_toon_face_full.glb"
MOBILE_GLB = "/tmp/gamerig_6_toon_face_mobile.glb"
SHEET_PNG = "/tmp/gamerig_6_toon_face_sheet.png"


def fail(msg):
    print("RIGFORGE_TOON_FACE_FAIL:", msg)
    sys.exit(1)


def check(cond, msg):
    if not cond:
        fail(msg)


def load_fixture_mod():
    path = os.path.join(TOOLS, "toon_face_fixture.py")
    spec = importlib.util.spec_from_file_location("toon_face_fixture", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_glb_json(path):
    with open(path, "rb") as f:
        raw = f.read()
    check(len(raw) >= 20, f"{path}: GLB too small ({len(raw)} bytes)")
    magic, version, total_len = struct.unpack("<4sII", raw[:12])
    check(
        magic == b"glTF" and version == 2, f"{path}: bad GLB header {magic}/{version}"
    )
    check(
        total_len == len(raw),
        f"{path}: GLB length mismatch header={total_len} actual={len(raw)}",
    )
    json_len, chunk_type = struct.unpack("<I4s", raw[12:20])
    check(chunk_type == b"JSON", f"{path}: first chunk not JSON ({chunk_type!r})")
    return json.loads(raw[20 : 20 + json_len])


def find_bin(env_name, exe_name, fallback_rel):
    for cand in (
        os.environ.get(env_name),
        shutil.which(exe_name),
        os.path.expanduser(fallback_rel),
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def duplicate_mesh_clean(src_obj, new_name):
    dup = src_obj.copy()
    dup.data = src_obj.data.copy()
    dup.name = new_name
    dup.modifiers.clear()
    dup.vertex_groups.clear()
    dup.parent = None
    bpy.context.scene.collection.objects.link(dup)
    return dup


def region_weight_coverage(mesh_obj, vert_indices, bone_prefixes, min_w=0.05):
    """Count region verts with total weight > min_w on matching DEF bones."""
    groups = mesh_obj.vertex_groups
    matching_ids = {
        g.index for g in groups if any(g.name.startswith(p) for p in bone_prefixes)
    }
    if not matching_ids:
        return 0, 0.0
    covered = 0
    max_total = 0.0
    verts = mesh_obj.data.vertices
    for vi in vert_indices:
        total = sum(el.weight for el in verts[vi].groups if el.group in matching_ids)
        if total > min_w:
            covered += 1
        if total > max_total:
            max_total = total
    return covered, max_total


def bone_world_point(arm_obj, bone_name, which="head"):
    b = arm_obj.data.bones.get(bone_name)
    if b is None:
        return None
    local = b.head_local if which == "head" else b.tail_local
    return arm_obj.matrix_world @ local


def main():
    bpy.ops.preferences.addon_enable(module="rigforge")
    import rigforge

    assert "rf_scripts" in rigforge.__file__, "loaded wrong copy: " + rigforge.__file__

    fx = load_fixture_mod()

    # 1. Build procedural toon-proportioned fixture (~0.27 head ratio)
    mesh, spec = fx.build_toon_fixture()
    verts_world = [mesh.matrix_world @ v.co for v in mesh.data.vertices]
    zmin = min(p.z for p in verts_world)
    zmax = max(p.z for p in verts_world)
    height = zmax - zmin
    check(1.65 <= height <= 1.75, f"unexpected fixture height {height:.3f}")
    check(
        0.25 <= spec["head_ratio"] <= 0.29,
        f"unexpected head_ratio {spec['head_ratio']:.3f}",
    )

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh.data)
        non_manifold = [e for e in bm.edges if not e.is_manifold]
    finally:
        bm.free()
    check(
        not non_manifold,
        f"toon fixture not watertight manifold: {len(non_manifold)} non-manifold edges",
    )

    regions = fx.face_vertex_regions(mesh)
    for rname, idxs in regions.items():
        check(len(idxs) >= 15, f"face region {rname} too sparse: {len(idxs)} verts")
    print(
        f"fixture OK: verts={len(mesh.data.vertices)} height={height:.3f} m "
        f"head_ratio={spec['head_ratio']:.3f} regions={ {k: len(v) for k, v in regions.items()} }"
    )

    # Keep a clean unrigged copy for the no-face-rig control comparison
    ctrl_mesh = duplicate_mesh_clean(mesh, "ToonControlMesh")
    ctrl_mesh.hide_render = True
    ctrl_mesh.hide_viewport = True

    # 2. Add biped_hero metarig and auto-place onto the toon fixture
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.rigforge_biped_hero_metarig_add()
    meta = bpy.context.view_layer.objects.active
    meta.name = "ToonHeroMetarig"

    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    meta.select_set(True)
    bpy.context.view_layer.objects.active = meta
    res = bpy.ops.wm.rigforge_auto_place()
    check("FINISHED" in res, f"auto_place failed on toon fixture: {res}")

    # Verify face metarig placement matches stylized toon head proportions (~0.27 ratio)
    face_bones = [
        b
        for b in meta.data.bones
        if any(
            b.name.startswith(p)
            for p in (
                "jaw",
                "chin",
                "lip.",
                "lid.",
                "brow.",
                "eye.",
                "forehead.",
                "nose",
            )
        )
    ]
    face_zs = [
        (meta.matrix_world @ pt).z
        for b in face_bones
        for pt in (b.head_local, b.tail_local)
    ]
    face_span = max(face_zs) - min(face_zs)
    head_height = spec["head_max_z"] - spec["head_min_z"]
    print(
        f"face metarig span={face_span:.3f} m (head_height={head_height:.3f} m, "
        f"ratio={face_span / head_height:.2f})"
    )
    check(
        face_span >= 0.55 * head_height,
        f"face metarig too small for toon head: span={face_span:.3f} m vs head={head_height:.3f} m",
    )

    placement_checks = [
        ("jaw", "tail", spec["chin"], 0.065, "jaw chin"),
        ("eye.L", "head", spec["eye_l"], 0.050, "eye.L"),
        ("eye.R", "head", spec["eye_r"], 0.050, "eye.R"),
        ("lid.T.L.002", "head", spec["lid_t_l"], 0.045, "lid.T.L.002"),
        ("lid.B.L.002", "head", spec["lid_b_l"], 0.045, "lid.B.L.002"),
        ("brow.T.L.002", "head", spec["brow_l"], 0.045, "brow.T.L.002"),
    ]
    for bname, which, target, tol, label in placement_checks:
        pt = bone_world_point(meta, bname, which)
        check(pt is not None, f"missing metarig bone {bname}")
        err = (pt - target).length
        print(
            f"  place {label}: got={[round(v, 3) for v in pt]} "
            f"target={[round(v, 3) for v in target]} err={err:.3f} m"
        )
        check(err <= tol, f"metarig {label} misplaced by {err:.3f} m (> {tol:.3f} m)")

    for bname in ("breast.L", "breast.R"):
        bpt = bone_world_point(meta, bname, "head")
        check(bpt is not None, f"missing metarig bone {bname}")
        check(
            bpt.z < spec["neck_z"],
            f"torso bone {bname} drifted above neck ({bpt.z:.3f} >= {spec['neck_z']:.3f})",
        )
    fh_pt = bone_world_point(meta, "forehead.L.001", "head")
    br_pt = bone_world_point(meta, "brow.T.L.002", "head")
    check(
        fh_pt is not None and br_pt is not None, "missing forehead/brow metarig bones"
    )
    check(
        fh_pt.z - br_pt.z >= 0.050,
        f"forehead.L.001 too close to brow ({fh_pt.z - br_pt.z:.3f} m < 0.050 m)",
    )

    # Snapshot fitted metarig bone transforms before generate so we can build
    # the no-face-rig control from the exact same body fit.
    ctrl_meta = meta.copy()
    ctrl_meta.data = meta.data.copy()
    ctrl_meta.name = "ToonNoFaceMetarig"
    bpy.context.scene.collection.objects.link(ctrl_meta)

    # 3. Generate full biped_hero rig and bind weights with voxel auto-weights
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    meta.select_set(True)
    bpy.context.view_layer.objects.active = meta
    bpy.ops.pose.rigforge_generate()
    rig = next(
        (
            o
            for o in bpy.data.objects
            if o.type == "ARMATURE"
            and "rig_id" in o.data
            and o == meta.data.rigforge_target_rig
        ),
        None,
    )
    check(rig is not None, "biped_hero generate produced no rig")
    rig.name = "ToonHeroRig"

    required_ctrls = (
        "jaw_master",
        "lid.T.L.002",
        "lid.T.R.002",
        "lid.B.L.002",
        "lid.B.R.002",
        "brow.T.L.001",
        "brow.T.L.002",
        "brow.T.L.003",
        "brow.T.R.001",
        "brow.T.R.002",
        "brow.T.R.003",
        "eyes",
        "eye.L",
        "eye.R",
    )
    for cname in required_ctrls:
        check(cname in rig.pose.bones, f"missing face control {cname}")

    required_defs = (
        "DEF-jaw",
        "DEF-chin",
        "DEF-chin.001",
        "DEF-lid.T.L.001",
        "DEF-lid.T.L.002",
        "DEF-lid.B.L.001",
        "DEF-lid.B.L.002",
        "DEF-brow.T.L.001",
        "DEF-brow.T.L.002",
        "DEF-brow.B.L.001",
        "DEF-brow.B.L.002",
    )
    for dname in required_defs:
        check(dname in rig.data.bones, f"missing face DEF bone {dname}")

    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    bind_res = bpy.ops.wm.rigforge_voxel_weights(cell_size=0.008)
    check("FINISHED" in bind_res, f"voxel weights failed: {bind_res}")

    bare = [v.index for v in mesh.data.vertices if not v.groups]
    check(not bare, f"mesh has {len(bare)} unweighted verts after voxel bind")

    # Verify non-zero weight coverage on jaw, upper lids, lower lids, and brows
    jaw_cov, jaw_max = region_weight_coverage(
        mesh,
        regions["jaw"],
        ("DEF-jaw", "DEF-chin", "DEF-lip.B"),
    )
    lid_tl_cov, lid_tl_max = region_weight_coverage(
        mesh, regions["lid_t_l"], ("DEF-lid.T.L",)
    )
    lid_tr_cov, lid_tr_max = region_weight_coverage(
        mesh, regions["lid_t_r"], ("DEF-lid.T.R",)
    )
    lid_bl_cov, lid_bl_max = region_weight_coverage(
        mesh, regions["lid_b_l"], ("DEF-lid.B.L",)
    )
    lid_br_cov, lid_br_max = region_weight_coverage(
        mesh, regions["lid_b_r"], ("DEF-lid.B.R",)
    )
    brow_l_cov, brow_l_max = region_weight_coverage(
        mesh, regions["brow_l"], ("DEF-brow.T.L", "DEF-brow.B.L")
    )
    brow_r_cov, brow_r_max = region_weight_coverage(
        mesh, regions["brow_r"], ("DEF-brow.T.R", "DEF-brow.B.R")
    )
    print(
        f"weight coverage (>0.05): jaw={jaw_cov}/{len(regions['jaw'])} (max={jaw_max:.2f}), "
        f"lidT.L={lid_tl_cov}/{len(regions['lid_t_l'])} (max={lid_tl_max:.2f}), "
        f"lidT.R={lid_tr_cov}/{len(regions['lid_t_r'])} (max={lid_tr_max:.2f}), "
        f"lidB.L={lid_bl_cov}/{len(regions['lid_b_l'])} (max={lid_bl_max:.2f}), "
        f"lidB.R={lid_br_cov}/{len(regions['lid_b_r'])} (max={lid_br_max:.2f}), "
        f"brow.L={brow_l_cov}/{len(regions['brow_l'])} (max={brow_l_max:.2f}), "
        f"brow.R={brow_r_cov}/{len(regions['brow_r'])} (max={brow_r_max:.2f})"
    )
    check(jaw_cov >= 30, f"insufficient jaw weight coverage: {jaw_cov}")
    check(
        lid_tl_cov >= 12 and lid_tr_cov >= 12,
        f"insufficient upper lid weight coverage: L={lid_tl_cov} R={lid_tr_cov}",
    )
    check(
        lid_bl_cov >= 10 and lid_br_cov >= 10,
        f"insufficient lower lid weight coverage: L={lid_bl_cov} R={lid_br_cov}",
    )
    check(
        brow_l_cov >= 15 and brow_r_cov >= 15,
        f"insufficient brow weight coverage: L={brow_l_cov} R={brow_r_cov}",
    )
    neck_breast_cov, neck_breast_max = region_weight_coverage(
        mesh, regions["neck"], ("DEF-breast.",), min_w=0.01
    )
    jaw_breast_cov, jaw_breast_max = region_weight_coverage(
        mesh, regions["jaw"], ("DEF-breast.",), min_w=0.01
    )
    check(
        neck_breast_max < 0.25 and jaw_breast_cov == 0,
        f"breast bones leaked into neck/jaw (neck={neck_breast_cov} max={neck_breast_max:.3f}, "
        f"jaw={jaw_breast_cov} max={jaw_breast_max:.3f})",
    )
    neck_jaw_cov, neck_jaw_max = region_weight_coverage(
        mesh, regions["neck"], ("DEF-jaw", "DEF-chin"), min_w=0.05
    )
    print(
        f"neck isolation: jaw/chin>0.05={neck_jaw_cov}/{len(regions['neck'])} "
        f"(max={neck_jaw_max:.3f}), breast_max={neck_breast_max:.3f}"
    )

    # 4. Evaluate vertex displacements under each expression
    fx.apply_expression(rig, "rest")
    rest_pos = fx.evaluated_world_positions(mesh)

    fx.apply_expression(rig, "jaw_open")
    jaw_pos = fx.evaluated_world_positions(mesh)
    jaw_dz = jaw_pos[regions["jaw"], 2] - rest_pos[regions["jaw"], 2]
    jaw_disp = np.linalg.norm(
        jaw_pos[regions["jaw"]] - rest_pos[regions["jaw"]], axis=1
    )
    crown_disp = np.linalg.norm(
        jaw_pos[regions["crown"]] - rest_pos[regions["crown"]], axis=1
    )
    neck_disp = np.linalg.norm(
        jaw_pos[regions["neck"]] - rest_pos[regions["neck"]], axis=1
    )
    print(
        f"jaw_open: mean_dz={jaw_dz.mean():.4f} m min_dz={jaw_dz.min():.4f} m "
        f"max_disp={jaw_disp.max():.4f} m neck_max={neck_disp.max():.4f} m "
        f"crown_max={crown_disp.max():.4f} m"
    )
    check(
        jaw_dz.mean() < -0.012,
        f"jaw did not open downward: mean_dz={jaw_dz.mean():.4f}",
    )
    check(
        jaw_disp.max() > 0.025, f"jaw max displacement too small: {jaw_disp.max():.4f}"
    )
    check(
        crown_disp.max() < 0.005, f"crown bled during jaw open: {crown_disp.max():.4f}"
    )
    check(
        neck_disp.max() < 0.032,
        f"neck displaced too far during jaw open: {neck_disp.max():.4f}",
    )

    for rname in ("lid_t_l", "lid_b_l", "brow_l"):
        sums = {}
        for vi in regions[rname]:
            for el in mesh.data.vertices[vi].groups:
                gn = mesh.vertex_groups[el.group].name
                sums[gn] = sums.get(gn, 0.0) + el.weight
        top = sorted(
            ((w / len(regions[rname]), k) for k, w in sums.items()), reverse=True
        )[:6]
        print(f"top weights {rname}: {[(k, round(w, 3)) for w, k in top]}")

    fx.apply_expression(rig, "rest")
    rest_pb = {
        b: (rig.pose.bones[b].head.copy(), rig.pose.bones[b].tail.copy())
        for b in (
            "lid.T.L.002",
            "MCH-lid.T.L.002",
            "DEF-lid.T.L.001",
            "DEF-lid.T.L.002",
        )
    }

    fx.apply_expression(rig, "blink")
    for b, (h0, t0) in rest_pb.items():
        pb = rig.pose.bones[b]
        dh = [round(v, 4) for v in (pb.head - h0)]
        dt = [round(v, 4) for v in (pb.tail - t0)]
        print(f"  blink pose {b}: dhead={dh} dtail={dt}")
    blink_pos = fx.evaluated_world_positions(mesh)
    blink_errors = []
    for side in ("l", "r"):
        ut_dz = (
            blink_pos[regions[f"lid_t_{side}"], 2]
            - rest_pos[regions[f"lid_t_{side}"], 2]
        )
        lb_dz = (
            blink_pos[regions[f"lid_b_{side}"], 2]
            - rest_pos[regions[f"lid_b_{side}"], 2]
        )
        gap_rest = (
            rest_pos[regions[f"lid_t_{side}"], 2].mean()
            - rest_pos[regions[f"lid_b_{side}"], 2].mean()
        )
        gap_blink = (
            blink_pos[regions[f"lid_t_{side}"], 2].mean()
            - blink_pos[regions[f"lid_b_{side}"], 2].mean()
        )
        print(
            f"blink.{side.upper()}: upper_mean_dz={ut_dz.mean():.4f} lower_mean_dz={lb_dz.mean():.4f} "
            f"gap {gap_rest:.4f} -> {gap_blink:.4f} m"
        )
        if not (ut_dz.mean() < -0.007):
            blink_errors.append(f"upper lid.{side} did not drop: {ut_dz.mean():.4f}")
        if not (lb_dz.mean() > 0.002):
            blink_errors.append(f"lower lid.{side} did not lift: {lb_dz.mean():.4f}")
        if not (gap_blink < gap_rest - 0.012):
            blink_errors.append(
                f"eye.{side} did not close enough: {gap_rest:.4f} -> {gap_blink:.4f}"
            )

    fx.apply_expression(rig, "brow_raise")
    brow_pos = fx.evaluated_world_positions(mesh)
    for side in ("l", "r"):
        b_dz = (
            brow_pos[regions[f"brow_{side}"], 2] - rest_pos[regions[f"brow_{side}"], 2]
        )
        print(
            f"brow_raise.{side.upper()}: mean_dz={b_dz.mean():.4f} max_dz={b_dz.max():.4f} m"
        )
        if not (b_dz.mean() > 0.007):
            blink_errors.append(f"brow.{side} did not lift: {b_dz.mean():.4f}")
        if not (b_dz.max() > 0.013):
            blink_errors.append(f"brow.{side} peak lift too small: {b_dz.max():.4f}")
    if blink_errors:
        fail("; ".join(blink_errors))
    jaw_bleed = np.linalg.norm(
        brow_pos[regions["jaw"]] - rest_pos[regions["jaw"]], axis=1
    ).max()
    check(jaw_bleed < 0.004, f"jaw bled during brow raise: {jaw_bleed:.4f}")
    crown_brow_bleed = np.linalg.norm(
        brow_pos[regions["crown"]] - rest_pos[regions["crown"]], axis=1
    ).max()
    print(f"brow_raise crown_max={crown_brow_bleed:.4f} m")
    check(
        crown_brow_bleed < 0.015,
        f"crown tented during brow raise: {crown_brow_bleed:.4f}",
    )
    pb_brow_l2 = rig.pose.bones["DEF-brow.T.L.002"]
    pb_fh_l1 = rig.pose.bones["DEF-forehead.L.001"]
    check(
        (rig.matrix_world @ pb_brow_l2.head).z
        < (rig.matrix_world @ pb_fh_l1.head).z - 0.015,
        "brow.T.L.002 approached/overshot forehead.L.001 anchor",
    )

    # 5. Control rig without face bones (rigid head only)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = ctrl_meta
    bpy.ops.object.mode_set(mode="EDIT")
    face_root = ctrl_meta.data.edit_bones.get("face")
    if face_root is not None:
        to_remove = [face_root.name]
        stack = [face_root]
        while stack:
            cur = stack.pop()
            for ch in cur.children:
                to_remove.append(ch.name)
                stack.append(ch)
        for bname in to_remove:
            eb = ctrl_meta.data.edit_bones.get(bname)
            if eb is not None:
                ctrl_meta.data.edit_bones.remove(eb)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    ctrl_meta.select_set(True)
    bpy.context.view_layer.objects.active = ctrl_meta
    bpy.ops.pose.rigforge_generate()
    ctrl_rig = ctrl_meta.data.rigforge_target_rig
    check(ctrl_rig is not None, "no-face control generate produced no rig")
    ctrl_rig.name = "ToonNoFaceRig"
    for absent in ("jaw_master", "lid.T.L.002", "brow.T.L.002"):
        check(absent not in ctrl_rig.pose.bones, f"control rig leaked {absent}")
    for absent_def in ("DEF-jaw", "DEF-lid.T.L.002", "DEF-brow.T.L.002"):
        check(absent_def not in ctrl_rig.data.bones, f"control rig leaked {absent_def}")

    ctrl_mesh.hide_viewport = False
    ctrl_mesh.hide_render = False
    bpy.ops.object.select_all(action="DESELECT")
    ctrl_mesh.select_set(True)
    ctrl_rig.select_set(True)
    bpy.context.view_layer.objects.active = ctrl_mesh
    ctrl_bind = bpy.ops.wm.rigforge_voxel_weights(cell_size=0.008)
    check("FINISHED" in ctrl_bind, f"control voxel weights failed: {ctrl_bind}")
    ctrl_rest = fx.evaluated_world_positions(ctrl_mesh)
    ctrl_diff = float(np.linalg.norm(ctrl_rest - rest_pos, axis=1).max())
    check(ctrl_diff < 1e-5, f"control rest differs from full-face rest: {ctrl_diff}")

    # 6. Keyframe face animation + GLB export (FULL and MOBILE)
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 30
    action = bpy.data.actions.new("ToonFaceExpr")
    rig.animation_data_create()
    rig.animation_data.action = action
    key_bones = (
        "jaw_master",
        "lid.T.L.002",
        "lid.T.R.002",
        "lid.B.L.002",
        "lid.B.R.002",
        "brow.T.L.001",
        "brow.T.L.002",
        "brow.T.L.003",
        "brow.T.R.001",
        "brow.T.R.002",
        "brow.T.R.003",
    )
    for frame, expr in (
        (1, "rest"),
        (10, "jaw_open"),
        (20, "blink"),
        (30, "brow_raise"),
    ):
        scene.frame_set(frame)
        fx.apply_expression(rig, expr)
        for kb in key_bones:
            pb = rig.pose.bones[kb]
            pb.keyframe_insert("location", frame=frame)
            pb.keyframe_insert("rotation_quaternion", frame=frame)
    scene.frame_set(1)
    fx.reset_face_pose(rig)

    ctrl_mesh.hide_render = True
    ctrl_mesh.hide_viewport = True
    ctrl_rig.hide_viewport = True
    for p in (FULL_GLB, MOBILE_GLB):
        if os.path.exists(p):
            os.remove(p)

    # Export FULL GLB (face rig enabled + face animations)
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    exp_full = bpy.ops.wm.rigforge_game_export(
        filepath=FULL_GLB, export_animations=True, profile="FULL"
    )
    check("FINISHED" in exp_full, f"FULL export failed: {exp_full}")
    gltf_full = read_glb_json(FULL_GLB)
    full_joints = [
        gltf_full["nodes"][j].get("name")
        for skin in gltf_full.get("skins", [])
        for j in skin["joints"]
    ]
    non_def = [j for j in full_joints if not (j or "").startswith("DEF-")]
    check(full_joints and not non_def, f"non-DEF joints in FULL GLB: {non_def[:5]}")
    for dname in required_defs:
        check(dname in full_joints, f"FULL GLB missing {dname}")
    anim_targets = {
        gltf_full["nodes"][ch["target"]["node"]].get("name")
        for clip in gltf_full.get("animations", [])
        for ch in clip["channels"]
    }
    check(anim_targets, "FULL GLB has no animation channels")
    for expected_target in ("DEF-jaw", "DEF-lid.T.L.002", "DEF-brow.T.L.002"):
        check(
            expected_target in anim_targets,
            f"FULL GLB animation missing target {expected_target}",
        )
    print(
        f"FULL GLB OK: {len(full_joints)} DEF joints, {len(anim_targets)} animated nodes"
    )

    # Export MOBILE GLB (must still merge face bones up and keep ~65 joints)
    exp_mob = bpy.ops.wm.rigforge_game_export(
        filepath=MOBILE_GLB, export_animations=True, profile="MOBILE"
    )
    check("FINISHED" in exp_mob, f"MOBILE export failed: {exp_mob}")
    gltf_mob = read_glb_json(MOBILE_GLB)
    mob_joints = [
        gltf_mob["nodes"][j].get("name")
        for skin in gltf_mob.get("skins", [])
        for j in skin["joints"]
    ]
    leaked_face = [
        j
        for j in mob_joints
        if any(j.startswith(p) for p in ("DEF-jaw", "DEF-lid", "DEF-brow", "DEF-lip"))
    ]
    check(not leaked_face, f"MOBILE GLB leaked face bones: {leaked_face[:5]}")
    check(
        len(mob_joints) < len(full_joints),
        f"MOBILE GLB did not reduce joints ({len(mob_joints)} vs {len(full_joints)})",
    )
    print(f"MOBILE GLB OK: {len(mob_joints)} kept joints (face merged up)")

    # Optional external checks (rfcheck and meshweights weights check)
    sibling_dir = os.path.dirname(ROOT)
    rfcheck = find_bin(
        "RFCHECK",
        "rfcheck",
        os.path.join(sibling_dir, "rfcheck", "target", "release", "rfcheck"),
    )
    if rfcheck:
        proc = subprocess.run(
            [rfcheck, "--class", "hero", FULL_GLB],
            capture_output=True,
            text=True,
            timeout=120,
        )
        print(f"rfcheck ({rfcheck}) rc={proc.returncode}: {proc.stdout.strip()[:300]}")
        check(proc.returncode == 0, f"rfcheck failed: {proc.stdout} {proc.stderr}")
    else:
        print("rfcheck: skipped (binary not present in environment)")

    weights_bin = find_bin(
        "WEIGHTS_BIN",
        "weights",
        os.path.join(
            sibling_dir, "meshweights", "rust", "target", "release", "weights"
        ),
    )
    if weights_bin:
        proc = subprocess.run(
            [weights_bin, "check", FULL_GLB, "--json"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        # weights check exits 0 (clean) or 1 (faults found), 2 on usage/IO error
        check(proc.returncode in (0, 1), f"weights check crashed: {proc.stderr}")
        wreport = json.loads(proc.stdout)
        findings = wreport.get("findings", [])
        face_findings = [
            f
            for f in findings
            if any(
                tok in json.dumps(f).lower()
                for tok in (
                    "jaw",
                    "lid",
                    "brow",
                    "face",
                    "head",
                    "chin",
                    "lip",
                    "cheek",
                    "nose",
                    "temple",
                )
            )
        ]
        breast_findings = [
            f for f in findings if "breast" in f.get("region", "").lower()
        ]
        print(
            f"weights check: rc={proc.returncode} score={wreport.get('score')} "
            f"findings={len(findings)} face_findings={len(face_findings)} "
            f"breast_findings={len(breast_findings)}"
        )
    else:
        print("weights check: skipped (weights binary not present in environment)")

    # 7. Render side-by-side comparison PNG at the talk camera
    if os.path.exists(SHEET_PNG):
        os.remove(SHEET_PNG)

    # Clear animation action before posing static comparison panels
    rig.animation_data.action = None

    def show_control():
        mesh.hide_render = True
        ctrl_mesh.hide_render = False
        fx.reset_face_pose(ctrl_rig)

    def show_face(expr):
        def _fn():
            ctrl_mesh.hide_render = True
            mesh.hide_render = False
            fx.apply_expression(rig, expr)

        return _fn

    panels = [
        ("CONTROL (NO FACE)", show_control),
        ("FACE: JAW OPEN", show_face("jaw_open")),
        ("FACE: BLINK", show_face("blink")),
        ("FACE: BROW RAISE", show_face("brow_raise")),
    ]
    fx.render_comparison_sheet(panels, SHEET_PNG, panel_w=420, panel_h=520)
    check(
        os.path.exists(SHEET_PNG) and os.path.getsize(SHEET_PNG) > 10_000,
        f"comparison sheet missing or tiny: {SHEET_PNG}",
    )

    # Verify visual pixels differ between Control panel and each Posed panel
    img = bpy.data.images.load(SHEET_PNG, check_existing=False)
    try:
        w, h = img.size
        arr = np.asarray(img.pixels[:], dtype=np.float32).reshape((h, w, 4))
        arr = np.flipud(arr)
        panel_w, gap, header_h = 420, 4, 38
        body = arr[header_h : header_h + 520]
        p0 = body[:, 0:panel_w, :3]
        for idx, name in ((1, "jaw_open"), (2, "blink"), (3, "brow_raise")):
            x0 = idx * (panel_w + gap)
            pi = body[:, x0 : x0 + panel_w, :3]
            mean_diff = float(np.abs(pi - p0).mean())
            print(f"sheet diff ({name} vs control): mean={mean_diff:.5f}")
            check(
                mean_diff > 0.0005,
                f"rendered panel {name} looks identical to control ({mean_diff:.6f})",
            )
    finally:
        bpy.data.images.remove(img)

    print(f"sheet OK: {SHEET_PNG} ({os.path.getsize(SHEET_PNG)} bytes)")
    print("RIGFORGE_TOON_FACE_OK")


if __name__ == "__main__":
    main()
