# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless test: retarget source intake normalizes FBX import state.

Run (from the repo root; no overlay needed, no external assets):
    /Applications/Blender.app/Contents/MacOS/Blender --background \\
      --factory-startup --python tests/test_retarget.py

Regression test for the live-clip failure: Mixamo 'for Unity' FBX clips
arrive centimeters + Y-up with the importer compensating via object
rotation+scale, which made facing detection fail
('cannot determine Y facing'). The test builds the procedural source,
puts it into exactly that import state (cm data in the file frame +
compensating object transform), proves facing fails red, normalizes,
and proves the pipeline inputs recover (facing sign, meter-scale leg
length, location keys). No Adobe assets are committed for this.

Pass criteria: exits 0 and prints RIGFORGE_RETARGET_TEST_OK.
"""

import importlib.util
import json
import math
import os
import stat
import struct
import sys
import tempfile

import bpy
from mathutils import Matrix, Vector


def check(cond, msg):
    if not cond:
        raise SystemExit(f"TEST FAIL: {msg}")
    print(f"  ok: {msg}")


def _write_exe(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env python3\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


def _inject_proof_anim_glb(src_glb, dst_glb, clip_name):
    """Copy a valid rig GLB and add one animation driving DEF-spine & DEF-thigh.L."""
    with open(src_glb, "rb") as f:
        raw = f.read()
    magic, ver, _total = struct.unpack("<III", raw[:12])
    assert magic == 0x46546C67 and ver == 2
    jlen, jtype = struct.unpack("<II", raw[12:20])
    assert jtype == 0x4E4F534A
    gltf = json.loads(raw[20 : 20 + jlen].decode("utf-8"))
    off = 20 + jlen
    bin_bytes = bytearray()
    if off + 8 <= len(raw):
        blen, btype = struct.unpack("<II", raw[off : off + 8])
        assert btype == 0x004E4942
        bin_bytes.extend(raw[off + 8 : off + 8 + blen])
    while len(bin_bytes) % 4:
        bin_bytes.append(0)

    names = [n.get("name") for n in gltf.get("nodes", [])]
    spine_idx = names.index("DEF-spine")
    thigh_idx = names.index("DEF-thigh.L")

    def push_acc(fmt, vals, acc_type, with_minmax=False):
        start = len(bin_bytes)
        for row in vals:
            bin_bytes.extend(struct.pack(f"<{fmt}", *row))
        byte_len = len(bin_bytes) - start
        while len(bin_bytes) % 4:
            bin_bytes.append(0)
        bv_idx = len(gltf.setdefault("bufferViews", []))
        gltf["bufferViews"].append(
            {"buffer": 0, "byteOffset": start, "byteLength": byte_len}
        )
        acc = {
            "bufferView": bv_idx,
            "componentType": 5126,
            "count": len(vals),
            "type": acc_type,
        }
        if with_minmax:
            acc["min"] = [min(r[i] for r in vals) for i in range(len(vals[0]))]
            acc["max"] = [max(r[i] for r in vals) for i in range(len(vals[0]))]
        acc_idx = len(gltf.setdefault("accessors", []))
        gltf["accessors"].append(acc)
        return acc_idx

    t_acc = push_acc("f", [[0.0], [0.5], [1.0]], "SCALAR", with_minmax=True)
    loc_acc = push_acc(
        "3f", [[0.0, 0.55, 0.0], [0.0, 0.56, 0.2], [0.0, 0.55, 0.4]], "VEC3"
    )
    a = 0.25
    q1 = [math.sin(a / 2), 0.0, 0.0, math.cos(a / 2)]
    rot_acc = push_acc("4f", [[0.0, 0.0, 0.0, 1.0], q1, [0.0, 0.0, 0.0, 1.0]], "VEC4")

    gltf["buffers"][0]["byteLength"] = len(bin_bytes)
    gltf["animations"] = [
        {
            "name": clip_name,
            "samplers": [
                {"input": t_acc, "interpolation": "LINEAR", "output": loc_acc},
                {"input": t_acc, "interpolation": "LINEAR", "output": rot_acc},
            ],
            "channels": [
                {"sampler": 0, "target": {"node": spine_idx, "path": "translation"}},
                {"sampler": 1, "target": {"node": thigh_idx, "path": "rotation"}},
            ],
        }
    ]
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_bytes)
    with open(dst_glb, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<I", len(js)) + b"JSON" + js)
        f.write(struct.pack("<I", len(bin_bytes)) + b"BIN\0" + bytes(bin_bytes))


def test_motionforge_delegation(rt, tool_path):
    """Gate-first tests for gamerig-5 thin motionforge caller."""
    duplicated = (
        "retarget",
        "build_drive_plan",
        "pin_feet",
        "foot_tracks",
        "stance_mask",
        "measure_foot_slide",
        "rest_parent_rel",
        "FOLLOWERS",
    )
    leftover = [sym for sym in duplicated if hasattr(rt, sym)]
    check(
        not leftover, f"duplicated Python retarget math removed (leftover: {leftover})"
    )
    check(
        hasattr(rt, "find_motionforge_binary"),
        "retarget_mixamo exposes find_motionforge_binary",
    )

    # 1. Missing binary fails loudly with actionable guidance.
    old_env = os.environ.get("MOTIONFORGE_BIN")
    old_path = os.environ.get("PATH")
    try:
        os.environ.pop("MOTIONFORGE_BIN", None)
        os.environ["PATH"] = ""
        raised = False
        try:
            rt.find_motionforge_binary("", search_sibling=False)
        except (SystemExit, RuntimeError) as exc:
            raised = True
            msg = str(exc)
            check(
                "MOTIONFORGE_BIN" in msg and "motionforge" in msg.lower(),
                f"missing binary error is actionable: {msg}",
            )
        check(raised, "find_motionforge_binary fails when motionforge is missing")
    finally:
        if old_env is not None:
            os.environ["MOTIONFORGE_BIN"] = old_env
        if old_path is not None:
            os.environ["PATH"] = old_path

    # 2. Successful CLI delegation (with and without --no-pin) + arg verification.
    with tempfile.TemporaryDirectory(prefix="rf_rt_cli_") as tmp:
        log_path = os.path.join(tmp, "mf_calls.jsonl")
        fake_mf = os.path.join(tmp, "motionforge")
        helper_src = (
            "import json, shutil, sys\n"
            f"log_path = {log_path!r}\n"
            "argv = sys.argv[1:]\n"
            "with open(log_path, 'a', encoding='utf-8') as lf:\n"
            "    lf.write(json.dumps(argv) + '\\n')\n"
            "cmd = argv[0]\n"
            "if cmd == 'standardize':\n"
            "    inp = argv[argv.index('--input') + 1]\n"
            "    out = argv[argv.index('--output') + 1]\n"
            "    shutil.copyfile(inp, out)\n"
            "elif cmd == 'animate':\n"
            "    inp = argv[argv.index('--input') + 1]\n"
            "    clips = argv[argv.index('--clips') + 1]\n"
            "    out = argv[argv.index('--output') + 1]\n"
            "    pick = argv[argv.index('--pick') + 1]\n"
            "    clip_name = pick.split('=', 1)[1]\n"
            "    assert os.path.isfile(clips), f'missing clips GLB: {clips}'\n"
        )
        # Re-use _inject_proof_anim_glb logic cleanly in the fake binary.
        _write_exe(
            fake_mf,
            "import os\n"
            + helper_src
            + f"""    import importlib.util
    spec = importlib.util.spec_from_file_location("tr_helper", {os.path.abspath(__file__)!r})
    mod = importlib.util.module_from_spec(spec)
    # Write animated GLB directly without importing bpy inside the fake CLI.
    import math, struct
    with open(inp, "rb") as f:
        raw = f.read()
    jlen = struct.unpack("<I", raw[12:16])[0]
    gltf = json.loads(raw[20 : 20 + jlen].decode("utf-8"))
    off = 20 + jlen
    blen = struct.unpack("<I", raw[off : off + 4])[0]
    bin_bytes = bytearray(raw[off + 8 : off + 8 + blen])
    while len(bin_bytes) % 4:
        bin_bytes.append(0)
    names = [n.get("name") for n in gltf.get("nodes", [])]
    spine_idx = names.index("DEF-spine")
    thigh_idx = names.index("DEF-thigh.L")
    def push_acc(fmt, vals, acc_type, mm=False):
        s = len(bin_bytes)
        for row in vals:
            bin_bytes.extend(struct.pack(f"<{{fmt}}", *row))
        bl = len(bin_bytes) - s
        while len(bin_bytes) % 4:
            bin_bytes.append(0)
        bv = len(gltf["bufferViews"])
        gltf["bufferViews"].append({{"buffer": 0, "byteOffset": s, "byteLength": bl}})
        acc = {{"bufferView": bv, "componentType": 5126, "count": len(vals), "type": acc_type}}
        if mm:
            acc["min"] = [min(r[i] for r in vals) for i in range(len(vals[0]))]
            acc["max"] = [max(r[i] for r in vals) for i in range(len(vals[0]))]
        ai = len(gltf["accessors"])
        gltf["accessors"].append(acc)
        return ai
    ta = push_acc("f", [[0.0], [0.5], [1.0]], "SCALAR", True)
    la = push_acc("3f", [[0.0, 0.55, 0.0], [0.0, 0.56, 0.2], [0.0, 0.55, 0.4]], "VEC3")
    a = 0.25
    ra = push_acc("4f", [[0.0, 0.0, 0.0, 1.0], [math.sin(a / 2), 0.0, 0.0, math.cos(a / 2)], [0.0, 0.0, 0.0, 1.0]], "VEC4")
    gltf["buffers"][0]["byteLength"] = len(bin_bytes)
    gltf["animations"] = [{{"name": clip_name, "samplers": [{{"input": ta, "interpolation": "LINEAR", "output": la}}, {{"input": ta, "interpolation": "LINEAR", "output": ra}}], "channels": [{{"sampler": 0, "target": {{"node": spine_idx, "path": "translation"}}}}, {{"sampler": 1, "target": {{"node": thigh_idx, "path": "rotation"}}}}]}}]
    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    tot = 12 + 8 + len(js) + 8 + len(bin_bytes)
    with open(out, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, tot))
        f.write(struct.pack("<I", len(js)) + b"JSON" + js)
        f.write(struct.pack("<I", len(bin_bytes)) + b"BIN\\0" + bytes(bin_bytes))
else:
    sys.exit(2)
""",
        )

        out1 = os.path.join(tmp, "out_pin.glb")
        rt.main(
            [
                "--prove",
                "--frames",
                "6",
                "--fps",
                "30",
                "--out",
                out1,
                "--motionforge",
                fake_mf,
            ]
        )
        check(os.path.isfile(out1), "retarget_mixamo --prove wrote output GLB")

        out2 = os.path.join(tmp, "out_nopin.glb")
        rt.main(
            [
                "--prove",
                "--frames",
                "6",
                "--fps",
                "24",
                "--no-pin",
                "--out",
                out2,
                "--motionforge",
                fake_mf,
            ]
        )
        check(os.path.isfile(out2), "retarget_mixamo --no-pin wrote output GLB")

        with open(log_path, encoding="utf-8") as lf:
            calls = [json.loads(line) for line in lf if line.strip()]
        anim_calls = [c for c in calls if c and c[0] == "animate"]
        check(len(anim_calls) == 2, f"expected 2 animate calls, got {len(anim_calls)}")
        check("--no-pin" not in anim_calls[0], "pin run omits --no-pin")
        check("--no-pin" in anim_calls[1], "--no-pin run forwards --no-pin")
        check(
            "--fps" in anim_calls[1]
            and anim_calls[1][anim_calls[1].index("--fps") + 1] == "24",
            "forwards --fps 24",
        )

        # 3. Subprocess non-zero exit must fail loudly and leave no stale output GLB.
        fail_mf = os.path.join(tmp, "motionforge_fail")
        _write_exe(
            fail_mf,
            "import sys\n"
            "if sys.argv[1] == 'standardize':\n"
            "    import shutil\n"
            "    shutil.copyfile(sys.argv[sys.argv.index('--input') + 1], sys.argv[sys.argv.index('--output') + 1])\n"
            "    sys.exit(0)\n"
            "sys.stderr.write('simulated motionforge animate error\\n')\n"
            "sys.exit(3)\n",
        )
        stale_out = os.path.join(tmp, "stale.glb")
        with open(stale_out, "wb") as f:
            f.write(b"STALE")
        failed = False
        try:
            rt.main(
                [
                    "--prove",
                    "--frames",
                    "6",
                    "--out",
                    stale_out,
                    "--motionforge",
                    fail_mf,
                ]
            )
        except SystemExit as exc:
            failed = exc.code != 0
        check(failed, "failing motionforge CLI exits non-zero")
        check(
            not os.path.exists(stale_out),
            "failing motionforge CLI never leaves stale output GLB behind",
        )


def main():
    tool = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tools", "retarget_mixamo.py"
    )
    spec = importlib.util.spec_from_file_location("rt", tool)
    rt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rt)

    src, action = rt.build_procedural_source(frames=6)
    names = rt.src_bone_names(src)
    # Sanity on the meter/Z-up procedural source (pre-existing behavior).
    check(
        rt.facing_sign(src, names["LeftFoot"], names["LeftToeBase"]) in (1.0, -1.0),
        "procedural facing works",
    )
    leg_m = rt.leg_length_from_bones(src, names["LeftUpLeg"], names["LeftFoot"])
    check(0.5 < leg_m < 1.2, f"procedural leg {leg_m:.3f} m is sane")
    loc0 = None
    for fc in rt.all_fcurves(action):
        if fc.data_path.endswith("location") and fc.keyframe_points:
            loc0 = (fc.data_path, fc.array_index, fc.keyframe_points[0].co.y)
            break
    check(loc0 is not None, "procedural source has location keys")

    # Mimic FBX import state: cm data in the Y-up file frame + object
    # rotation+scale compensating (exactly what io_scene_fbx produces).
    bpy.context.view_layer.objects.active = src
    bpy.ops.object.mode_set(mode="EDIT")
    for eb in src.data.edit_bones:
        eb.use_connect = False
    rot = Matrix.Rotation(-1.5707963, 4, "X")
    for eb in src.data.edit_bones:
        eb.head = rot @ (Vector(eb.head) * 100.0)
        eb.tail = rot @ (Vector(eb.tail) * 100.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    src.rotation_euler = (1.5707963, 0.0, 0.0)
    src.scale = (0.01, 0.01, 0.01)
    for fc in rt.all_fcurves(action):
        if fc.data_path.endswith("location"):
            for kp in fc.keyframe_points:
                kp.co.y *= 100.0

    # RED: facing must fail on the un-normalized import state.
    try:
        rt.facing_sign(src, names["LeftFoot"], names["LeftToeBase"])
    except SystemExit:
        print("  ok: facing fails red on raw import state")
    else:
        raise SystemExit("TEST FAIL: facing passed on raw import state (repro broken)")

    # GREEN: normalize recovers meter/Z-up pipeline inputs.
    rt.normalize_clip_transform(src, action)
    check(
        all(abs(s - 1.0) < 1e-6 for s in src.scale),
        f"scale recovered to 1 (got {tuple(round(s, 6) for s in src.scale)})",
    )
    check(Vector(src.rotation_euler).length < 1e-6, "rotation recovered to identity")
    check(
        rt.facing_sign(src, names["LeftFoot"], names["LeftToeBase"]) in (1.0, -1.0),
        "facing works after normalize",
    )
    leg1 = rt.leg_length_from_bones(src, names["LeftUpLeg"], names["LeftFoot"])
    check(abs(leg1 - leg_m) < 1e-4, f"leg length recovered ({leg1:.4f} m)")
    for fc in rt.all_fcurves(action):
        if (fc.data_path, fc.array_index) == (loc0[0], loc0[1]):
            check(
                abs(fc.keyframe_points[0].co.y - loc0[2]) < 1e-6,
                "location key recovered to meters",
            )
            break
    test_motionforge_delegation(rt, tool)
    print("RIGFORGE_RETARGET_TEST_OK")


if __name__ == "__main__":
    main()
    sys.exit(0)
