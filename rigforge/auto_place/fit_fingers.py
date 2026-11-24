# SPDX-License-Identifier: GPL-2.0-or-later
"""Finger-column detection and verify-then-correct finger chain fitting."""

import math

import bpy
from mathutils import Matrix, Quaternion, Vector

from .fit_common import eval_envelope, mirror_bones

FINGER_CHAINS = ("f_index", "f_middle", "f_ring", "f_pinky")
FINGER_SLAB_MEDIAL = 0.03  # finger slab: wrist_x - this (thigh side)
FINGER_SLAB_LATERAL = 0.08  # finger slab: wrist_x + this (splay side)
FINGER_SLAB_MIN_N = 50
THUMB_MASK_R = 0.015  # slab verts nearer the legacy thumb are thumb flesh
FINGER_TIP_MIN_DEPTH = 0.001  # accept a fingertip 1 mm+ inside (no grazes)
FINGER_CAP_DEG = 60.0  # per-finger re-aim rotation cap about the knuckle


def finger_row_axis(tips):
    """Row axis of the legacy fan: 'x' or 'y', whichever the tips span."""
    xs = [t.x for t in tips.values()]
    ys = [t.y for t in tips.values()]
    return "x" if max(xs) - min(xs) > max(ys) - min(ys) else "y"


def dist_to_seg(p, a, b):
    """Distance from point p to segment a-b (thumb-mask helper)."""
    ab = b - a
    denom = ab.length_squared
    t = (p - a).dot(ab) / denom if denom > 1e-12 else 0.0
    return (p - (a + ab * max(0.0, min(1.0, t)))).length


def mesh_finger_columns(rv, env, lm, tips, thumb_segs):
    """Per-finger mesh columns from a thigh-safe hand slab (+X side).

    The gate's wide slab (|x - wrist_x| < 0.08) catches the thigh when
    the hand rests against it; the medial bound stays tight (-0.03,
    thigh side) while the lateral bound matches the gate (+0.08, splay
    side), and verts near the legacy thumb chains are masked (the thumb
    would own the edge quartile). The slab is quartile-split along the
    fan's row axis
    and each quartile yields a centroid-split dir; the anchor is the
    bottom-quartile-z centroid (below the webbing, on fingertip flesh).
    Quartiles assign to fingers in legacy-tip order along the row.
    The anchor is the lowest flesh vertex + 5 mm (a centroid would land
    between splayed tips); quartiles whose lowest verts are all outside
    flesh fall back to the centroid. Returns {finger: (dir, anchor)}
    with unmeasurable fingers omitted.
    """
    floor = lm["hand_tip_z"] - 0.02
    fingers_roof = (lm["wrist_z"] + lm["hand_tip_z"]) / 2

    def centroid(vs):
        return Vector(
            (
                sum(v.x for v in vs) / len(vs),
                sum(v.y for v in vs) / len(vs),
                sum(v.z for v in vs) / len(vs),
            )
        )

    def column(part):
        zs = sorted(v.z for v in part)
        cut = zs[len(zs) // 2]
        up = [v for v in part if v.z >= cut]
        lo = [v for v in part if v.z < cut]
        if not up or not lo:
            return None
        vec = centroid(lo) - centroid(up)
        if vec.length < 1e-9 or abs(vec.normalized().z) < 0.5:
            return None
        if vec.normalized().z > 0:
            return None
        return vec.normalized(), centroid(lo)

    slab = [
        v.co
        for v in env.data.vertices
        if floor < v.co.z < lm["wrist_z"]
        and -FINGER_SLAB_MEDIAL < v.co.x - lm["wrist_x"] < FINGER_SLAB_LATERAL
        and all(dist_to_seg(v.co, a, b) >= THUMB_MASK_R for a, b in thumb_segs)
    ]
    if len(slab) < FINGER_SLAB_MIN_N:
        return {}, f"only {len(slab)} hand-slab verts"
    # Direction from the full slab (long span averages splay out);
    # anchor from the bottom-z quartile flesh (below the webbing).
    low = [v for v in slab if v.z < fingers_roof]
    axis = finger_row_axis(tips)
    key = (lambda v: v.x) if axis == "x" else (lambda v: v.y)
    fingers = sorted(tips, key=lambda f: key(tips[f]))
    ordered, ordered_low = sorted(slab, key=key), sorted(low, key=key)
    n, n_low = len(ordered), len(ordered_low)
    out = {}
    for rank, finger in enumerate(fingers):
        part = ordered[rank * n // 4 : (rank + 1) * n // 4]
        part_low = ordered_low[rank * n_low // 4 : (rank + 1) * n_low // 4]
        if len(part) < FINGER_SLAB_MIN_N // 4 or not part_low:
            continue
        full = column(part)
        if full is None:
            continue
        direction, _ = full
        eval_env = eval_envelope(env)
        anchor = None
        for v in sorted(part_low, key=lambda v: v.z)[:3]:
            cand = v + Vector((0.0, 0.0, 0.005))
            if rv.point_depth(eval_env, cand)["inside"]:
                anchor = cand
                break
        if anchor is None:
            deep = sorted(part_low, key=lambda v: v.z)[:3]
            base = centroid(deep)
            anchor = base + Vector((0.0, 0.0, 0.005))
        out[finger] = (direction, anchor)
    if not out:
        return {}, "no measurable finger quartile"
    return out, None


def _shift_chain(chain, delta):
    for b in chain:
        b.head += delta
        b.tail += delta


def _tip_inside(rv, eval_env, mat, tail_b):
    held = rv.point_depth(eval_env, mat @ tail_b.tail)
    return held["inside"] and held["depth"] >= FINGER_TIP_MIN_DEPTH


def _seat_finger_chain(rv, eval_env, mat, lm, chain, pivot, mesh_dir, anchor, floor):
    """Scale, translate onto column, and search for clean inside fingertip."""
    if rv.point_depth(eval_env, anchor)["inside"]:
        pt, last = anchor, None
        for _ in range(30):
            pt = pt + mesh_dir * 0.0015
            if pt.z < floor or not rv.point_depth(eval_env, pt)["inside"]:
                break
            last = pt
        if last is not None:
            target = last - mesh_dir * 0.004
            tip = chain[2].tail.copy()
            if abs(pivot.z - tip.z) > 1e-6:
                factor = (pivot.z - target.z) / (pivot.z - tip.z)
                if 0.5 <= factor <= 1.5:
                    for b in chain:
                        b.head = pivot + (b.head - pivot) * factor
                        b.tail = pivot + (b.tail - pivot) * factor
    tip = chain[2].tail.copy()
    if floor < tip.z < lm["wrist_z"]:
        dest = anchor + mesh_dir * ((tip.z - anchor.z) / mesh_dir.z)
        shift = dest - tip
        if shift.length > 0.05:
            shift = shift.normalized() * 0.05
        _shift_chain(chain, shift)
    if _tip_inside(rv, eval_env, mat, chain[2]):
        return

    top = min(anchor.z + 0.03, lm["wrist_z"] - 0.01)
    climbed, found = 0, False
    for _ in range(30):
        _shift_chain(chain, -mesh_dir * 0.0015)
        climbed += 1
        if chain[2].tail.z > top:
            break
        if _tip_inside(rv, eval_env, mat, chain[2]):
            found = True
            break
    if not found:
        _shift_chain(chain, mesh_dir * (0.0015 * climbed))
    if _tip_inside(rv, eval_env, mat, chain[2]):
        return

    # Lateral centering for curving caps (the column x/y grazes).
    lateral = [
        (dx, dy)
        for r in (0.001, 0.002, 0.003)
        for dx, dy in (
            (r, 0.0),
            (-r, 0.0),
            (0.0, r),
            (0.0, -r),
            (r, r),
            (r, -r),
            (-r, r),
            (-r, -r),
        )
    ]
    for dx, dy in lateral:
        trial = Vector((dx, dy, 0.0))
        _shift_chain(chain, trial)
        if _tip_inside(rv, eval_env, mat, chain[2]):
            return
        _shift_chain(chain, -trial)
    for dist in (0.003, 0.006, 0.009, 0.012):
        trial = mesh_dir * dist
        _shift_chain(chain, trial)
        if _tip_inside(rv, eval_env, mat, chain[2]):
            return
        _shift_chain(chain, -trial)


def fit_fingers(rv, env, lm, obj, cautions):
    """Re-aim .L finger chains onto the mesh finger columns; mirror to .R.

    Rigid follow keeps the preset fan angle while knuckles sit off the
    mesh; each chain rotates about its knuckle toward its own measured
    mesh column (capped), scales with the shared hand factor, and shifts
    its tip onto its column. Per-finger revert on failed verify (tip
    inside + gate angle under the warn line), so a bad measurement
    leaves the legacy fan in place.
    """
    gate_dir = rv.mesh_finger_dir(env, lm, "L")
    gate_dir_r = rv.mesh_finger_dir(env, lm, "R")
    eval_env = eval_envelope(env)
    mat = obj.matrix_world
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bones = obj.data.edit_bones
    floor = lm["hand_tip_z"] - 0.02
    avail = {}
    for finger in FINGER_CHAINS:
        names = [f"{finger}.{mid}.L" for mid in ("01", "02", "03")]
        chain = [bones.get(n) for n in names]
        if any(b is None for b in chain):
            continue
        if (chain[2].tail - chain[0].head).length < 1e-6:
            continue
        avail[finger] = (names, chain)
    if not avail:
        return []

    def chain_quiet(gdir, head_b, tail_b):
        if gdir is None or head_b is None or tail_b is None:
            return False
        deg = rv.chain_angle(gdir, head_b, tail_b)
        return (
            rv.point_depth(eval_env, mat @ tail_b.tail)["inside"]
            and deg is not None
            and deg < rv.FINGER_WARN_DEG
        )

    if all(
        chain_quiet(gate_dir, chain[0], chain[2])
        and chain_quiet(
            gate_dir_r,
            bones.get(names[0][:-2] + ".R"),
            bones.get(names[2][:-2] + ".R"),
        )
        for names, chain in avail.values()
    ):
        return []
    tips = {f: chain[2].tail.copy() for f, (_, chain) in avail.items()}
    thumb_segs = []
    for mid in ("01", "02", "03"):
        tb = bones.get(f"thumb.{mid}.L")
        if tb is not None:
            thumb_segs.append((tb.head.copy(), tb.tail.copy()))
    cols, reason = mesh_finger_columns(rv, env, lm, tips, thumb_segs)
    if not cols:
        cautions.append(f"finger fit skipped: {reason}")
        return []

    chains = {}
    saved = {}
    cap = math.radians(FINGER_CAP_DEG)
    for finger, (names, chain) in avail.items():
        if finger not in cols:
            continue
        mesh_dir, _anchor = cols[finger]
        head_b, tail_b = chain[0], chain[2]
        vec = tail_b.tail - head_b.head
        rot = vec.normalized().rotation_difference(mesh_dir)
        if rot.angle > cap:
            rot = Quaternion(rot.axis, cap)
        pivot = head_b.head.copy()
        move = (
            Matrix.Translation(pivot)
            @ rot.to_matrix().to_4x4()
            @ Matrix.Translation(-pivot)
        )
        chains[finger] = (names, chain, pivot)
        saved[finger] = [(b.head.copy(), b.tail.copy()) for b in chain]
        for b in chain:
            b.head = move @ b.head
            b.tail = move @ b.tail
    if not chains:
        return []

    for finger, (_, chain, pivot) in chains.items():
        mesh_dir, anchor = cols[finger]
        _seat_finger_chain(rv, eval_env, mat, lm, chain, pivot, mesh_dir, anchor, floor)

    fixed = []
    for finger, (names, chain, _) in chains.items():
        rnames = [n[:-2] + ".R" for n in names]
        rchain = [bones.get(n) for n in rnames]
        saved_r = [(b.head.copy(), b.tail.copy()) for b in rchain if b is not None]
        mirror_bones(bones, names)
        head_b, tail_b = chain[0], chain[2]
        deg = rv.chain_angle(gate_dir, head_b, tail_b) if gate_dir else None
        deg_r = rv.chain_angle(gate_dir_r, rchain[0], rchain[2]) if gate_dir_r else None
        if (
            _tip_inside(rv, eval_env, mat, tail_b)
            and deg is not None
            and deg < rv.FINGER_WARN_DEG
            and _tip_inside(rv, eval_env, mat, rchain[2])
            and deg_r is not None
            and deg_r < rv.FINGER_WARN_DEG
        ):
            fixed.append(finger)
        else:
            for b, (h, t) in zip(chain, saved[finger], strict=True):
                b.head, b.tail = h, t
            for b, (h, t) in zip(rchain, saved_r, strict=True):
                b.head, b.tail = h, t
    if fixed:
        cautions.append(f"fingers re-aimed: {','.join(fixed)}")
    return fixed
