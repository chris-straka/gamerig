# SPDX-License-Identifier: GPL-2.0-or-later
"""Retarget Mixamo clips onto the biped hero deform rig via motionforge.

Thin Blender caller over `motionforge`: imports/normalizes the Mixamo FBX
clip (or builds the procedural walk proof source) in meters/Z-up, exports
source + target GLBs, delegates standardization (`motionforge standardize`)
and retargeting + foot-pinning (`motionforge animate`) to `motionforge`, and
validates the output GLB inventory and leg/root motion.

Headless, from the repo root (addon overlay needed for generate+export):

    rsync -a --delete rigforge/ /tmp/rf_scripts/addons/rigforge/
    BLENDER_USER_SCRIPTS=/tmp/rf_scripts \\
      /Applications/Blender.app/Contents/MacOS/Blender --background \\
      --factory-startup --python tools/retarget_mixamo.py -- --prove

Live validation with a real Mixamo clip:

    1. Download any animation FBX from mixamo.com ("Skin: Without Skin" is fine).
    2. Pass `--source ~/Downloads/<clip>.fbx --out /tmp/<clip>.glb`.
    3. Binary discovery: `--motionforge PATH`, `$MOTIONFORGE_BIN`, `PATH`, or
       sibling `../motion/rust/target/release/motionforge`.
"""

import argparse
import itertools
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile

import bmesh
import bpy
from mathutils import Matrix, Vector

# ---------------------------------------------------------------------------
# Correspondence map: Mixamo short bone name -> biped hero DEF bone.
# Keys omit the "mixamorig:" FBX prefix (see norm_src_name); extend by
# adding rows. 32 entries: 6 spine + 16 limbs + 10 finger proximals.
# ---------------------------------------------------------------------------
MIXAMO_TO_DEF = {
    # Spine chain (Mixamo 6 -> DEF 6 of 7; DEF-spine.005 stays at rest).
    "Hips": "DEF-spine",
    "Spine": "DEF-spine.001",
    "Spine1": "DEF-spine.002",
    "Spine2": "DEF-spine.003",
    "Neck": "DEF-spine.004",
    "Head": "DEF-spine.006",
    # Left arm / leg.
    "LeftShoulder": "DEF-shoulder.L",
    "LeftArm": "DEF-upper_arm.L",
    "LeftForeArm": "DEF-forearm.L",
    "LeftHand": "DEF-hand.L",
    "LeftUpLeg": "DEF-thigh.L",
    "LeftLeg": "DEF-shin.L",
    "LeftFoot": "DEF-foot.L",
    "LeftToeBase": "DEF-toe.L",
    # Right arm / leg.
    "RightShoulder": "DEF-shoulder.R",
    "RightArm": "DEF-upper_arm.R",
    "RightForeArm": "DEF-forearm.R",
    "RightHand": "DEF-hand.R",
    "RightUpLeg": "DEF-thigh.R",
    "RightLeg": "DEF-shin.R",
    "RightFoot": "DEF-foot.R",
    "RightToeBase": "DEF-toe.R",
    # Finger proximals (extend with *2/*3 -> DEF *.02/*.03 as needed).
    "LeftHandThumb1": "DEF-thumb.01.L",
    "LeftHandIndex1": "DEF-f_index.01.L",
    "LeftHandMiddle1": "DEF-f_middle.01.L",
    "LeftHandRing1": "DEF-f_ring.01.L",
    "LeftHandPinky1": "DEF-f_pinky.01.L",
    "RightHandThumb1": "DEF-thumb.01.R",
    "RightHandIndex1": "DEF-f_index.01.R",
    "RightHandMiddle1": "DEF-f_middle.01.R",
    "RightHandRing1": "DEF-f_ring.01.R",
    "RightHandPinky1": "DEF-f_pinky.01.R",
}

PROVE_FRAMES = 48
PROVE_FPS = 30
PROVE_OUT = "/tmp/rigforge_retarget.glb"


def fail(msg):
    print(f"RIGFORGE_RETARGET_FAIL: {msg}")
    sys.exit(1)


def all_fcurves(action):
    """Every fcurve in an action, layered (Blender 5.x) or legacy."""
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    out = []
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                out.extend(bag.fcurves)
    return out


def find_fcurve(action, data_path, index):
    for fc in all_fcurves(action):
        if fc.data_path == data_path and fc.array_index == index:
            return fc
    return None


def norm_src_name(name):
    """Strip Mixamo FBX name decoration to the MIXAMO_TO_DEF key form."""
    short = name.split(":")[-1]
    if short.lower().startswith("mixamorig_"):
        short = short[len("mixamorig_") :]
    return short


# ---------------------------------------------------------------------------
# Procedural proof source: mixamorig skeleton + scripted walk cycle.
# Faces +Y like a real Mixamo FBX import; 1.80 m tall so stride scaling
# (hero is 1.14 m) is exercised, not assumed.
# ---------------------------------------------------------------------------
def _src_skeleton_spec():
    """(name, head, tail, parent) in Blender units, facing +Y."""
    spine = [
        ("Hips", (0, 0, 0.98), (0, 0, 1.10), None),
        ("Spine", (0, 0, 1.10), (0, 0, 1.26), "Hips"),
        ("Spine1", (0, 0, 1.26), (0, 0, 1.42), "Spine"),
        ("Spine2", (0, 0, 1.42), (0, 0, 1.55), "Spine1"),
        ("Neck", (0, 0, 1.55), (0, 0, 1.63), "Spine2"),
        ("Head", (0, 0, 1.63), (0, 0, 1.80), "Neck"),
    ]
    limbs = []
    for side, s in (("Left", 1.0), ("Right", -1.0)):
        limbs += [
            (f"{side}Shoulder", (0.06 * s, 0, 1.52), (0.20 * s, 0, 1.52), "Spine2"),
            (f"{side}Arm", (0.20 * s, 0, 1.52), (0.48 * s, 0, 1.52), f"{side}Shoulder"),
            (
                f"{side}ForeArm",
                (0.48 * s, 0, 1.52),
                (0.74 * s, 0, 1.52),
                f"{side}Arm",
            ),
            (f"{side}Hand", (0.74 * s, 0, 1.52), (0.90 * s, 0, 1.52), f"{side}ForeArm"),
            (f"{side}UpLeg", (0.11 * s, 0, 0.98), (0.11 * s, 0, 0.52), "Hips"),
            (f"{side}Leg", (0.11 * s, 0, 0.52), (0.11 * s, 0, 0.10), f"{side}UpLeg"),
            (
                f"{side}Foot",
                (0.11 * s, 0, 0.10),
                (0.11 * s, 0.14, 0.03),
                f"{side}Leg",
            ),
            (
                f"{side}ToeBase",
                (0.11 * s, 0.14, 0.03),
                (0.11 * s, 0.24, 0.02),
                f"{side}Foot",
            ),
        ]
    return spine + limbs


def build_procedural_source(frames=PROVE_FRAMES, fps=PROVE_FPS):
    """Build the mixamorig source armature and key a walk cycle on it."""
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.armature_add(enter_editmode=False, location=(0, 0, 0))
    src = bpy.context.view_layer.objects.active
    src.name = "MixamoSource"
    arm = src.data
    arm.name = "MixamoSource"
    bpy.ops.object.mode_set(mode="EDIT")
    for b in list(arm.edit_bones):
        arm.edit_bones.remove(b)
    made = {}
    for name, head, tail, parent in _src_skeleton_spec():
        b = arm.edit_bones.new(f"mixamorig:{name}")
        b.head, b.tail = head, tail
        b.roll = 0.0
        if parent is not None:
            b.parent = made[parent]
            if (Vector(b.head) - Vector(made[parent].tail)).length < 1e-4:
                b.use_connect = True
        made[name] = b
    bpy.ops.object.mode_set(mode="POSE")
    for pb in src.pose.bones:
        pb.rotation_mode = "XYZ"

    scene = bpy.context.scene
    scene.render.fps = fps
    scene.frame_start, scene.frame_end = 1, frames
    action = bpy.data.actions.new("ProceduralWalk")
    src.animation_data_create()
    src.animation_data.action = action

    rest_rot = {pb.name: pb.bone.matrix.to_3x3() for pb in src.pose.bones}

    def swing(full_name, axis, angle, frame):
        # Express a world-axis rotation in the bone's local frame so the
        # scripted motion reads the same regardless of rest orientation.
        pb = src.pose.bones[full_name]
        r = rest_rot[full_name]
        local = r.inverted() @ Matrix.Rotation(angle, 3, axis) @ r
        scene.frame_set(frame)
        pb.matrix_basis = local.to_4x4()
        pb.keyframe_insert("rotation_euler", frame=frame)

    hips_r = rest_rot["mixamorig:Hips"]
    # Amplitudes are matched to the root travel so stance feet genuinely
    # plant: leg swing L*A*w ~= root speed (48 f @ 30 fps, 1.2 m travel).
    travel = 1.2  # metres of +Y root travel over the clip
    for f in range(1, frames + 1):
        i = f - 1
        ph = 2.0 * math.pi * i / frames
        scene.frame_set(f)
        # Root: travel + sway + double-frequency bob.
        off = Vector(
            (
                0.02 * math.sin(ph),
                travel * i / (frames - 1),
                0.025 * math.cos(2.0 * ph),
            )
        )
        hips = src.pose.bones["mixamorig:Hips"]
        hips.location = hips_r.inverted() @ off
        hips.keyframe_insert("location", frame=f)
        swing("mixamorig:Hips", "Z", 0.06 * math.sin(ph), f)
        # Legs: opposite-phase swing, knee flexes through the swing.
        for side, s in (("Left", 0.0), ("Right", math.pi)):
            p = ph + s
            up = 0.22 * math.sin(p)
            knee = -0.14 * (1.0 + math.cos(p - 0.4))
            swing(f"mixamorig:{side}UpLeg", "X", up, f)
            swing(f"mixamorig:{side}Leg", "X", knee, f)
            swing(f"mixamorig:{side}Foot", "X", -(up + knee) * 0.75, f)
            swing(
                f"mixamorig:{side}ToeBase", "X", 0.30 * max(0.0, math.sin(p + 2.4)), f
            )
            # Arms oppose the same-side leg; elbows hold a soft bend.
            arm_sw = -0.15 * math.sin(p)
            bend = 0.20 if side == "Left" else -0.20
            swing(f"mixamorig:{side}Shoulder", "Z", 0.03 * math.sin(p), f)
            swing(f"mixamorig:{side}Arm", "Z", arm_sw, f)
            swing(f"mixamorig:{side}ForeArm", "Z", arm_sw + bend, f)
            swing(f"mixamorig:{side}Hand", "Z", arm_sw + bend, f)
        # Torso: slight lean + yaw against the pelvis.
        swing("mixamorig:Spine", "X", 0.04 + 0.02 * math.sin(2.0 * ph), f)
        swing("mixamorig:Spine1", "Z", 0.08 * math.sin(ph), f)
        swing("mixamorig:Spine2", "Z", 0.05 * math.sin(ph), f)
        swing("mixamorig:Neck", "X", -0.02 * math.sin(2.0 * ph), f)

    scene.frame_set(1)
    bpy.context.view_layer.update()
    print(f"procedural source: {len(made)} bones, {frames} frames @ {fps} fps")
    return src, action


# ---------------------------------------------------------------------------
# Real-clip input + shared characterization helpers.
# ---------------------------------------------------------------------------
def find_mixamo_armature():
    """Armature whose bones best match the correspondence map, else None."""
    best, best_hits = None, 0
    for obj in bpy.data.objects:
        if obj.type != "ARMATURE" or "rig_id" in obj.data:
            continue
        shorts = {norm_src_name(b.name) for b in obj.data.bones}
        hits = len(shorts & set(MIXAMO_TO_DEF))
        if hits > best_hits:
            best, best_hits = obj, hits
    return best if best_hits >= 8 else None


def find_clip_action(arm):
    """Action driving the source armature (active one, else best scan)."""
    if arm.animation_data and arm.animation_data.action:
        return arm.animation_data.action
    names = {b.name for b in arm.data.bones}
    names |= {norm_src_name(n) for n in names}
    best, best_hits = None, 0
    for action in bpy.data.actions:
        paths = " ".join(fc.data_path for fc in all_fcurves(action))
        hits = sum(1 for n in names if f'"{n}"' in paths)
        if hits > best_hits:
            best, best_hits = action, hits
    return best if best_hits >= 4 else None


def import_mixamo_clip(path):
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_fbx")
    except Exception as exc:
        print(f"fbx addon enable note: {exc}")
    if not hasattr(bpy.ops.import_scene, "fbx"):
        fail("FBX importer unavailable (io_scene_fbx not enabled)")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    fresh = [o for o in bpy.data.objects if o not in before]
    print(f"fbx import: {len(fresh)} new objects from {path}")
    arm = find_mixamo_armature()
    if arm is None:
        fail(f"no mixamorig skeleton found in {path}")
    action = find_clip_action(arm)
    normalize_clip_transform(arm, action)
    if action is None:
        fail(f"no animation action found driving {arm.name} in {path}")
    lo, hi = action.frame_range
    frames = list(range(math.floor(lo), math.ceil(hi) + 1))
    if len(frames) < 2:
        fail(f"clip action {action.name} covers < 2 frames")
    print(
        f"clip: armature={arm.name} action={action.name} frames={frames[0]}..{frames[-1]}"
    )
    return arm, action, frames


def normalize_clip_transform(arm, action):
    """Bake the FBX import's object transform into meters/Z-up data.

    Mixamo 'for Unity' clips arrive centimeters + Y-up: the importer keeps
    raw data and compensates with object rotation+scale (0.01, +90deg X).
    Downstream code (facing, rest orientations, stride) assumes meter-scale
    Z-up data, so apply rotation+scale and scale location fcurves by the
    uniform factor. Rotation channels are parent-relative and untouched.
    """
    s = tuple(arm.scale)
    if abs(s[0] - s[1]) > 1e-6 or abs(s[0] - s[2]) > 1e-6:
        fail(f"non-uniform clip scale {s} — unsupported")
    if abs(s[0] - 1.0) < 1e-6 and Vector(arm.rotation_euler).length < 1e-6:
        return
    prev_active = bpy.context.view_layer.objects.active
    prev_sel = [o for o in bpy.data.objects if o.select_get()]
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in prev_sel:
        o.select_set(True)
    bpy.context.view_layer.objects.active = prev_active
    if abs(s[0] - 1.0) > 1e-6:
        for fc in all_fcurves(action):
            if fc.data_path.endswith("location"):
                for kp in fc.keyframe_points:
                    kp.co.y *= s[0]
                    kp.handle_left.y *= s[0]
                    kp.handle_right.y *= s[0]
    print(f"clip normalize: baked scale {s[0]:.4f} + object rotation")


def facing_sign(arm, foot_name, toe_name):
    """+1 if the rig faces +Y, -1 if -Y, from the foot->toe direction."""
    foot = arm.data.bones.get(foot_name)
    toe = arm.data.bones.get(toe_name)
    if foot is None or toe is None:
        fail(f"facing probe bones missing: {foot_name}, {toe_name}")
    v = toe.tail_local - foot.head_local
    if abs(v.y) < abs(v.x) or abs(v.y) < abs(v.z):
        fail(f"cannot determine Y facing from foot vector {tuple(v)}")
    return 1.0 if v.y > 0 else -1.0


def src_bone_names(src):
    """Map MIXAMO_TO_DEF short names -> actual source bone names."""
    out = {}
    for b in src.data.bones:
        short = norm_src_name(b.name)
        if short in MIXAMO_TO_DEF and short not in out:
            out[short] = b.name
    return out


def leg_length_from_bones(arm, upper_name, foot_name):
    upper = arm.data.bones.get(upper_name)
    foot = arm.data.bones.get(foot_name)
    if upper is None or foot is None:
        fail(f"leg probe bones missing: {upper_name}, {foot_name}")
    return (upper.head_local - foot.head_local).length


# ---------------------------------------------------------------------------
# motionforge CLI wrapper + GLB inspection.
# ---------------------------------------------------------------------------
def _sibling_motionforge_candidates():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    motion_root = os.path.join(os.path.dirname(repo_root), "motion")
    return [
        os.path.join(motion_root, "rust", "target", "release", "motionforge"),
        os.path.join(motion_root, "rust", "target", "debug", "motionforge"),
        os.path.expanduser("~/Games/_blender/motion/rust/target/release/motionforge"),
        os.path.expanduser("~/rigwork/bin/motionforge"),
    ]


def find_motionforge_binary(explicit_path="", search_sibling=True):
    """Locate an executable motionforge binary or fail with setup guidance."""
    if explicit_path:
        path = os.path.expanduser(explicit_path)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        raise RuntimeError(
            f"--motionforge binary not found or not executable: {path} "
            "(set MOTIONFORGE_BIN or build ~/Games/_blender/motion/rust)"
        )

    env_path = os.environ.get("MOTIONFORGE_BIN", "").strip()
    if env_path:
        path = os.path.expanduser(env_path)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        raise RuntimeError(
            f"MOTIONFORGE_BIN binary not found or not executable: {path} "
            "(set MOTIONFORGE_BIN, pass --motionforge, or put motionforge on PATH)"
        )

    on_path = shutil.which("motionforge")
    if on_path:
        return on_path

    if search_sibling:
        for cand in _sibling_motionforge_candidates():
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand

    raise RuntimeError(
        "motionforge binary not found: set MOTIONFORGE_BIN, pass "
        "--motionforge PATH, put motionforge on PATH, or build "
        "~/Games/_blender/motion/rust (cargo build --release --bin motionforge)"
    )


def _run_motionforge(cmd, step):
    """Run motionforge subprocess and fail loudly on non-zero exit."""
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.stdout:
        print(res.stdout.rstrip())
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or "(no output)"
        fail(f"motionforge {step} failed (exit {res.returncode}): {detail}")
    return res.stdout


def _export_source_clip_glb(src, frames, fps, out_glb):
    """Export the normalized Mixamo armature+action with a skinned proxy mesh."""
    scene = bpy.context.scene
    scene.frame_start = min(frames)
    scene.frame_end = max(frames)
    scene.render.fps = max(1, round(fps))

    bones = [b.name for b in src.data.bones]
    if len(bones) < 3:
        fail(f"source skeleton has too few bones to export: {len(bones)}")

    mesh_data = bpy.data.meshes.new("_mf_src_probe")
    bm = bmesh.new()
    verts = [bm.verts.new((0.001 * i, 0.0, 0.0)) for i in range(len(bones))]
    for i in range(len(verts)):
        bm.faces.new(
            (verts[i], verts[(i + 1) % len(verts)], verts[(i + 2) % len(verts)])
        )
    bm.to_mesh(mesh_data)
    bm.free()

    probe = bpy.data.objects.new("_mf_src_probe", mesh_data)
    scene.collection.objects.link(probe)
    for idx, bname in enumerate(bones):
        vg = probe.vertex_groups.new(name=bname)
        vg.add([idx], 1.0, "REPLACE")
    mod = probe.modifiers.new("Armature", "ARMATURE")
    mod.object = src

    try:
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        src.select_set(True)
        probe.select_set(True)
        bpy.context.view_layer.objects.active = src
        bpy.ops.export_scene.gltf(
            filepath=out_glb,
            export_format="GLB",
            use_selection=True,
            export_yup=True,
            export_apply=False,
            export_skins=True,
            export_def_bones=False,
            export_animations=True,
        )
    finally:
        bpy.data.objects.remove(probe, do_unlink=True)
        if mesh_data.users == 0:
            bpy.data.meshes.remove(mesh_data)

    if not os.path.isfile(out_glb):
        fail(f"source clip export wrote no file to {out_glb}")


def _read_glb(path):
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 20:
        fail(f"invalid GLB (too small: {len(raw)} bytes): {path}")
    json_len = struct.unpack("<I", raw[12:16])[0]
    gltf = json.loads(raw[20 : 20 + json_len].decode("utf-8"))
    off = 20 + json_len
    bin_bytes = b""
    if off + 8 <= len(raw):
        bin_len, bin_type = struct.unpack("<II", raw[off : off + 8])
        if bin_type == 0x004E4942:
            bin_bytes = raw[off + 8 : off + 8 + bin_len]
    return raw, gltf, bin_bytes


def _read_accessor_floats(gltf, bin_bytes, acc_idx):
    acc = gltf["accessors"][acc_idx]
    bv = gltf["bufferViews"][acc["bufferView"]]
    widths = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}
    width = widths[acc["type"]]
    stride = bv.get("byteStride") or (width * 4)
    base = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    return [
        struct.unpack_from(f"<{width}f", bin_bytes, base + i * stride)
        for i in range(acc["count"])
    ]


def glb_motion_proof(glb_path):
    """Read thigh swing and root travel directly from the output GLB clip."""
    _raw, gltf, bin_bytes = _read_glb(glb_path)
    clips = gltf.get("animations", [])
    if not clips:
        return {"thigh_swing_rad": 0.0, "root_travel_m": 0.0}
    clip = clips[0]
    nodes = gltf.get("nodes", [])
    samplers = clip.get("samplers", [])

    quats, roots = [], []
    for ch in clip.get("channels", []):
        target = ch.get("target", {})
        node_idx = target.get("node")
        if node_idx is None or node_idx >= len(nodes):
            continue
        node_name = nodes[node_idx].get("name")
        path = target.get("path")
        out_acc = samplers[ch["sampler"]]["output"]
        if node_name == "DEF-thigh.L" and path == "rotation":
            quats = _read_accessor_floats(gltf, bin_bytes, out_acc)
        elif node_name == "DEF-spine" and path == "translation":
            roots = _read_accessor_floats(gltf, bin_bytes, out_acc)

    swing = 0.0
    if quats:
        q0 = quats[0]
        swing = max(
            2.0
            * math.acos(min(1.0, abs(sum(a * b for a, b in zip(q0, q, strict=True)))))
            for q in quats
        )
    travel = 0.0
    for a, b in itertools.pairwise(roots):
        travel += math.sqrt(sum((y - x) ** 2 for x, y in zip(a, b, strict=True)))
    return {
        "thigh_swing_rad": round(swing, 4),
        "root_travel_m": round(travel, 4),
    }


def glb_report(path):
    raw, gltf, _bin_bytes = _read_glb(path)
    joints = []
    for skin in gltf.get("skins", []):
        joints += [gltf["nodes"][j].get("name") for j in skin["joints"]]
    clips = []
    for clip in gltf.get("animations", []):
        targets = [
            gltf["nodes"][ch["target"]["node"]].get("name") for ch in clip["channels"]
        ]
        clips.append(
            {"name": clip.get("name"), "channels": len(targets), "targets": targets}
        )
    return {
        "bytes": len(raw),
        "meshes": [m.get("name") for m in gltf.get("meshes", [])],
        "joints": len(joints),
        "non_def_joints": [j for j in joints if not (j or "").startswith("DEF-")],
        "animations": [{"name": c["name"], "channels": c["channels"]} for c in clips],
        "non_def_channels": sorted(
            {t for c in clips for t in c["targets"] if not (t or "").startswith("DEF-")}
        ),
    }


# ---------------------------------------------------------------------------
# Session assembly: hero rig, proof meshes, export.
# ---------------------------------------------------------------------------
def clean_scene():
    if bpy.context.active_object is not None and bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.actions):
        for x in list(coll):
            coll.remove(x)


def generate_hero():
    # NOTE: no mode_set here — clean_scene leaves no active object behind.
    bpy.ops.object.rigforge_biped_hero_metarig_add()
    bpy.ops.object.mode_set(mode="OBJECT")
    meta = bpy.context.view_layer.objects.active
    bpy.ops.pose.rigforge_generate()
    rig = next(
        (o for o in bpy.data.objects if o.type == "ARMATURE" and "rig_id" in o.data),
        None,
    )
    if rig is None:
        fail("hero generate produced no rig")
    n_def = sum(1 for b in rig.data.bones if b.name.startswith("DEF-"))
    print(f"hero: {meta.name} -> {rig.name} ({len(rig.data.bones)} bones, {n_def} DEF)")
    return rig


def bind_proof_meshes(rig):
    """Two cubes skinned to torso + head so the GLB proves skinning too."""
    bpy.ops.object.mode_set(mode="OBJECT")
    bound = []
    for bone_name, loc in (
        ("DEF-spine.002", (0, 0, 0.79)),
        ("DEF-spine.006", (0, 0, 1.08)),
    ):
        bpy.ops.mesh.primitive_cube_add(size=0.16, location=loc)
        cube = bpy.context.view_layer.objects.active
        mod = cube.modifiers.new("Armature", "ARMATURE")
        mod.object = rig
        vg = cube.vertex_groups.new(name=bone_name)
        vg.add(range(len(cube.data.vertices)), 1.0, "REPLACE")
        bound.append(cube.name)
    print(f"proof meshes bound: {bound}")
    return bound


def export_game_glb(rig, path):
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    if os.path.exists(path):
        os.remove(path)
    bpy.ops.wm.rigforge_game_export(filepath=path, export_animations=False)
    if not os.path.exists(path):
        fail(f"game export wrote no file to {path}")


def run_pipeline(
    src,
    tgt,
    frames,
    fps,
    out_path,
    do_pin,
    action_name,
    motionforge_bin="",
):
    """Characterize -> export GLBs -> motionforge standardize + animate. Returns report."""
    # Remove any prior output before invoking motionforge so errors never leave stale GLBs.
    if os.path.exists(out_path):
        os.remove(out_path)
    try:
        motionforge = find_motionforge_binary(motionforge_bin)
    except RuntimeError as exc:
        fail(str(exc))

    src_names = src_bone_names(src)
    src_facing = facing_sign(src, src_names["LeftFoot"], src_names["LeftToeBase"])
    tgt_facing = facing_sign(tgt, "DEF-foot.L", "DEF-toe.L")
    yaw_flip = src_facing != tgt_facing
    src_leg = leg_length_from_bones(src, src_names["LeftUpLeg"], src_names["LeftFoot"])
    tgt_leg = leg_length_from_bones(tgt, "DEF-thigh.L", "DEF-foot.L")
    stride = tgt_leg / src_leg
    print(
        f"facing: src={' +Y' if src_facing > 0 else ' -Y'} tgt={' +Y' if tgt_facing > 0 else ' -Y'} yaw_flip={yaw_flip}"
    )
    print(f"stride: src_leg={src_leg:.4f} tgt_leg={tgt_leg:.4f} scale={stride:.4f}")

    with tempfile.TemporaryDirectory(prefix="rf_retarget_") as tmp:
        src_glb = os.path.join(tmp, "src_clip.glb")
        _export_source_clip_glb(src, frames, fps, src_glb)
        src_rep = glb_report(src_glb)
        if not src_rep["animations"]:
            fail("exported source GLB contains no animations")
        src_clip_name = src_rep["animations"][0].get("name") or action_name

        # Drop source rig and actions before exporting the target rig GLB.
        bpy.data.objects.remove(src, do_unlink=True)
        for act in list(bpy.data.actions):
            bpy.data.actions.remove(act)
        bpy.context.view_layer.objects.active = tgt

        bind_proof_meshes(tgt)
        raw_tgt_glb = os.path.join(tmp, "raw_tgt.glb")
        std_tgt_glb = os.path.join(tmp, "std_tgt.glb")
        staged_out = os.path.join(tmp, "animated.glb")
        export_game_glb(tgt, raw_tgt_glb)

        _run_motionforge(
            [
                motionforge,
                "standardize",
                "--input",
                raw_tgt_glb,
                "--output",
                std_tgt_glb,
                "--class",
                "humanoid",
            ],
            "standardize",
        )
        anim_cmd = [
            motionforge,
            "animate",
            "--input",
            std_tgt_glb,
            "--clips",
            src_glb,
            "--output",
            staged_out,
            "--fps",
            str(max(1, round(fps))),
            "--pick",
            f"{src_clip_name}={action_name}",
            "--no-contact",
        ]
        if not do_pin:
            anim_cmd.append("--no-pin")
        _run_motionforge(anim_cmd, "animate")
        if not os.path.isfile(staged_out):
            fail(f"motionforge animate produced no output GLB at {staged_out}")

        out_dir = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(out_dir, exist_ok=True)
        shutil.copyfile(staged_out, out_path)

    proof = glb_motion_proof(out_path)
    print(f"motion proof: {json.dumps(proof)}")
    if proof["thigh_swing_rad"] < 0.15:
        fail(f"no leg motion transferred (swing {proof['thigh_swing_rad']} rad)")
    if proof["root_travel_m"] < 0.05:
        fail(f"no root motion transferred (travel {proof['root_travel_m']} m)")

    rep = glb_report(out_path)
    print(f"glb: {out_path} ({rep['bytes']} bytes, {rep['joints']} joints)")
    if rep["non_def_joints"]:
        fail(f"non-DEF joints in GLB: {rep['non_def_joints'][:5]}")
    if len(rep["animations"]) != 1:
        fail(f"expected exactly 1 animation clip, got {len(rep['animations'])}")
    if rep["non_def_channels"]:
        fail(f"animation on non-DEF nodes: {rep['non_def_channels'][:5]}")
    n_ch = sum(c["channels"] for c in rep["animations"])
    if n_ch < 2:
        fail(f"only {n_ch} animation channels baked")

    return {
        "map_entries": len(MIXAMO_TO_DEF),
        "facing": {"src": src_facing, "tgt": tgt_facing, "yaw_flip": yaw_flip},
        "stride_scale": round(stride, 4),
        "motionforge": motionforge,
        "pin": do_pin,
        "motion": proof,
        "glb": {**rep, "path": out_path},
    }


def main(argv):
    parser = argparse.ArgumentParser(
        description="Retarget Mixamo clips onto the biped hero rig via motionforge"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--prove", action="store_true", help="procedural walk-cycle proof run"
    )
    mode.add_argument("--source", help="real Mixamo clip FBX to retarget")
    parser.add_argument("--out", default=PROVE_OUT, help="output GLB path")
    parser.add_argument("--frames", type=int, default=PROVE_FRAMES, help="prove frames")
    parser.add_argument("--fps", type=int, default=PROVE_FPS, help="prove fps")
    parser.add_argument("--no-pin", action="store_true", help="skip the foot-pin pass")
    parser.add_argument("--blend", default="", help="also save the session .blend here")
    parser.add_argument(
        "--motionforge",
        default="",
        help="path to motionforge binary (or set MOTIONFORGE_BIN)",
    )
    args = parser.parse_args(argv)

    bpy.ops.preferences.addon_enable(module="rigforge")
    import rigforge

    scripts_env = os.environ.get("BLENDER_USER_SCRIPTS", "")
    if "rf_scripts" not in rigforge.__file__ and (
        not scripts_env or not rigforge.__file__.startswith(scripts_env)
    ):
        fail(f"loaded wrong addon copy: {rigforge.__file__}")
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_gltf2")
    except Exception as exc:
        print(f"gltf addon enable note: {exc}")

    clean_scene()
    tgt = generate_hero()
    if args.prove:
        src, _src_action = build_procedural_source(args.frames, args.fps)
        frames = list(range(1, args.frames + 1))
        fps = args.fps
        label = "ProceduralWalk"
    else:
        if not os.path.exists(args.source):
            fail(f"source clip not found: {args.source}")
        src, _src_action, frames = import_mixamo_clip(args.source)
        fps = bpy.context.scene.render.fps
        label = os.path.splitext(os.path.basename(args.source))[0]

    report = run_pipeline(
        src,
        tgt,
        frames,
        fps,
        args.out,
        not args.no_pin,
        f"Mixamo_{label}",
        motionforge_bin=args.motionforge,
    )
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=args.blend)

    print("RIGFORGE_RETARGET_BEGIN")
    print(json.dumps(report, indent=2))
    print("RIGFORGE_RETARGET_END")
    print("RIGFORGE_RETARGET_OK")


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
