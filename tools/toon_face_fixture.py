# SPDX-FileCopyrightText: 2026 rigforge contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic procedural toon-proportioned head + body fixture.

Builds a 1.70 m stylized toon biped with oversized toon proportions (~0.27 head
ratio, chunky upper torso, and clean facial loop geometry for eyelids, brows,
mouth cleft, nose, and jaw/chin) plus helpers to pose expressions, query
ground-truth face feature locations/vertex regions, and render side-by-side
talk-camera comparison sheets.

Used by tests/test_toon_face_rig.py and gates/gamerig-6/gate.sh; works both in
headless Blender and under the bpy pip wheel.
"""

import math
import os
from itertools import pairwise

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

TOTAL_HEIGHT = 1.70
HEAD_MIN_Z = 1.24
HEAD_MAX_Z = 1.70
HEAD_CENTER = Vector((0.0, -0.01, 1.47))
HEAD_RADII = Vector((0.205, 0.195, 0.230))
NECK_Z = 1.20

# Ground-truth facial feature centers (metres, world space, facing -Y)
FACE_SPEC = {
    "height": TOTAL_HEIGHT,
    "head_ratio": (HEAD_MAX_Z - HEAD_MIN_Z) / TOTAL_HEIGHT,
    "neck_z": NECK_Z,
    "head_min_z": HEAD_MIN_Z,
    "head_max_z": HEAD_MAX_Z,
    "chin": Vector((0.0, -0.155, 1.265)),
    "jaw_hinge_l": Vector((0.125, -0.055, 1.410)),
    "mouth_center": Vector((0.0, -0.198, 1.345)),
    "lip_t": Vector((0.0, -0.208, 1.366)),
    "lip_b": Vector((0.0, -0.205, 1.322)),
    "nose_tip": Vector((0.0, -0.236, 1.425)),
    "eye_l": Vector((0.082, -0.158, 1.486)),
    "eye_r": Vector((-0.082, -0.158, 1.486)),
    "lid_t_l": Vector((0.082, -0.200, 1.514)),
    "lid_t_r": Vector((-0.082, -0.200, 1.514)),
    "lid_b_l": Vector((0.082, -0.197, 1.454)),
    "lid_b_r": Vector((-0.082, -0.197, 1.454)),
    "brow_l": Vector((0.084, -0.198, 1.568)),
    "brow_r": Vector((-0.084, -0.198, 1.568)),
    "crown": Vector((0.0, -0.01, HEAD_MAX_Z)),
}

# Talk camera framing (1.3 m, 38 deg vertical FOV, 24 deg yaw off front -Y,
# aiming at upper-torso/face).
TALK_CAM_DISTANCE_M = 1.30
TALK_CAM_FOV_DEG = 38.0
TALK_CAM_YAW_DEG = 24.0
TALK_CAM_PITCH_DEG = 4.4
TALK_CAM_TARGET = Vector((0.0, -0.04, 1.37))

MAT_SPECS = (
    ("Skin", (0.87, 0.65, 0.52, 1.0)),
    ("Sclera", (0.96, 0.96, 0.94, 1.0)),
    ("Iris", (0.08, 0.11, 0.19, 1.0)),
    ("EyelidRim", (0.20, 0.12, 0.10, 1.0)),
    ("Brow", (0.14, 0.09, 0.07, 1.0)),
    ("MouthInterior", (0.16, 0.05, 0.06, 1.0)),
    ("Tunic", (0.84, 0.39, 0.19, 1.0)),
    ("Trousers", (0.20, 0.25, 0.36, 1.0)),
)


def clean_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (
        bpy.data.meshes,
        bpy.data.armatures,
        bpy.data.actions,
        bpy.data.materials,
        bpy.data.cameras,
        bpy.data.images,
    ):
        for item in list(coll):
            coll.remove(item)


def _ensure_materials(mesh_obj):
    mat_idx = {}
    for name, rgba in MAT_SPECS:
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = bpy.data.materials.new(name)
        mat.diffuse_color = rgba
        mat_idx[name] = len(mesh_obj.data.materials)
        mesh_obj.data.materials.append(mat)
    return mat_idx


def _smoothstep(edge0, edge1, x):
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _sculpt_head_point(theta, z):
    """Map (azimuth theta, height z) on the head+neck+torso shell to 3D pos.

    theta=0 is front (-Y); positive theta moves toward +X (character left).
    """
    cx, cy = 0.0, -0.01
    if z >= HEAD_MIN_Z:
        # Normalized height inside head [-1, 1]
        vz = max(-1.0, min(1.0, (z - HEAD_CENTER.z) / HEAD_RADII.z))
        r_prof = math.sqrt(max(1e-4, 1.0 - vz * vz))
        rx = HEAD_RADII.x * r_prof
        ry = HEAD_RADII.y * r_prof
        # Smooth blend into neck near bottom of head (1.24 .. 1.29)
        chin_blend = _smoothstep(1.24, 1.295, z)
        # Keep chin forward while neck narrows behind it
        min_rx = 0.082
        rx = min_rx + (rx - min_rx) * chin_blend
        ry = 0.078 + (ry - 0.078) * chin_blend
        x = cx + rx * math.sin(theta)
        y = cy - ry * math.cos(theta)
        # Chin fullness forward on front-bottom (keep z strictly monotonic)
        if math.cos(theta) > 0.2 and 1.24 <= z <= 1.33:
            chin_w = (1.0 - abs(z - 1.275) / 0.055) * max(0.0, math.cos(theta))
            y -= 0.042 * max(0.0, chin_w)
    elif z >= 1.15:
        # Neck cylinder narrowing around NECK_Z=1.20 then flaring to shoulders
        t = (z - 1.15) / (HEAD_MIN_Z - 1.15)
        neck_pinch = 1.0 - 0.14 * math.sin(math.pi * t)
        rx = 0.086 * neck_pinch + 0.115 * (1.0 - _smoothstep(1.15, 1.195, z))
        ry = 0.080 * neck_pinch + 0.062 * (1.0 - _smoothstep(1.15, 1.195, z))
        x = cx + rx * math.sin(theta)
        y = cy * t - ry * math.cos(theta)
    else:
        # Chunky toon torso (z in [0.56, 1.15])
        t = (z - 0.56) / (1.15 - 0.56)
        shoulder_swell = 0.85 + 0.18 * math.sin(math.pi * min(1.0, t * 1.1))
        rx = 0.215 * shoulder_swell
        ry = 0.142 * shoulder_swell
        if z > 1.07:
            blend = _smoothstep(1.07, 1.15, z)
            rx = rx * (1.0 - blend) + 0.195 * blend
            ry = ry * (1.0 - blend) + 0.136 * blend
        x = cx + rx * math.sin(theta)
        y = -ry * math.cos(theta)

    # Apply sculpted facial features on the front hemisphere (-Y)
    if z >= HEAD_MIN_Z - 0.01 and y < cy - 0.02:
        # Mouth cavity + upper/lower lips around z=1.345
        mx = x / 0.072
        mz = (z - 1.345) / 0.016
        mouth_r2 = mx * mx + mz * mz
        if mouth_r2 < 1.0:
            depth = 0.046 * (1.0 - mouth_r2) ** 0.75
            y += depth
        # Upper and lower lip rims
        if abs(x) < 0.085:
            xw = math.cos(0.5 * math.pi * min(1.0, abs(x) / 0.085))
            if 1.352 <= z <= 1.384:
                lw = math.sin(math.pi * (z - 1.352) / 0.032)
                y -= 0.015 * xw * lw
            if 1.304 <= z <= 1.338:
                lw = math.sin(math.pi * (z - 1.304) / 0.034)
                y -= 0.014 * xw * lw

        # Toon nose at (0, -0.236, 1.425)
        nx = x / 0.030
        nz = (z - 1.425) / 0.034
        nr2 = nx * nx + nz * nz
        if nr2 < 1.0:
            y -= 0.036 * math.sqrt(1.0 - nr2)

        # Eyes + recessed socket + eyeball dome + overhanging lids + brows
        for sx in (1.0, -1.0):
            ex = (x - sx * 0.082) / 0.044
            ez = (z - 1.485) / 0.036
            er2 = ex * ex + ez * ez
            if er2 < 1.15:
                # Socket recess + bulging eyeball dome inside opening
                socket_d = 0.014 * _smoothstep(1.15, 0.75, er2)
                y += socket_d
                if er2 < 0.85:
                    y -= 0.018 * math.sqrt(max(0.0, 1.0 - er2 / 0.85))
            # Overhanging upper eyelid hood (z in [1.500, 1.536])
            lx = (x - sx * 0.082) / 0.050
            if abs(lx) < 1.0 and 1.498 <= z <= 1.538:
                lw = math.cos(0.5 * math.pi * lx) * math.sin(
                    math.pi * (z - 1.498) / 0.040
                )
                y -= 0.022 * lw
            # Lower eyelid rim (z in [1.434, 1.466])
            if abs(lx) < 1.0 and 1.434 <= z <= 1.466:
                lw = math.cos(0.5 * math.pi * lx) * math.sin(
                    math.pi * (z - 1.434) / 0.032
                )
                y -= 0.016 * lw
            # Chunky expressive eyebrow ridge (z in [1.552, 1.590])
            bx = (x - sx * 0.084) / 0.052
            if abs(bx) < 1.0 and 1.550 <= z <= 1.592:
                bw = math.cos(0.5 * math.pi * bx) * math.sin(
                    math.pi * (z - 1.550) / 0.042
                )
                y -= 0.020 * bw

    return Vector((x, y, z))


def _angle_grid(n_theta):
    """Non-uniform azimuth angles concentrated on the front (-Y) face."""
    raw = []
    for i in range(n_theta):
        u = (2.0 * i / n_theta) - 1.0  # [-1, 1)
        # Warp toward 0 (front -Y) so facial loops are dense (~5 mm)
        theta = math.pi * (0.62 * u + 0.38 * (u**3))
        raw.append(theta)
    return raw


def _height_rings():
    """Deterministic height rings from pelvis bottom to head crown."""
    zs = []
    # Torso (0.56 .. 1.15)
    for k in range(18):
        zs.append(0.56 + (1.15 - 0.56) * k / 18.0)
    # Neck (1.15 .. 1.24)
    for k in range(6):
        zs.append(1.15 + (1.24 - 1.15) * k / 6.0)
    # Lower jaw & chin to mouth bottom (1.24 .. 1.305)
    for k in range(8):
        zs.append(1.24 + (1.305 - 1.24) * k / 8.0)
    # Face core (mouth, nose, eyes, eyelids, brows: 1.305 .. 1.605) at ~5 mm step
    for k in range(56):
        zs.append(1.305 + (1.605 - 1.305) * k / 56.0)
    # Forehead & crown (1.605 .. 1.696)
    for k in range(10):
        zs.append(1.605 + (1.696 - 1.605) * k / 10.0)
    return zs


def _classify_face_material(center, mat_idx):
    x, y, z = center.x, center.y, center.z
    if z < 1.16:
        return mat_idx["Tunic"]
    if y > -0.05:
        return mat_idx["Skin"]
    # Mouth interior
    mx = x / 0.068
    mz = (z - 1.345) / 0.015
    if mx * mx + mz * mz < 0.92:
        return mat_idx["MouthInterior"]
    # Eyebrows
    for sx in (1.0, -1.0):
        if abs(x - sx * 0.084) < 0.044 and 1.556 <= z <= 1.584:
            return mat_idx["Brow"]
        # Eyelid dark lash border
        if abs(x - sx * 0.082) < 0.044 and 1.500 <= z <= 1.512:
            return mat_idx["EyelidRim"]
        # Eye opening (sclera + iris)
        ex = (x - sx * 0.082) / 0.038
        ez = (z - 1.482) / 0.022
        if ex * ex + ez * ez < 0.95 and z < 1.502 and z > 1.454:
            dx = x - sx * 0.082
            dz = z - 1.482
            if dx * dx + dz * dz <= 0.0175 * 0.0175:
                return mat_idx["Iris"]
            return mat_idx["Sclera"]
    return mat_idx["Skin"]


def _build_limb_tube(name, profile, mat, segs=18):
    """Build a single clean closed lofted mesh tube along [(Vector, rx, ry), ...]."""
    bm = bmesh.new()
    rings = []
    for center, rx, ry in profile:
        ring = []
        # Keep clean horizontal cross-sections
        for i in range(segs):
            ang = 2.0 * math.pi * i / segs
            pt = Vector(
                (center.x + rx * math.cos(ang), center.y + ry * math.sin(ang), center.z)
            )
            ring.append(bm.verts.new(pt))
        rings.append(ring)
    top_c = bm.verts.new(profile[0][0] + Vector((0.0, 0.0, 0.012)))
    bot_c = bm.verts.new(profile[-1][0] - Vector((0.0, 0.0, 0.012)))
    # Profile goes top -> bottom: top ring cap normal +Z, side quads outward, bot cap -Z
    for i in range(segs):
        j = (i + 1) % segs
        bm.faces.new((top_c, rings[0][i], rings[0][j]))
    for r_hi, r_lo in pairwise(rings):
        for i in range(segs):
            j = (i + 1) % segs
            bm.faces.new((r_hi[j], r_hi[i], r_lo[i], r_lo[j]))
    for i in range(segs):
        j = (i + 1) % segs
        bm.faces.new((bot_c, rings[-1][j], rings[-1][i]))
    bm.normal_update()
    data = bpy.data.meshes.new(name + "_Data")
    bm.to_mesh(data)
    bm.free()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def build_toon_fixture(name="ToonCharacterFixture"):
    """Build deterministic stylized toon character mesh; returns (obj, spec)."""
    clean_scene()
    n_theta = 112
    thetas = _angle_grid(n_theta)
    zs = _height_rings()

    bm = bmesh.new()
    rings = []
    for z in zs:
        ring = []
        for theta in thetas:
            pt = _sculpt_head_point(theta, z)
            ring.append(bm.verts.new(pt))
        rings.append(ring)

    bottom_center = bm.verts.new((0.0, 0.0, 0.55))
    top_center = bm.verts.new((0.0, -0.01, HEAD_MAX_Z))

    # Create body mesh object so material slots exist before face assignment
    mesh_data = bpy.data.meshes.new(name + "_Data")
    body = bpy.data.objects.new(name, mesh_data)
    bpy.context.scene.collection.objects.link(body)
    mat_idx = _ensure_materials(body)

    # Bottom cap
    r0 = rings[0]
    for i in range(n_theta):
        j = (i + 1) % n_theta
        f = bm.faces.new((bottom_center, r0[j], r0[i]))
        f.material_index = mat_idx["Tunic"]

    # Quad strips
    for r_lo, r_hi in pairwise(rings):
        for i in range(n_theta):
            j = (i + 1) % n_theta
            f = bm.faces.new((r_lo[i], r_lo[j], r_hi[j], r_hi[i]))
            center = (r_lo[i].co + r_lo[j].co + r_hi[j].co + r_hi[i].co) * 0.25
            f.material_index = _classify_face_material(center, mat_idx)

    # Top crown cap
    r_top = rings[-1]
    for i in range(n_theta):
        j = (i + 1) % n_theta
        f = bm.faces.new((r_top[i], r_top[j], top_center))
        f.material_index = mat_idx["Skin"]

    bm.normal_update()
    bm.to_mesh(mesh_data)
    bm.free()

    # Build A-pose arms and legs below z=1.12 so biped landmarking & full rig work
    # while leaving all head & facial geometry (z >= 1.22) completely untouched.
    parts = []
    tunic_mat = bpy.data.materials["Tunic"]
    trousers_mat = bpy.data.materials["Trousers"]
    for side, s in (("L", 1.0), ("R", -1.0)):
        # Arm: shoulder embedded in torso -> upper arm -> elbow dip -> forearm -> wrist dip -> hand
        parts.append(
            _build_limb_tube(
                f"_Arm_{side}",
                [
                    (Vector((0.12 * s, 0.0, 1.06)), 0.062, 0.062),
                    (Vector((0.23 * s, 0.0, 0.99)), 0.066, 0.064),
                    (Vector((0.27 * s, 0.0, 0.91)), 0.060, 0.058),
                    (Vector((0.30 * s, 0.0, 0.82)), 0.040, 0.040),
                    (Vector((0.34 * s, 0.0, 0.72)), 0.055, 0.054),
                    (Vector((0.38 * s, 0.0, 0.62)), 0.034, 0.034),
                    (Vector((0.39 * s, -0.01, 0.55)), 0.048, 0.068),
                    (Vector((0.39 * s, -0.01, 0.49)), 0.036, 0.052),
                ],
                tunic_mat,
            )
        )
        # Leg: hip embedded in pelvis -> thigh -> knee dip -> shin -> ankle dip -> foot
        parts.append(
            _build_limb_tube(
                f"_Leg_{side}",
                [
                    (Vector((0.105 * s, 0.0, 0.62)), 0.088, 0.088),
                    (Vector((0.105 * s, 0.0, 0.50)), 0.084, 0.082),
                    (Vector((0.105 * s, 0.0, 0.40)), 0.072, 0.070),
                    (Vector((0.105 * s, 0.0, 0.34)), 0.048, 0.048),
                    (Vector((0.105 * s, 0.0, 0.22)), 0.066, 0.066),
                    (Vector((0.105 * s, 0.0, 0.10)), 0.040, 0.040),
                    (Vector((0.105 * s, -0.055, 0.045)), 0.058, 0.115),
                    (Vector((0.105 * s, -0.055, 0.012)), 0.052, 0.108),
                ],
                trousers_mat,
            )
        )

    bpy.context.view_layer.objects.active = body
    if body.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for part in parts:
        mod = body.modifiers.new("union", "BOOLEAN")
        mod.operation = "UNION"
        mod.solver = "EXACT"
        mod.object = part
        bpy.context.view_layer.objects.active = body
        bpy.ops.object.modifier_apply(modifier=mod.name)
        pdata = part.data
        bpy.data.objects.remove(part, do_unlink=True)
        if pdata.users == 0:
            bpy.data.meshes.remove(pdata)

    for p in body.data.polygons:
        p.use_smooth = True
    body.data.update()
    bpy.context.view_layer.update()
    return body, dict(FACE_SPEC)


def face_vertex_regions(mesh_obj):
    """Return vertex index sets for jaw, neck, eyelids, brows, and crown anchor."""
    verts = mesh_obj.data.vertices
    mat = mesh_obj.matrix_world
    regions = {
        "jaw": [],
        "neck": [],
        "lid_t_l": [],
        "lid_t_r": [],
        "lid_b_l": [],
        "lid_b_r": [],
        "brow_l": [],
        "brow_r": [],
        "crown": [],
    }
    for v in verts:
        p = mat @ v.co
        if p.z >= 1.655:
            regions["crown"].append(v.index)
        if 1.20 <= p.z <= 1.235 and p.y < -0.02:
            regions["neck"].append(v.index)
        if p.y > -0.11:
            continue
        if 1.24 <= p.z <= 1.335 and abs(p.x) <= 0.115:
            regions["jaw"].append(v.index)
        for side, sx in (("l", 1.0), ("r", -1.0)):
            if abs(p.x - sx * 0.082) <= 0.046:
                if 1.500 <= p.z <= 1.536:
                    regions[f"lid_t_{side}"].append(v.index)
                elif 1.434 <= p.z <= 1.464:
                    regions[f"lid_b_{side}"].append(v.index)
            if abs(p.x - sx * 0.084) <= 0.048 and 1.554 <= p.z <= 1.592:
                regions[f"brow_{side}"].append(v.index)
    return regions


def _set_local_delta(rig, bone_name, world_delta):
    pb = rig.pose.bones.get(bone_name)
    if pb is None:
        return False
    mat3 = pb.bone.matrix_local.to_3x3()
    pb.location = mat3.inverted() @ Vector(world_delta)
    return True


def _set_local_rot(rig, bone_name, world_axis, angle_rad):
    pb = rig.pose.bones.get(bone_name)
    if pb is None:
        return False
    mat3 = pb.bone.matrix_local.to_3x3()
    local_axis = (mat3.inverted() @ Vector(world_axis)).normalized()
    pb.rotation_mode = "QUATERNION"
    pb.rotation_quaternion = Quaternion(local_axis, angle_rad)
    return True


def reset_face_pose(rig):
    for pb in rig.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    if "jaw_master" in rig.pose.bones and "mouth_lock" in rig.pose.bones["jaw_master"]:
        rig.pose.bones["jaw_master"]["mouth_lock"] = 0.0
    bpy.context.view_layer.update()


def apply_expression(rig, expr_name):
    """Apply a named facial expression on the generated biped_hero rig."""
    reset_face_pose(rig)
    if expr_name == "rest":
        return
    if expr_name == "jaw_open":
        # Rotate jaw_master downward around world +X (opens mouth/chin down)
        _set_local_rot(rig, "jaw_master", (1.0, 0.0, 0.0), math.radians(19.0))
    elif expr_name == "blink":
        # Close upper lids down and lower lids up toward eye midline (~0.060 m span)
        for side in ("L", "R"):
            _set_local_delta(rig, f"lid.T.{side}.002", (0.0, 0.0, -0.042))
            _set_local_delta(rig, f"lid.B.{side}.002", (0.0, 0.0, 0.018))
    elif expr_name == "brow_raise":
        # Rigify constrains brow.T.*.002 to 0.5*(.001 + .003) with offset, so
        # keep .002's own delta small to lift a smooth arch without a peak spike.
        for side in ("L", "R"):
            _set_local_delta(rig, f"brow.T.{side}.001", (0.0, 0.0, 0.022))
            _set_local_delta(rig, f"brow.T.{side}.002", (0.0, 0.0, 0.004))
            _set_local_delta(rig, f"brow.T.{side}.003", (0.0, 0.0, 0.022))
    elif expr_name == "talk_expressive":
        _set_local_rot(rig, "jaw_master", (1.0, 0.0, 0.0), math.radians(18.0))
        for side in ("L", "R"):
            _set_local_delta(rig, f"lid.T.{side}.002", (0.0, 0.0, -0.012))
            _set_local_delta(rig, f"brow.T.{side}.001", (0.0, 0.0, 0.018))
            _set_local_delta(rig, f"brow.T.{side}.002", (0.0, 0.0, 0.003))
            _set_local_delta(rig, f"brow.T.{side}.003", (0.0, 0.0, 0.018))
    else:
        raise ValueError(f"unknown expression: {expr_name!r}")
    bpy.context.view_layer.update()


def evaluated_world_positions(mesh_obj):
    deps = bpy.context.evaluated_depsgraph_get()
    eval_obj = mesh_obj.evaluated_get(deps)
    mw = eval_obj.matrix_world
    mesh = eval_obj.data
    return np.array([tuple(mw @ v.co) for v in mesh.vertices], dtype=np.float64)


# Crisp 5x7 uppercase bitmap font for headless PNG panel labels (no PIL/TTF needed)
_FONT_5X7 = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01110"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "(": ("00100", "01000", "10000", "10000", "10000", "01000", "00100"),
    ")": ("00100", "00010", "00001", "00001", "00001", "00010", "00100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def _draw_label(buf, x0, y0, text, scale=2, color=(1.0, 1.0, 1.0, 1.0)):
    """Draw bitmap text into an (H, W, 4) float RGBA buffer (row 0 = top)."""
    h, w, _ = buf.shape
    cx = x0
    for ch in text.upper():
        glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit != "1":
                    continue
                yy0 = y0 + gy * scale
                xx0 = cx + gx * scale
                if 0 <= yy0 and yy0 + scale <= h and 0 <= xx0 and xx0 + scale <= w:
                    buf[yy0 : yy0 + scale, xx0 : xx0 + scale] = color
        cx += 6 * scale


def setup_talk_camera():
    """Create and activate the talk-shot camera (1.3 m, 38 deg vertical FOV)."""
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("ToonTalkCam")
    cam_data.sensor_fit = "VERTICAL"
    cam_data.angle_y = math.radians(TALK_CAM_FOV_DEG)
    cam = bpy.data.objects.new("ToonTalkCam", cam_data)
    scene.collection.objects.link(cam)
    yaw = math.radians(TALK_CAM_YAW_DEG)
    pitch = math.radians(TALK_CAM_PITCH_DEG)
    offset = Vector(
        (
            math.sin(yaw) * math.cos(pitch),
            -math.cos(yaw) * math.cos(pitch),
            math.sin(pitch),
        )
    )
    cam.location = TALK_CAM_TARGET + offset * TALK_CAM_DISTANCE_M
    rot = (TALK_CAM_TARGET - cam.location).to_track_quat("-Z", "Y")
    cam.matrix_world = Matrix.Translation(cam.location) @ rot.to_matrix().to_4x4()
    scene.camera = cam
    bpy.context.view_layer.update()
    return cam


def _render_panel(path, width, height):
    scene = bpy.context.scene
    render = scene.render
    render.engine = "BLENDER_WORKBENCH"
    render.resolution_x = width
    render.resolution_y = height
    render.resolution_percentage = 100
    render.film_transparent = False
    render.image_settings.file_format = "PNG"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = False
    scene.world.color = (0.14, 0.16, 0.20)
    render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_comparison_sheet(panels, out_path, panel_w=420, panel_h=520):
    """Render and composite [(label, apply_fn)] side-by-side at the talk camera.

    Each entry in `panels` is `(label_text, callback)` where `callback()` sets up
    the scene pose/objects for that column before rendering. Composites into one
    PNG at `out_path` via numpy + bpy.data.images (no external Pillow dependency).
    """
    cam = setup_talk_camera()
    header_h = 38
    gap = 4
    total_w = len(panels) * panel_w + (len(panels) - 1) * gap
    total_h = header_h + panel_h
    canvas = np.zeros((total_h, total_w, 4), dtype=np.float32)
    canvas[:, :] = (0.08, 0.09, 0.11, 1.0)

    tmp_dir = os.path.dirname(os.path.abspath(out_path)) or "/tmp"
    os.makedirs(tmp_dir, exist_ok=True)
    panel_files = []
    try:
        for idx, (label, setup_fn) in enumerate(panels):
            setup_fn()
            bpy.context.view_layer.update()
            tmp_png = os.path.join(tmp_dir, f"_toon_panel_{idx}.png")
            _render_panel(tmp_png, panel_w, panel_h)
            panel_files.append(tmp_png)
            img = bpy.data.images.load(tmp_png, check_existing=False)
            try:
                # Blender image pixels are bottom-to-top RGBA floats
                arr = np.asarray(img.pixels[:], dtype=np.float32).reshape(
                    (panel_h, panel_w, 4)
                )
                arr = np.flipud(arr)
            finally:
                bpy.data.images.remove(img)
            x0 = idx * (panel_w + gap)
            canvas[header_h : header_h + panel_h, x0 : x0 + panel_w] = arr
            canvas[0:header_h, x0 : x0 + panel_w] = (0.12, 0.14, 0.18, 1.0)
            _draw_label(canvas, x0 + 12, 12, label, scale=2)

        # Write composite via Blender's image saver (expects bottom-to-top RGBA)
        out_img = bpy.data.images.new(
            "ToonFaceSheet", width=total_w, height=total_h, alpha=True
        )
        try:
            out_img.pixels.foreach_set(np.flipud(canvas).ravel())
            out_img.filepath_raw = out_path
            out_img.file_format = "PNG"
            out_img.save()
        finally:
            bpy.data.images.remove(out_img)
    finally:
        for p in panel_files:
            if os.path.exists(p):
                os.remove(p)
        cam_data = cam.data
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    return out_path
