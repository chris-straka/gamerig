# SPDX-License-Identifier: GPL-2.0-or-later
"""Quadruped preset fitting: connected chain solving and chest/spine insets."""

import json

import bpy
from mathutils import Matrix, Vector

from . import detect_landmarks as _detect
from . import make_presets as _presets
from . import unirig_hints as _hints
from . import validate_fit as _validate
from .fit_common import (
    ROLL_RECOMPUTE_DEG,
    _renorm,
    arg_value,
    chain_fractions,
    check_closure,
    check_symmetry,
    emit_module,
    eval_envelope,
    extract_joint_positions,
    fail,
    hint_target_overrides,
    lerp_points,
    mirror_bones,
    mirror_subtree,
    run_generate,
    snapshot_bones,
    solve_chain,
)

STALKER_SPINE = (
    "spine.001",
    "spine.002",
    "spine.003",
    "spine.004",
    "spine.005",
    "spine.006",
)
STALKER_NECK = ("neck.001", "neck.002", "neck.003", "neck.004", "head")
STALKER_FRONT_LEG = (
    "upper_arm.L",
    "forearm.L",
    "forefoot.L",
    "f_toe.L",
    "f_hoof.L",
)
STALKER_REAR_LEG = (
    "thigh.L",
    "lower_leg.L",
    "hind_foot.L",
    "r_toe.L",
    "r_hoof.L",
)
STALKER_TAIL = ("tail.001", "tail.002", "tail.003", "tail.004", "tail.005")
STALKER_DRIVERS = (
    "upper_arm.L",
    "forearm.L",
    "forefoot.L",
    "thigh.L",
    "lower_leg.L",
    "hind_foot.L",
)
STALKER_CLOSURE_DRIVERS = (
    *STALKER_SPINE,
    *STALKER_NECK,
    "shoulder.L",
    "shoulder.R",
    "pelvis.L",
    "pelvis.R",
    "upper_arm.L",
    "upper_arm.R",
    "forearm.L",
    "forearm.R",
    "forefoot.L",
    "forefoot.R",
    "thigh.L",
    "thigh.R",
    "lower_leg.L",
    "lower_leg.R",
    "hind_foot.L",
    "hind_foot.R",
    *STALKER_TAIL,
)

STALKER_JOINT_MAP = {
    "spine_rear": ("spine.001", "head"),
    "spine_front": ("spine.006", "tail"),
    # neck_base lands mid-chain (neck.002.tail); neck.001.head plants
    # on the chest top (spine_front) by the connected-chain design.
    "neck_base": ("neck.002", "tail"),
    "head": ("head", "head"),
    "skull": ("head", "tail"),
    "tail_base": ("tail.001", "head"),
    "front_top": ("upper_arm.L", "head"),
    "front_mid": ("upper_arm.L", "tail"),
    "front_foot": ("forearm.L", "tail"),
    "front_toe": ("forefoot.L", "tail"),
    "rear_top": ("thigh.L", "head"),
    "rear_mid": ("thigh.L", "tail"),
    "rear_foot": ("lower_leg.L", "tail"),
    "rear_toe": ("hind_foot.L", "tail"),
}

SPINE_END_NEED = 0.015  # inset march: endpoint depth target (gate + headroom)
SPINE_END_MAX = 0.05  # inset march bound
SPINE_END_STEP = 0.01
TOE_SPAN_MIN = 0.01  # below: stub too small to aim, rigid-follow instead
TAIL_SPAN_MIN = 0.03  # below: rigid-follow the tail instead of solving


def stalker_targets(lm):
    """Quadruped landmark JSON -> driver-joint targets (all measured 3D)."""
    if lm.get("kind") != "quadruped":
        fail(f"stalker fit needs kind=quadruped landmarks, got {lm.get('kind')!r}")
    if lm["facing"] not in ("-Y", "+Y"):
        fail(f"unsupported facing {lm['facing']!r}")
    for key in (
        "spine_front",
        "spine_rear",
        "neck_base",
        "head",
        "skull",
        "tail_base",
        "legs",
    ):
        if lm.get(key) is None:
            fail(f"landmark JSON missing {key}")
    for leg in ("FL", "BL"):
        if leg not in lm["legs"]:
            fail(f"landmark JSON legs missing {leg} (need FL/BL, .R mirrors)")
        for joint in ("top", "mid", "foot", "toe"):
            if lm["legs"][leg].get(joint) is None:
                fail(f"landmark JSON legs.{leg} missing {joint}")

    def v(p):
        return Vector(p)

    return {
        "spine_rear": v(lm["spine_rear"]),
        "spine_front": v(lm["spine_front"]),
        "neck_base": v(lm["neck_base"]),
        "head": v(lm["head"]),
        "skull": v(lm["skull"]),
        "tail_base": v(lm["tail_base"]),
        "tail_tip": v(lm["tail_tip"]) if lm.get("tail_tip") is not None else None,
        "tail_fallback": bool(lm.get("tail_base_fallback")),
        "front_top": v(lm["legs"]["FL"]["top"]),
        "front_mid": v(lm["legs"]["FL"]["mid"]),
        "front_foot": v(lm["legs"]["FL"]["foot"]),
        "front_toe": v(lm["legs"]["FL"]["toe"]),
        "rear_top": v(lm["legs"]["BL"]["top"]),
        "rear_mid": v(lm["legs"]["BL"]["mid"]),
        "rear_foot": v(lm["legs"]["BL"]["foot"]),
        "rear_toe": v(lm["legs"]["BL"]["toe"]),
    }


def snapshot_stalker_preset(hp, obj):
    """Preset measures in OBJECT mode + per-bone dirs for roll checks."""
    bones = obj.data.bones
    head = bones["spine.001"].head_local
    tail = bones["spine.006"].tail_local
    preset = {"spine_len": (tail - head).length}
    names = (
        *STALKER_SPINE,
        *STALKER_NECK,
        *STALKER_FRONT_LEG,
        *STALKER_REAR_LEG,
        *STALKER_TAIL,
    )
    return preset, snapshot_bones(bones, names)


def stalker_joint_positions(bones, edit):
    return extract_joint_positions(bones, edit, STALKER_JOINT_MAP)


def _deep_enough(rv, eval_env, pt, need):
    held = rv.point_depth(eval_env, pt)
    return held["inside"] and held["depth"] >= need


def inset_spine_end(rv, env, point, inward, cautions, label):
    """March a spine endpoint inward until it sits 15 mm deep (bounded)."""
    eval_env = eval_envelope(env)
    cur, moved = point.copy(), 0.0
    while moved < SPINE_END_MAX:
        if _deep_enough(rv, eval_env, cur, SPINE_END_NEED):
            break
        cur += inward * SPINE_END_STEP
        moved += SPINE_END_STEP
    if not _deep_enough(rv, eval_env, cur, SPINE_END_NEED):
        cautions.append(f"{label} inset exhausted, measured kept")
        return point
    if moved:
        cautions.append(f"{label} inset {moved * 100:.0f} cm for depth")
    return cur


def inset_chest_top(rv, env, front, neck_base, frac0, inward, cautions):
    """March the chest top back until the neck.001 midpoint passes."""
    eval_env = eval_envelope(env)
    cur, moved = front.copy(), 0.0
    while moved < SPINE_END_MAX:
        seg = neck_base - cur
        mid = cur + seg * (frac0 / 2)
        if _deep_enough(rv, eval_env, cur, SPINE_END_NEED) and _deep_enough(
            rv, eval_env, mid, rv.MARGIN_DEFAULT + 0.002
        ):
            break
        cur += inward * SPINE_END_STEP
        moved += SPINE_END_STEP
    seg = neck_base - cur
    mid = cur + seg * (frac0 / 2)
    if not _deep_enough(rv, eval_env, mid, rv.MARGIN_DEFAULT + 0.002):
        cautions.append("spine_front inset exhausted, measured kept")
        return front
    if moved:
        cautions.append(f"spine_front inset {moved * 100:.0f} cm for depth")
    return cur


def fit_stalker(hp, obj, targets, s, snap, cautions):
    """Root-outward stalker solve onto measured targets. Returns angles."""
    bpy.ops.object.mode_set(mode="EDIT")
    bones = obj.data.edit_bones
    for b in bones:  # as in make_presets: no connected dragging
        b.use_connect = False
    hp.apply(bones, [b.name for b in bones], Matrix.Scale(s, 4))  # coarse fit

    angles = {}
    fracs = chain_fractions(bones, STALKER_SPINE)
    angles.update(
        solve_chain(
            hp,
            obj,
            snap,
            STALKER_SPINE,
            lerp_points(targets["spine_rear"], targets["spine_front"], fracs),
        )
    )
    nfracs = chain_fractions(bones, STALKER_NECK[:4])
    seg1 = lerp_points(
        targets["spine_front"], targets["neck_base"], _renorm(nfracs[:2])
    )
    seg2 = lerp_points(targets["neck_base"], targets["head"], _renorm(nfracs[2:]))
    neck_pts = [seg1[0], seg1[1], seg1[2], seg2[1], seg2[2], targets["skull"]]
    angles.update(solve_chain(hp, obj, snap, STALKER_NECK, neck_pts))
    for root, chain, keys in (
        (
            "shoulder.L",
            STALKER_FRONT_LEG[:3],
            ("front_top", "front_mid", "front_foot", "front_toe"),
        ),
        (
            "pelvis.L",
            STALKER_REAR_LEG[:3],
            ("rear_top", "rear_mid", "rear_foot", "rear_toe"),
        ),
    ):
        hp.apply(
            bones,
            hp.descendants(bones, root),
            Matrix.Translation(targets[keys[0]] - bones[chain[0]].head),
        )
        angles.update(solve_chain(hp, obj, snap, chain, [targets[k] for k in keys]))
    for chain, foot_k, toe_k in (
        (("f_toe.L", "f_hoof.L"), "front_foot", "front_toe"),
        (("r_toe.L", "r_hoof.L"), "rear_foot", "rear_toe"),
    ):
        span = targets[foot_k] - targets[toe_k]
        if span.length < TOE_SPAN_MIN:
            cautions.append(f"{chain[0]} span tiny, rigid follow")
            continue
        fracs = _renorm(chain_fractions(bones, chain))
        pts = lerp_points(targets[toe_k], targets[foot_k], fracs)
        angles.update(solve_chain(hp, obj, snap, chain, pts))
    tail_tip = targets.get("tail_tip")
    tail_span = (
        (tail_tip - targets["tail_base"]).length if tail_tip is not None else 0.0
    )
    if tail_span >= TAIL_SPAN_MIN:
        fracs = chain_fractions(bones, STALKER_TAIL)
        angles.update(
            solve_chain(
                hp,
                obj,
                snap,
                STALKER_TAIL,
                lerp_points(targets["tail_base"], tail_tip, fracs),
            )
        )
    else:
        hp.apply(
            bones,
            hp.descendants(bones, "tail.001"),
            Matrix.Translation(targets["tail_base"] - bones["tail.001"].head),
        )
        if not targets["tail_fallback"]:
            cautions.append("tail span short, rigid follow")
    mirror_subtree(hp, bones, "shoulder.L")
    mirror_subtree(hp, bones, "pelvis.L")
    mirror_bones(bones, ["breast.L"])
    hp.reconnect_from(obj, "rigforge_quadruped_stalker_metarig_add")
    return angles


def fit_stalker_object(hp, rv, obj, lm, env, targets, cautions):
    """Solve the quadruped_stalker metarig `obj` onto targets; returns fit record."""
    preset, snap = snapshot_stalker_preset(hp, obj)
    spine_axis = targets["spine_front"] - targets["spine_rear"]
    if spine_axis.length < 1e-9:
        fail("spine endpoints coincide")
    spine_axis.normalize()
    neck_frac0 = (snap["neck.001"]["tail"] - snap["neck.001"]["head"]).length / (
        (snap["neck.001"]["tail"] - snap["neck.001"]["head"]).length
        + (snap["neck.002"]["tail"] - snap["neck.002"]["head"]).length
    )
    targets["spine_front"] = inset_chest_top(
        rv,
        env,
        targets["spine_front"],
        targets["neck_base"],
        neck_frac0,
        -spine_axis,
        cautions,
    )
    targets["spine_rear"] = inset_spine_end(
        rv, env, targets["spine_rear"], spine_axis, cautions, "spine_rear"
    )

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = obj
    s = (targets["spine_front"] - targets["spine_rear"]).length / preset["spine_len"]
    before = {
        k: v * s for k, v in stalker_joint_positions(obj.data.bones, False).items()
    }
    if targets["tail_fallback"]:
        cautions.append("tail_base fallback: no tail geometry, rigid follow")

    angles = fit_stalker(hp, obj, targets, s, snap, cautions)
    realigned_bones = [b for b in angles if angles[b] > ROLL_RECOMPUTE_DEG]
    max_gap, open_drivers = check_closure(
        obj, drivers=STALKER_CLOSURE_DRIVERS, extra=()
    )
    bones = obj.data.edit_bones  # refetch: reconnect_from switched modes
    symmetry = check_symmetry(bones)
    after = stalker_joint_positions(bones, True)
    joints = {}
    for key, tgt in targets.items():
        if key in ("tail_fallback", "tail_tip"):
            continue
        joints[key] = {
            "target": [round(v, 4) for v in tgt],
            "before_d": round((before[key] - tgt).length, 4),
            "after_d": round((after[key] - tgt).length, 4),
        }
    lengths = {}
    for bone in STALKER_DRIVERS:
        old = (snap[bone]["tail"] - snap[bone]["head"]).length * s
        new = (bones[bone].tail - bones[bone].head).length
        lengths[bone] = round(100 * (new - old) / old, 1)
    return {
        "scale": s,
        "joints": joints,
        "length_change_pct": lengths,
        "roll_realigned": sorted(realigned_bones),
        "closure": {"max_gap": max_gap, "open_drivers": open_drivers},
        "symmetry_max": symmetry,
    }


def main_stalker(
    args,
    mesh_path,
    lm_path,
    lm,
    out_path,
    blend_path,
    want_generate,
    dl=_detect,
    hp=_presets,
    uh=_hints,
    rv=_validate,
):
    """Stalker fit flow: measured 3D targets, connected-chain solve."""
    try:
        bpy.ops.preferences.addon_enable(module="rigforge")
    except RuntimeError:
        fail("rigforge addon not found; set BLENDER_USER_SCRIPTS overlay")
    import rigforge

    print("rigforge:", rigforge.__file__)

    targets = stalker_targets(lm)
    hints_report = None
    if "--hints" in args:
        hints_report, overrides = hint_target_overrides(
            uh, lm, arg_value(args, "--hints"), "--hints-rotated" in args
        )
        for role, pos in overrides.items():
            if role in targets:
                targets[role] = pos
        n_trust = len(hints_report["trusted"])
        n_sup = len(hints_report["supporting"])
        n_div = len(hints_report["diverged"])
        print(f"hints: {n_trust} trusted, {n_sup} supporting, {n_div} measurement-wins")

    subject = dl.import_mesh(mesh_path)
    env = dl.envelope_copy(subject)

    cautions = []
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.rigforge_quadruped_stalker_metarig_add()
    obj = bpy.context.view_layer.objects.active
    fit = fit_stalker_object(hp, rv, obj, lm, env, targets, cautions)

    emit_module(obj, out_path, mesh_path, lm_path, preset="quadruped_stalker")
    generate = run_generate(obj) if want_generate else None
    if blend_path:
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    report = {
        "preset": "quadruped_stalker",
        "subject": mesh_path,
        "facing": lm["facing"],
        "length": lm["length"],
        "scale": round(fit["scale"], 4),
        "cautions": cautions,
        "hints": hints_report,
        "joints": fit["joints"],
        "length_change_pct": fit["length_change_pct"],
        "roll_realigned": fit["roll_realigned"],
        "closure": {
            "max_gap": round(fit["closure"]["max_gap"], 6),
            "open_drivers": fit["closure"]["open_drivers"],
        },
        "symmetry_max": round(fit["symmetry_max"], 6),
        "emit": out_path,
        "generate": generate,
    }
    print("RIGFORGE_FIT_BEGIN")
    print(json.dumps(report, indent=2))
    print("RIGFORGE_FIT_OK")
