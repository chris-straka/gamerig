# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared math, bone-solving, verification, and module-emission helpers."""

import math
import os

import bpy
from mathutils import Matrix, Vector

ROLL_RECOMPUTE_DEG = 5.0  # Section 4.3: preserve rolls below this
CLOSE_EPS = 1e-4  # same epsilon as reconnect_from
SYMMETRY_TOL = 0.01  # Section 4.4: L/R mirror error must be < 1 cm
NUDGE_STEP = 0.002  # verify-nudge: target step opposite the near surface
NUDGE_MAX_STEPS = 8
NUDGE_HEADROOM = 0.002  # nudge past the margin by this (remesh variance)
DRIVERS = (
    "upper_arm.L",
    "forearm.L",
    "hand.L",
    "thigh.L",
    "shin.L",
    "foot.L",
)


def fail(msg):
    """Print failure marker and abort CLI/stage execution."""
    print("RIGFORGE_FIT_FAIL:", msg)
    raise SystemExit(msg)


def arg_value(args, flag):
    """Return the CLI value following `flag`, failing cleanly if missing."""
    idx = args.index(flag)
    if idx + 1 >= len(args):
        fail(f"{flag} requires a value")
    return args[idx + 1]


def fit_line(points):
    """Least squares x(z), y(z) over (x, y, z) samples; returns eval fn."""
    n = len(points)
    if n == 0:
        fail("degenerate limb run: no bands provided")
    sz = sum(p[2] for p in points)
    szz = sum(p[2] ** 2 for p in points)
    denom = n * szz - sz * sz
    if denom == 0:
        fail("degenerate limb run: all bands share one z")
    out = {}
    for key, idx in (("x", 0), ("y", 1)):
        sv = sum(p[idx] for p in points)
        szv = sum(p[idx] * p[2] for p in points)
        slope = (n * szv - sz * sv) / denom
        out[key] = (slope, sv / n - slope * sz / n)
    (mx, bx), (my, by) = out["x"], out["y"]

    def at(z):
        return (mx * z + bx, my * z + by, z)

    at.slope = (mx, my)
    return at


def eval_envelope(env):
    """A fresh evaluated envelope for raycasts (scene may have changed)."""
    bpy.context.view_layer.update()
    return env.evaluated_get(bpy.context.evaluated_depsgraph_get())


def mid_state(rv, eval_env, mid, margin):
    """Gate-semantics verdict on a prospective midpoint: (ok, depth)."""
    held = rv.point_depth(eval_env, mid)
    return held["inside"] and held["depth"] >= margin, round(held["depth"], 4)


def mirror_x(v):
    """Mirror of a prospective point (.L solve <-> .R check)."""
    return Vector((-v.x, v.y, v.z))


def nearest_surface_dir(rv, eval_env, mid):
    """Direction of the nearest surface from mid (for nudge steps)."""
    best, best_d = None, None
    for d, (_inside, dist) in zip(rv.DIRS, rv.ray_parity(eval_env, mid), strict=True):
        if dist is not None and (best_d is None or dist < best_d):
            best, best_d = d, dist
    return best


def nudge_target(rv, eval_env, margin, target, mid_of, label, cautions):
    """Bounded verify-nudge: step the target off the nearest surface.

    mid_of(target) lists (bone, midpoint) pairs riding on the target; a
    step is kept only while no passing midpoint un-passes. Returns the
    (possibly moved) target.
    """
    cur = target.copy()

    def states_of(t):
        return [(name, mid, rv.point_depth(eval_env, mid)) for name, mid in mid_of(t)]

    def passed(held):
        return held["inside"] and held["depth"] >= margin

    def snug(held):
        return held["inside"] and held["depth"] >= margin + NUDGE_HEADROOM

    if all(passed(held) for _, _, held in states_of(cur)):
        return cur, 0
    moved = 0
    for _ in range(NUDGE_MAX_STEPS):
        states = states_of(cur)
        if all(snug(held) for _, _, held in states):
            break
        failing = [(name, mid) for name, mid, held in states if not passed(held)]
        pool = failing or [(name, mid) for name, mid, _ in states]
        depths = {name: held["depth"] for name, _, held in states}
        worst_name, worst_mid = min(pool, key=lambda nm: depths[nm[0]])
        step_dir = nearest_surface_dir(rv, eval_env, worst_mid)
        if step_dir is None:
            break
        if worst_name.endswith(".R"):
            # The R midpoint mirrors the L target: convert the desired
            # R-space step back into L-space (x flips, y/z hold).
            step_dir = mirror_x(step_dir)
        trial = cur - step_dir * NUDGE_STEP
        before = {name: passed(held) for name, _, held in states}
        after = {name: passed(held) for name, _, held in states_of(trial)}
        if any(before[name] and not after[name] for name in before):
            break
        cur = trial
        moved += 1
    if moved:
        cautions.append(f"{label} nudged {moved} steps to {[round(v, 4) for v in cur]}")
    return cur, moved


def snapshot_bones(bones, names):
    """Capture rest heads, tails, directions, and local z-axes for named bones."""
    snap = {}
    for name in names:
        if name not in bones:
            fail(f"preset metarig missing required bone {name!r}")
        b = bones[name]
        snap[name] = {
            "head": b.head_local.copy(),
            "tail": b.tail_local.copy(),
            "dir": (b.tail_local - b.head_local).normalized(),
            "z_axis": b.matrix_local.col[2].to_3d().normalized(),
        }
    return snap


def extract_joint_positions(bones, edit, mapping):
    """Read (head/tail) positions for a mapping of {label: (bone_name, end)}."""
    end_attrs = (
        {"head": "head", "tail": "tail"}
        if edit
        else {"head": "head_local", "tail": "tail_local"}
    )
    return {
        label: getattr(bones[bone], end_attrs[end])
        for label, (bone, end) in mapping.items()
    }


def _aim_bone_and_children(hp, align_roll, obj, bones, snap, name, head_t, tail_t):
    """Align a single edit bone from head_t to tail_t with rigid children."""
    sub = hp.descendants(bones, name)
    hp.apply(bones, sub, Matrix.Translation(head_t - bones[name].head))
    cur = bones[name].tail - bones[name].head
    want = tail_t - head_t
    if cur.length < 1e-9 or want.length < 1e-9:
        fail(f"degenerate solve for {name}")
    rot = cur.rotation_difference(want).to_matrix().to_4x4()
    hp.apply(bones, sub, hp.about(head_t, rot))
    residual = tail_t - bones[name].tail
    bones[name].tail = tail_t
    hp.apply(bones, [n for n in sub if n != name], Matrix.Translation(residual))
    new_dir = (bones[name].tail - bones[name].head).normalized()
    angle_deg = math.degrees(snap[name]["dir"].angle(new_dir))
    realigned = False
    if angle_deg > ROLL_RECOMPUTE_DEG:
        align_roll(obj, name, snap[name]["z_axis"])
        realigned = True
    return angle_deg, realigned


def solve_bone(hp, align_roll, obj, snap, name, head_t, tail_t):
    """Root-outward driver solve: head on target, tail aimed then set.

    Children rigid-follow (rotation about the head, then the length
    residual); roll is preserved unless the direction changed by more
    than ROLL_RECOMPUTE_DEG, in which case the bone's z-axis is
    realigned to its pre-fit world orientation.
    """
    return _aim_bone_and_children(
        hp, align_roll, obj, obj.data.edit_bones, snap, name, head_t, tail_t
    )


def solve_chain(hp, obj, snap, chain, points):
    """Lay a connected chain onto target points (root outward).

    Each bone's head lands on its point, tail on the next; children
    rigid-follow; rolls preserved unless the direction changed past
    ROLL_RECOMPUTE_DEG (same rule as solve_bone). Returns {bone: deg}.
    """
    from rigforge.utils.bones import align_bone_z_axis

    bones = obj.data.edit_bones
    angles = {}
    # points carries N+1 entries for N bones by design (shared joints).
    for name, head_t, tail_t in zip(chain, points, points[1:], strict=False):
        deg, _ = _aim_bone_and_children(
            hp, align_bone_z_axis, obj, bones, snap, name, head_t, tail_t
        )
        angles[name] = deg
    return angles


def mirror_bones(bones, names):
    """Copy solved .L bones onto .R (x negated, roll negated)."""
    for name in names:
        if not name.endswith(".L"):
            fail(f"cannot mirror non-paired bone {name}")
        rname = name[:-2] + ".R"
        if rname not in bones:
            fail(f"missing mirror bone {rname}")
        src, dst = bones[name], bones[rname]
        dst.head = (-src.head.x, src.head.y, src.head.z)
        dst.tail = (-src.tail.x, src.tail.y, src.tail.z)
        dst.roll = -src.roll


def mirror_subtree(hp, bones, root_L):
    """Copy a solved .L subtree onto .R (x negated, roll negated)."""
    mirror_bones(bones, hp.descendants(bones, root_L))


def chain_fractions(bones, chain):
    """Preset length fraction per bone over the chain's total length."""
    lens = [(bones[n].tail - bones[n].head).length for n in chain]
    total = sum(lens)
    if total <= 1e-9:
        fail(f"chain {[n for n in chain]} collapsed")
    return [L / total for L in lens]


def _renorm(fracs):
    total = sum(fracs)
    if total <= 1e-9:
        fail("zero total fraction in chain interpolation")
    return [f / total for f in fracs]


def lerp_points(start, end, fracs):
    """Chain points from start to end splitting by preset fractions."""
    pts, acc = [start], 0.0
    for f in fracs[:-1]:
        acc += f
        pts.append(start + (end - start) * acc)
    pts.append(end)
    return pts


def emit_module(obj, out_path, mesh_path, lm_path, preset="biped_hero"):
    """Write a generated Python metarig module for `obj` to `out_path`."""
    from rigforge.utils.rig import write_metarig

    code = write_metarig(
        obj, layers=True, func_name="create", groups=True, widgets=True
    )
    header = (
        "# SPDX-License-Identifier: GPL-2.0-or-later\n"
        f"# Fitted metarig, generated by tools/fit_metarig.py from {preset}\n"
        f"# Subject: {os.path.basename(mesh_path)} | landmarks: "
        f"{os.path.basename(lm_path)} — do not hand-edit, re-fit instead.\n\n"
    )
    compile(header + code, out_path, "exec")  # emitted module must parse
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + code + "\n")
    return out_path


def check_closure(obj, drivers=DRIVERS, extra=("spine.004", "face")):
    """Verify connected bones remain joint-closed; report unconnected drivers."""
    bones = obj.data.edit_bones
    max_gap, open_drivers = 0.0, []
    for b in bones:
        if b.parent is None:
            continue
        gap = (b.head - b.parent.tail).length
        if b.use_connect:
            max_gap = max(max_gap, gap)
            if gap >= CLOSE_EPS:
                fail(f"joint left open: {b.name} gap={gap:.6f}")
        elif b.name in drivers or b.name in extra:
            open_drivers.append(b.name)
    return max_gap, sorted(open_drivers)


def check_symmetry(bones):
    """Verify .L/.R bone heads and tails mirror across X within SYMMETRY_TOL."""
    worst = 0.0
    for b in bones:
        if not b.name.endswith(".L"):
            continue
        other = bones.get(b.name[:-2] + ".R")
        if other is None:
            continue
        worst = max(
            worst,
            abs(b.head.x + other.head.x),
            abs(b.head.y - other.head.y),
            abs(b.head.z - other.head.z),
            abs(b.tail.x + other.tail.x),
            abs(b.tail.y - other.tail.y),
            abs(b.tail.z - other.tail.z),
        )
    if worst >= SYMMETRY_TOL:
        fail(f"symmetry error {worst:.4f} exceeds {SYMMETRY_TOL}")
    return worst


def run_generate(obj):
    """In-session headless generate; returns the generate proof dict."""
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    bpy.ops.pose.rigforge_generate()
    rig = next(
        (o for o in bpy.data.objects if o.type == "ARMATURE" and "rig_id" in o.data),
        None,
    )
    if rig is None:
        fail("generate produced no rig")
    n_def = sum(1 for b in rig.data.bones if b.name.startswith("DEF-"))
    print(f"generate: rig bones={len(rig.data.bones)} def={n_def}")
    if n_def <= 30:
        fail(f"only {n_def} DEF bones")
    print("RIGFORGE_FIT_GENERATE_OK")
    return {"rig_bones": len(rig.data.bones), "def_bones": n_def}


def hint_target_overrides(uh, lm, hints_path, rotated):
    """Arbitrate a unirig-hints doc against landmarks; CLI + operator share.

    Returns (hints_report, overrides): overrides maps target roles to
    trusted Vector positions; the caller applies the roles it knows.
    """
    report = uh.arbitrate(uh.load_hints(hints_path), lm, rotated)
    overrides = {role: Vector(pos) for role, pos in report["trusted"].items()}
    return report, overrides
