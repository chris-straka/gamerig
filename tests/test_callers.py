# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless test: what other tools call here keeps working (work item gamerig-1).

Run: tools/run_test.sh tests/test_callers.py
(or python tools/run_test_bpy.py tests/test_callers.py)

The callers, and what this checks for each, called the way they call it:

1. bodywrap's Detect body landmarks (`bodywrap/blender/wrapforge/__init__.py`,
   `detector_path` = tools/detect_landmarks.py): Blender, headless, runs
   `tools/detect_landmarks.py -- MESH.obj --kind biped --slices 120 --facing -Y`
   and reads the JSON between RIGFORGE_LANDMARKS_BEGIN and _OK. A procedural
   body goes in; every key bodywrap reads comes out, in head-to-foot order.
2. bodywrap's Refit Rig: moves the joints of a `biped_hero` metarig, then calls
   `bpy.ops.pose.rigforge_generate()`. The rig regenerates in place (one `rig`
   object), keeps its DEF bones, and its DEF bones follow the moved metarig.
3. assetgen's repair-rig stage (`assetgen/src/adapters.rs`):
   `tools/genforge_adapter.sh repair-rig IN.glb OUT.glb RESULT.json --class humanoid`.
   A body with a one-bone rig comes out as `ok` with a DEF-only GLB holding
   every DEF bone of a generated `biped_hero` rig.

With a Blender binary (BLENDER_BIN, or the one running this) 1 and 3 run
as separate processes through the very entry points; under the bpy wheel
(no binary) they run in this process with the same arguments, and say so.

Pass criteria: exits 0 and prints RIGFORGE_CALLERS_OK.
"""

import contextlib
import importlib.util
import io
import itertools
import json
import os
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
# The keys bodywrap's derive_body_points reads from the detector.
BODYWRAP_KEYS = (
    "height",
    "neck_z",
    "armpit_z",
    "elbow_z",
    "wrist_z",
    "wrist_x",
    "hand_tip_z",
    "crotch_z",
    "knee_z",
    "ankle_z",
)


def fail(msg):
    print("RIGFORGE_CALLERS_FAIL:", msg)
    sys.exit(1)


def check(cond, msg):
    if not cond:
        fail(msg)


def blender_binary():
    """A Blender executable to run the entry points with, or None (bpy wheel)."""
    for b in (os.environ.get("BLENDER_BIN"), bpy.app.binary_path):
        if b and os.path.isfile(b) and os.access(b, os.X_OK):
            if os.path.basename(b).lower().startswith("blender"):
                return b
    return None


def parse_detector_output(text):
    """bodywrap's parser (wrapforge/__init__.py): the JSON between the markers."""
    begin = text.find("RIGFORGE_LANDMARKS_BEGIN")
    end = text.find("RIGFORGE_LANDMARKS_OK")
    if begin < 0 or end < 0 or end < begin:
        raise ValueError("rigforge detector output lacks BEGIN/OK markers")
    return json.loads(text[begin + len("RIGFORGE_LANDMARKS_BEGIN") : end])


def clean_scene():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.actions):
        for x in list(coll):
            coll.remove(x)


def glb_joints(path):
    with open(path, "rb") as f:
        raw = f.read()
    json_len = struct.unpack("<I", raw[12:16])[0]
    gltf = json.loads(raw[20 : 20 + json_len])
    joints = []
    for skin in gltf.get("skins", []):
        joints += [gltf["nodes"][j].get("name") for j in skin["joints"]]
    return joints


BINARY = blender_binary()
print(
    f"callers: entry points run {'as ' + BINARY if BINARY else 'in this process (no Blender binary)'}"
)
bpy.ops.preferences.addon_enable(module="rigforge")
from rigforge.auto_place import detect_landmarks as dl  # noqa: E402
from rigforge.metarigs import biped_hero  # noqa: E402

work = tempfile.mkdtemp(prefix="rf_callers_")
try:
    # ---- 1. the landmark detector, as bodywrap runs it
    clean_scene()
    bpy.ops.mesh.primitive_cube_add()  # build_synthetic clears the scene through an active object
    body, _spec = dl.build_synthetic()
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    obj_path = os.path.join(work, "body.obj")
    bpy.ops.wm.obj_export(
        filepath=obj_path, export_selected_objects=True, export_materials=False
    )
    check(os.path.getsize(obj_path) > 0, "body.obj not written")
    detector = os.path.join(TOOLS, "detect_landmarks.py")
    check(
        os.path.isfile(detector),
        "tools/detect_landmarks.py is gone (bodywrap's detector_path)",
    )
    args = [obj_path, "--kind", "biped", "--slices", "120", "--facing", "-Y"]
    if BINARY:
        proc = subprocess.run(
            [
                BINARY,
                "--background",
                "--factory-startup",
                "--python",
                detector,
                "--",
                *args,
            ],
            capture_output=True,
            text=True,
        )
        check(
            proc.returncode == 0,
            f"detector exit {proc.returncode}: {proc.stderr[-1500:]}",
        )
        text = proc.stdout
    else:
        buf, old = io.StringIO(), sys.argv
        sys.argv = ["blender", "--background", "--python", detector, "--", *args]
        try:
            with contextlib.redirect_stdout(buf):
                runpy.run_path(detector, run_name="__main__")
        finally:
            sys.argv = old
        text = buf.getvalue()
    try:
        prof = parse_detector_output(text)
    except ValueError as e:
        fail(f"detector: {e}: {text[-800:]}")
    missing = [k for k in BODYWRAP_KEYS if not isinstance(prof.get(k), (int, float))]
    check(not missing, f"detector output lacks bodywrap's keys {missing}: {prof}")
    check(prof.get("facing") in ("-Y", "+Y"), f"detector facing {prof.get('facing')!r}")
    check(prof["height"] > 0.5, f"detector height {prof['height']}")
    order = (
        ["neck_z", "armpit_z", "elbow_z", "wrist_z"],
        ["crotch_z", "knee_z", "ankle_z"],
    )
    for chain in order:
        zs = [prof[k] for k in chain]
        check(
            all(a > b for a, b in itertools.pairwise(zs)),
            f"detector order {chain}: {zs}",
        )
    print(f"detector: {len(prof)} keys, height {prof['height']:.2f}")

    # ---- 2. regenerate biped_hero on a moved metarig, as bodywrap's Refit Rig does
    clean_scene()
    arm_data = bpy.data.armatures.new("metarig_data")
    metarig = bpy.data.objects.new("metarig", arm_data)
    bpy.context.scene.collection.objects.link(metarig)
    bpy.context.view_layer.objects.active = metarig
    bpy.ops.object.mode_set(mode="EDIT")
    biped_hero.create(metarig)
    bpy.ops.object.mode_set(mode="OBJECT")
    check(
        hasattr(bpy.ops.pose, "rigforge_generate"),
        "no pose.rigforge_generate (bodywrap calls it)",
    )

    def generate():
        bpy.context.view_layer.objects.active = metarig
        bpy.ops.object.mode_set(mode="OBJECT")
        check(
            bpy.ops.pose.rigforge_generate() == {"FINISHED"},
            "rigforge_generate did not finish",
        )
        rigs = [
            o for o in bpy.data.objects if o.type == "ARMATURE" and o is not metarig
        ]
        check(
            len(rigs) == 1, f"expected one generated rig, got {[o.name for o in rigs]}"
        )
        rig = rigs[0]
        return rig.name, {
            b.name: rig.matrix_world @ b.head_local
            for b in rig.data.bones
            if b.name.startswith("DEF-")
        }

    name0, def0 = generate()
    check(
        name0 == "rig",
        f"the generated rig is named {name0!r} (bodywrap looks for 'rig')",
    )
    bone = "upper_arm.L"
    check(
        bone in metarig.data.bones and "DEF-" + bone in def0, f"no {bone} / DEF-{bone}"
    )
    shift = 0.07
    bpy.context.view_layer.objects.active = metarig
    bpy.ops.object.mode_set(mode="EDIT")
    for (
        eb
    ) in metarig.data.edit_bones:  # the whole arm chain moves out, as a wider fit would
        if eb.name.endswith(".L") and eb.name.split(".")[0] in (
            "upper_arm",
            "forearm",
            "hand",
        ):
            eb.head.x += shift
            eb.tail.x += shift
    bpy.ops.object.mode_set(mode="OBJECT")
    name1, def1 = generate()
    check(name1 == "rig", f"regenerated rig is named {name1!r}")
    check(
        set(def1) == set(def0),
        f"DEF bones changed: {sorted(set(def0) ^ set(def1))[:8]}",
    )
    moved = def1["DEF-" + bone].x - def0["DEF-" + bone].x
    check(
        abs(moved - shift) < 0.01,
        f"DEF-{bone} moved {moved:.3f} m, the metarig {shift} m",
    )
    still = max((def1[n] - def0[n]).length for n in def0 if n.endswith(".R"))
    check(still < 1e-4, f"the right side moved {still:.4f} m")
    n_def = len(def0)
    print(
        f"regenerate: {n_def} DEF bones; DEF-{bone} followed the metarig ({moved:.3f} m)"
    )

    # ---- 3. repair-rig, as assetgen calls it
    clean_scene()
    bpy.ops.mesh.primitive_cube_add()
    synth, _spec = dl.build_synthetic()
    bpy.ops.object.mode_set(mode="OBJECT")
    junk_data = bpy.data.armatures.new("Junk")
    junk = bpy.data.objects.new("Junk", junk_data)
    bpy.context.scene.collection.objects.link(junk)
    bpy.context.view_layer.objects.active = junk
    bpy.ops.object.mode_set(mode="EDIT")
    eb = junk_data.edit_bones.new("NotDEF")
    eb.head, eb.tail = (0, 0, 0), (0, 0, 0.5)
    bpy.ops.object.mode_set(mode="OBJECT")
    group = synth.vertex_groups.new(name="NotDEF")
    group.add(range(len(synth.data.vertices)), 1.0, "REPLACE")
    synth.modifiers.new("Armature", "ARMATURE").object = junk
    synth.parent = junk
    bpy.ops.object.select_all(action="DESELECT")
    synth.select_set(True)
    junk.select_set(True)
    broken = os.path.join(work, "broken.glb")
    bpy.ops.export_scene.gltf(filepath=broken, export_format="GLB", use_selection=True)
    step = os.path.join(work, "09-fix", "repair-rig")
    os.makedirs(step)
    out, result = os.path.join(step, "output.glb"), os.path.join(step, "result.json")
    argv = ["repair-rig", broken, out, result, "--class", "humanoid"]
    sh = os.path.join(TOOLS, "genforge_adapter.sh")
    check(
        os.path.isfile(sh) and os.access(sh, os.X_OK),
        "tools/genforge_adapter.sh missing or not executable (assetgen runs it)",
    )
    if BINARY:
        env = dict(
            os.environ,
            BLENDER_BIN=BINARY,
            RIGFORGE_SCRIPTS=os.path.join(work, "scripts"),
        )
        proc = subprocess.run([sh, *argv], capture_output=True, text=True, env=env)
        code = proc.returncode
        tail = proc.stdout[-800:] + proc.stderr[-800:]
    else:
        spec = importlib.util.spec_from_file_location(
            "genforge_adapter", os.path.join(TOOLS, "genforge_adapter.py")
        )
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        code, payload = adapter.run_adapter(argv)
        tail = str(payload)
    check(code == 0, f"repair-rig exit {code}: {tail}")
    with open(result, encoding="utf-8") as f:
        written = json.load(f)
    check(
        written.get("ok") is True and written.get("tool") == "rigforge",
        f"result.json: {written}",
    )
    check(
        written.get("outputs") == ["output.glb"],
        f"result.json outputs: {written.get('outputs')}",
    )
    joints = glb_joints(out)
    check(
        joints and all((j or "").startswith("DEF-") for j in joints),
        f"non-DEF joints: {[j for j in joints if not (j or '').startswith('DEF-')][:5]}",
    )
    check("NotDEF" not in joints, "the one-bone rig survived")
    check(
        len(joints) == n_def,
        f"repair-rig gave {len(joints)} DEF joints, a generated biped_hero has {n_def}",
    )
    print(f"repair-rig: one bone in, {len(joints)} DEF joints out")
finally:
    shutil.rmtree(work, ignore_errors=True)

print("RIGFORGE_CALLERS_OK")
