# SPDX-License-Identifier: GPL-2.0-or-later
"""Biped preset fitting: axis tracking, target resolution, spine, and limbs."""

import bpy
from mathutils import Matrix, Vector

from .fit_common import (
    DRIVERS,
    ROLL_RECOMPUTE_DEG,
    check_closure,
    check_symmetry,
    eval_envelope,
    extract_joint_positions,
    fail,
    fit_line,
    mid_state,
    mirror_subtree,
    mirror_x,
    nudge_target,
    snapshot_bones,
    solve_bone,
)
from .fit_face import fit_face, measure_head_box
from .fit_fingers import fit_fingers

ANKLE_DIP_MIN = 0.010  # below: boot hides the narrowing -> sole offset
ANKLE_MAX_Z_FRAC = 0.085  # above: dip is mid-boot, not the joint
ANKLE_SOLE_FRAC = 0.048  # ankle joint above the sole (~8 cm at 1.7 m)
WRIST_DIP_MIN = 0.004  # below: gauntlet hides wrist -> hand_tip + palm
ELBOW_CAUTION_DIP = 0.005  # below: keep the dip, but log a caution
LEG_LINE_MIN_Z_FRAC = 0.11  # leg axis fits above the foot flare
MIN_LINE_BANDS = 6
MIN_BLOB_N = 12  # outer-blob crossing count below: speck, not a limb
MIN_BLOB_R = 0.012  # outer-blob radius below: speck, not a limb
ELBOW_OVERRIDE_MIN = 0.005  # adopt the re-derived elbow above this delta
UPPER_RAY_MIN_BANDS = 4  # bands above the elbow needed for the ray
SHOULDER_MARCH_MIN = 0.4  # back-projection shortens to at most this frac
SHOULDER_MARCH_STEP = 0.05
FOOT_TOP_MARGIN = 0.025  # ankle dip above forefoot top + this: not a joint
HAND_SPAN_FRAC = 0.75  # mesh-fit hand tail: knuckles above the tip floor
HAND_COL_MIN_BANDS = 3  # finger-column bands needed for the hand fit

BIPED_JOINT_MAP = {
    "shoulder": ("upper_arm.L", "head"),
    "elbow": ("upper_arm.L", "tail"),
    "wrist": ("forearm.L", "tail"),
    "hand": ("hand.L", "tail"),
    "hip": ("thigh.L", "head"),
    "knee": ("thigh.L", "tail"),
    "ankle": ("shin.L", "tail"),
    "foot": ("foot.L", "tail"),
}


def limb_samples(dl, top_down, bands):
    """Per-band averaged limb centers; skips bands where a side is lost."""
    samples, asym = [], 0.0
    for i in bands:
        blobs = top_down[i]["blobs"]
        r = dl.outer_blob(blobs, 1.0)
        left = dl.outer_blob(blobs, -1.0)
        if r is None or left is None:
            continue
        z = top_down[i]["z"]
        samples.append(
            ((abs(r["cx"]) + abs(left["cx"])) / 2, (r["cy"] + left["cy"]) / 2, z)
        )
        asym = max(asym, abs(abs(r["cx"]) - abs(left["cx"])))
    return samples, asym


def valid_arm_pair(dl, blobs):
    """An outer L/R blob pair big enough to be arms (Section 9).

    Specks (ponytail splits, hair cards, fingertip remnants) read as tiny
    outer blobs; requiring crossings + radius on BOTH sides drops them
    while keeping true arm bands whatever the slice's total blob count.
    """
    r = dl.outer_blob(blobs, 1.0)
    left = dl.outer_blob(blobs, -1.0)
    return (
        r is not None
        and left is not None
        and r["n"] >= MIN_BLOB_N
        and left["n"] >= MIN_BLOB_N
        and r["r"] >= MIN_BLOB_R
        and left["r"] >= MIN_BLOB_R
    )


def robust_arm_bands(dl, top_down, arms):
    """Arm-run bands with a valid outer pair, any blob count."""
    return [i for i in arms if valid_arm_pair(dl, top_down[i]["blobs"])]


def rederive_elbow(dl, top_down, bands, wrist_z):
    """Radius-dip elbow on the robust profile (detector's guards).

    Returns the dip z, or None when the search range is too short. The
    detector's elbow is kept unless this disagrees past
    ELBOW_OVERRIDE_MIN (its band was speck-dragged: the smoothed profile
    dips at the first clean band below the noise).
    """
    in_range = [i for i in bands if top_down[i]["z"] > wrist_z + 0.08]
    if len(in_range) < 3:
        return None
    prof = []
    for i in in_range:
        blobs = top_down[i]["blobs"]
        r = dl.outer_blob(blobs, 1.0)
        left = dl.outer_blob(blobs, -1.0)
        prof.append((r["r"] + left["r"]) / 2)
    band = dl.argmin_interior(dl.smooth(prof), 0, len(in_range))
    return top_down[in_range[band]]["z"]


def track_axes(dl, mesh_path, lm):
    """Envelope remesh + slice/blob analysis; returns arm/leg axis fns."""
    return track_axes_from_subject(dl, dl.import_mesh(mesh_path), lm)


def track_axes_from_subject(dl, subject, lm):
    """track_axes for an in-session subject mesh (no import, no cleanup).

    Same analysis as the path-based entry; the caller owns `subject`
    (typically a joined duplicate) and the returned axes["env"] envelope.
    """
    target = dl.envelope_copy(subject)
    signature, _, _ = dl.slice_signature(target, 120)
    top_down = list(reversed(signature))
    arms = dl.longest_run([i for i, s in enumerate(top_down) if len(s["blobs"]) >= 3])
    legs = dl.longest_run(
        [i for i, s in enumerate(top_down) if len(s["blobs"]) == 2 and i > max(arms)]
    )
    if not arms or not legs:
        fail("no biped signature for axis tracking")
    robust_all = robust_arm_bands(dl, top_down, arms)
    arm_bands = [i for i in robust_all if top_down[i]["z"] > lm["wrist_z"]]
    band_mode = "robust"
    if len(arm_bands) < MIN_LINE_BANDS:
        arm_bands = [
            i
            for i in arms
            if len(top_down[i]["blobs"]) == 3 and top_down[i]["z"] > lm["wrist_z"]
        ]
        band_mode = "legacy-above-wrist"
    if len(arm_bands) < MIN_LINE_BANDS:
        arm_bands = [i for i in arms if len(top_down[i]["blobs"]) == 3]
        band_mode = "legacy-all"
    leg_min_z = LEG_LINE_MIN_Z_FRAC * lm["height"]
    leg_bands = [i for i in legs if top_down[i]["z"] > leg_min_z]
    if len(leg_bands) < MIN_LINE_BANDS:
        leg_bands = legs
    arm_samples, arm_asym = limb_samples(dl, top_down, arm_bands)
    leg_samples, leg_asym = limb_samples(dl, top_down, leg_bands)
    if len(arm_samples) < MIN_LINE_BANDS or len(leg_samples) < MIN_LINE_BANDS:
        fail("untrackable limb blobs for axis fit")
    leg_cy = [(z, cy) for _, cy, z in limb_samples(dl, top_down, legs)[0]]
    elbow_new = rederive_elbow(dl, top_down, robust_all, lm["wrist_z"])
    elbow_z = lm["elbow_z"]
    elbow_override = False
    if elbow_new is not None and abs(elbow_new - lm["elbow_z"]) >= ELBOW_OVERRIDE_MIN:
        elbow_z = elbow_new
        elbow_override = True
    up_bands = [i for i in arm_bands if top_down[i]["z"] > elbow_z]
    upper_ray = None
    if len(up_bands) >= UPPER_RAY_MIN_BANDS:
        up = fit_line(limb_samples(dl, top_down, up_bands)[0])
        mx, my = up.slope
        upper_ray = Vector((mx, my, 1.0)).normalized()
    return {
        "arm": fit_line(arm_samples),
        "leg": fit_line(leg_samples),
        "arm_bands": len(arm_samples),
        "leg_bands": len(leg_samples),
        "leg_cy": leg_cy,
        "input_asym": round(max(arm_asym, leg_asym), 4),
        "band_mode": band_mode,
        "elbow_z": elbow_z,
        "elbow_lm_z": lm["elbow_z"],
        "elbow_override": elbow_override,
        "upper_ray": upper_ray,
        "upper_ray_bands": len(up_bands),
        "env": target,
        "signature": signature,
        "top_down": top_down,
        "arms_run": arms,
        "legs_run": legs,
        "dl": dl,
    }


def nearest_cy(leg_cy, z):
    if not leg_cy:
        fail("no leg centroid bands for ankle y lookup")
    return min(leg_cy, key=lambda zcy: abs(zcy[0] - z))[1]


def forefoot_top(env, lm, cy0):
    """Highest forefoot-vertex z (the dorsum the ankle dip must sit near)."""
    if lm["facing"] == "-Y":
        front = [
            v.co.z
            for v in env.data.vertices
            if v.co.x > 0.005 and v.co.y < cy0 - 0.05 and v.co.z < lm["ankle_z"]
        ]
    else:
        front = [
            v.co.z
            for v in env.data.vertices
            if v.co.x > 0.005 and v.co.y > cy0 + 0.05 and v.co.z < lm["ankle_z"]
        ]
    return max(front) if len(front) >= 20 else None


def finger_column(dl, axes, lm, wrist_z):
    """Mesh finger-column direction over the palm/finger window."""
    top_down = axes["top_down"]
    bands = [
        i
        for i in axes["arms_run"]
        if valid_arm_pair(dl, top_down[i]["blobs"])
        and top_down[i]["z"] < wrist_z + 0.03
        and top_down[i]["z"] > lm["hand_tip_z"] - 0.01
    ]
    if len(bands) < HAND_COL_MIN_BANDS:
        return None, f"only {len(bands)} finger-column bands"
    samples, _ = limb_samples(dl, top_down, bands)
    if len(samples) < HAND_COL_MIN_BANDS:
        return None, "finger column untrackable"
    col = fit_line(samples)
    mx, my = col.slope
    direction = Vector((-mx, -my, -1.0)).normalized()
    anchor = Vector(col((wrist_z + lm["hand_tip_z"]) / 2))
    return (direction, anchor), None


def _validate_biped_order(lm, elbow_z):
    if lm["facing"] not in ("-Y", "+Y"):
        fail(f"unsupported facing {lm['facing']!r}")
    order = {
        "neck_z": lm["neck_z"],
        "armpit_z": lm["armpit_z"],
        "elbow_z": elbow_z,
        "wrist_z": lm["wrist_z"],
        "hand_tip_z": lm["hand_tip_z"],
        "crotch_z": lm["crotch_z"],
        "knee_z": lm["knee_z"],
    }
    for a, b in (
        ("neck_z", "armpit_z"),
        ("armpit_z", "elbow_z"),
        ("elbow_z", "wrist_z"),
        ("wrist_z", "hand_tip_z"),
        ("crotch_z", "knee_z"),
    ):
        if not order[a] > order[b]:
            fail(f"landmark order violated: {a}={order[a]} <= {b}={order[b]}")


def _resolve_shoulder(
    axes, lm, preset, s, rv, eval_env, margin, elbow, wrist, cautions
):
    legacy_up = (elbow - wrist).normalized()
    upper_len = preset["upper_len"] * s
    min_shoulder_z = lm["armpit_z"] + 0.02
    shoulder_off = preset["shoulder_mid_off"] * s

    def shoulder_states(t):
        mid_s, mid_u = t + shoulder_off, (t + elbow) / 2
        return [
            ("shoulder.L", mid_s),
            ("upper_arm.L", mid_u),
            ("shoulder.R", mirror_x(mid_s)),
            ("upper_arm.R", mirror_x(mid_u)),
        ]

    def shoulder_score(t):
        depths = []
        for _, mid in shoulder_states(t):
            ok, depth = mid_state(rv, eval_env, mid, margin)
            if not ok:
                return None
            depths.append(depth)
        return min(depths)

    def clamped(t):
        if t.z < min_shoulder_z and abs(t.z - elbow.z) > 1e-9:
            k = (min_shoulder_z - elbow.z) / (t.z - elbow.z)
            t = elbow + (t - elbow) * k
        return t

    ray = axes.get("upper_ray")
    cands = []
    if ray is not None:
        cands.append(("ray", clamped(elbow + ray * upper_len)))
    legacy = clamped(elbow + legacy_up * upper_len)
    legacy.y = elbow.y
    cands.append(("legacy", legacy))
    scored = [(name, t, shoulder_score(t)) for name, t in cands]
    passing = [(name, t, score) for name, t, score in scored if score is not None]
    shoulder_method = None
    marched = 0
    if passing:
        shoulder_method, shoulder, _ = max(passing, key=lambda ntg: ntg[2])
    else:
        march_dir = ray if ray is not None else legacy_up
        shoulder_method = "march"
        best, best_depth = legacy, -1.0
        frac = 1.0
        while frac >= SHOULDER_MARCH_MIN - 1e-9:
            t = clamped(elbow + march_dir * upper_len * frac)
            score = shoulder_score(t)
            depths = [
                rv.point_depth(eval_env, mid)["depth"] for _, mid in shoulder_states(t)
            ]
            if min(depths) > best_depth:
                best, best_depth = t, min(depths)
            if score is not None:
                best = t
                break
            marched += 1
            frac -= SHOULDER_MARCH_STEP
        shoulder = best
        cautions.append(
            f"shoulder march exhausted ({marched} steps), best-effort used"
            if shoulder_score(shoulder) is None
            else f"shoulder marched to frac {round(frac, 2)}"
        )
    if shoulder.z <= min_shoulder_z + 1e-9 and shoulder_method != "march":
        cautions.append("shoulder clamped above armpit")
    return shoulder, {
        "method": shoulder_method,
        "marched_steps": marched,
        "ray_bands": axes.get("upper_ray_bands", 0),
    }


def _resolve_ankle_foot(axes, lm, preset, s, rv, eval_env, margin, leg, cautions):
    cy0 = nearest_cy(axes["leg_cy"], lm["ankle_z"])
    dorsum = forefoot_top(axes["env"], lm, cy0)
    weak = lm["ankle_dip"] < ANKLE_DIP_MIN
    high = lm["ankle_z"] > ANKLE_MAX_Z_FRAC * lm["height"]
    high_foot = dorsum is not None and lm["ankle_z"] > dorsum + FOOT_TOP_MARGIN
    ankle_fb = weak or high or high_foot
    ankle_z = lm["zmin"] + ANKLE_SOLE_FRAC * lm["height"] if ankle_fb else lm["ankle_z"]
    ankle = Vector((leg(ankle_z)[0], nearest_cy(axes["leg_cy"], ankle_z), ankle_z))
    foot_vec = preset["foot_vec"] * s
    toe_vec = preset["toe_vec"] * s
    if lm["facing"] == "+Y":
        foot_vec.y *= -1
        toe_vec.y *= -1
    foot = ankle + foot_vec
    prospective_outside = False
    for mid in ((ankle + foot) / 2, foot + toe_vec / 2):
        if not rv.point_depth(eval_env, mid)["inside"]:
            prospective_outside = True
        if not rv.point_depth(eval_env, mirror_x(mid))["inside"]:
            prospective_outside = True
    if prospective_outside and not ankle_fb:
        ankle_fb = True
        ankle_z = lm["zmin"] + ANKLE_SOLE_FRAC * lm["height"]
        ankle = Vector((leg(ankle_z)[0], nearest_cy(axes["leg_cy"], ankle_z), ankle_z))
        foot = ankle + foot_vec
        cautions.append("ankle forced to sole offset: chain outside the mesh")

    def foot_mids(t):
        mid_f, mid_t = (ankle + t) / 2, t + toe_vec / 2
        return [
            ("foot.L", mid_f),
            ("toe.L", mid_t),
            ("foot.R", mirror_x(mid_f)),
            ("toe.R", mirror_x(mid_t)),
        ]

    foot, foot_nudged = nudge_target(
        rv, eval_env, margin, foot, foot_mids, "foot", cautions
    )
    return (
        ankle,
        foot,
        {
            "fired": ankle_fb,
            "weak_dip": weak,
            "implausible_z": high,
            "forefoot_trigger": high_foot,
            "dorsum_z": round(dorsum, 4) if dorsum is not None else None,
            "measured_z": lm["ankle_z"],
            "used_z": round(ankle_z, 4),
        },
        {"nudged": foot_nudged},
    )


def _resolve_hand(axes, lm, hand_vec, rv, eval_env, margin, wrist, cautions):
    hand = wrist + hand_vec
    hand_fit = False
    hand_mid = (wrist + hand) / 2
    if (
        not rv.point_depth(eval_env, hand_mid)["inside"]
        or not rv.point_depth(eval_env, mirror_x(hand_mid))["inside"]
    ):
        col, reason = finger_column(axes["dl"], axes, lm, wrist.z)
        if col is None:
            cautions.append(f"hand mesh-fit skipped: {reason}")
        else:
            direction, _anchor = col
            span = wrist.z - lm["hand_tip_z"]
            if span < 0.02:
                cautions.append(
                    f"hand mesh-fit skipped: span {round(span, 4)} too short"
                )
            else:
                size = min(hand_vec.length, span * HAND_SPAN_FRAC)
                hand = wrist + direction * size
                hand_fit = True
                cautions.append(
                    f"hand mesh-fit to {[round(v, 4) for v in hand]} "
                    f"(len {round(size, 4)})"
                )

    def hand_mids(t):
        mid = (wrist + t) / 2
        return [("hand.L", mid), ("hand.R", mirror_x(mid))]

    hand, hand_nudged = nudge_target(
        rv, eval_env, margin, hand, hand_mids, "hand", cautions
    )
    return hand, {"mesh_fit": hand_fit, "nudged": hand_nudged}


def resolve_targets(lm, axes, preset, rv):
    """Landmark JSON + axis fits -> driver-joint targets + fallback record."""
    elbow_z = axes.get("elbow_z", lm["elbow_z"])
    _validate_biped_order(lm, elbow_z)
    s = lm["height"] / preset["height"]
    arm, leg = axes["arm"], axes["leg"]
    cautions = []
    eval_env = eval_envelope(axes["env"])
    margin = rv.MARGIN_DEFAULT

    elbow = Vector(arm(elbow_z))
    wrist = Vector(arm(lm["wrist_z"]))
    if axes.get("elbow_override"):
        cautions.append(
            f"elbow re-derived {lm['elbow_z']} -> {round(elbow_z, 4)} "
            "(detector band speck-dragged)"
        )
    if lm["elbow_dip"] < ELBOW_CAUTION_DIP:
        cautions.append(f"elbow_dip {lm['elbow_dip']} < {ELBOW_CAUTION_DIP}")

    wrist_fb = lm["wrist_dip"] < WRIST_DIP_MIN
    if wrist_fb:
        wz = lm["hand_tip_z"] + preset["palm_drop"] * s
        wrist = Vector((*arm(wz)[:2], wz))
        cautions.append(f"wrist fallback to hand_tip offset: z={wz:.4f}")
    if lm["wrist_dip"] < ANKLE_DIP_MIN and not wrist_fb:
        cautions.append(f"wrist_dip {lm['wrist_dip']} weak but usable")

    shoulder, shoulder_fb = _resolve_shoulder(
        axes, lm, preset, s, rv, eval_env, margin, elbow, wrist, cautions
    )
    ankle, foot, ankle_fb_rec, foot_fb_rec = _resolve_ankle_foot(
        axes, lm, preset, s, rv, eval_env, margin, leg, cautions
    )
    knee = Vector(leg(lm["knee_z"]))
    hip = Vector(
        (leg(preset["hip_z"] * s)[0], preset["hip_y"] * s, preset["hip_z"] * s)
    )
    hand, hand_fb_rec = _resolve_hand(
        axes, lm, preset["hand_vec"] * s, rv, eval_env, margin, wrist, cautions
    )

    targets = {
        "shoulder": shoulder,
        "elbow": elbow,
        "wrist": wrist,
        "hand": hand,
        "hip": hip,
        "knee": knee,
        "ankle": ankle,
        "foot": foot,
    }
    fallbacks = {
        "ankle": ankle_fb_rec,
        "wrist": {
            "fired": wrist_fb,
            "dip": lm["wrist_dip"],
            "used_z": round(wrist.z, 4),
        },
        "elbow": {
            "fired": axes.get("elbow_override", False),
            "measured_z": lm["elbow_z"],
            "used_z": round(elbow_z, 4),
        },
        "shoulder": shoulder_fb,
        "hand": hand_fb_rec,
        "foot": foot_fb_rec,
    }
    return targets, fallbacks, cautions, s


def snapshot_preset(hp, obj):
    """Preset measures in OBJECT mode (Bone head_local/matrix_local)."""
    bones = obj.data.bones
    height = max(max(b.head_local.z, b.tail_local.z) for b in bones)
    fingers = hp.descendants(bones, "hand.L")
    fingertip = min(min(bones[n].head_local.z, bones[n].tail_local.z) for n in fingers)
    hand = bones["hand.L"]
    shoulder = bones["shoulder.L"]
    upper = bones["upper_arm.L"]
    preset = {
        "height": height,
        "upper_len": (upper.tail_local - upper.head_local).length,
        "hand_vec": hand.tail_local - hand.head_local,
        "foot_vec": bones["foot.L"].tail_local - bones["foot.L"].head_local,
        "toe_vec": bones["toe.L"].tail_local - bones["toe.L"].head_local,
        "shoulder_mid_off": (shoulder.head_local + shoulder.tail_local) / 2
        - upper.head_local,
        "palm_drop": hand.tail_local.z - fingertip,
        "hip_z": bones["thigh.L"].head_local.z,
        "hip_y": bones["thigh.L"].head_local.y,
    }
    return preset, snapshot_bones(bones, DRIVERS)


def fit_spine(hp, obj, lm, axes=None, rv=None):
    """Torso compressed so the neck base lands on neck_z, head on zmax."""
    bones = obj.data.edit_bones
    base = bones["spine"].head.z
    old_top = bones["spine.003"].tail.z
    old_neck_z = bones["spine.004"].head.z
    if old_top <= base:
        fail("torso span collapsed")
    k_torso = (lm["neck_z"] - base) / (old_top - base)
    torso = Matrix.Identity(4)
    torso[2][2] = k_torso
    torso_bones = ["spine", "spine.001", "spine.002", "spine.003"]
    for name in ("breast.L", "breast.R"):
        if name in bones:
            torso_bones.append(name)
    hp.apply(
        bones,
        torso_bones,
        hp.about(Vector((0.0, 0.0, base)), torso),
    )
    neck = hp.descendants(bones, "spine.004")
    hp.apply(
        bones,
        neck,
        Matrix.Translation(bones["spine.003"].tail - bones["spine.004"].head),
    )
    dz = lm["neck_z"] - old_neck_z
    top = bones["spine.006"].tail.z
    span = top - lm["neck_z"]
    if span <= 0:
        fail("neck span collapsed")
    k = (lm["zmax"] - lm["neck_z"]) / span
    old_head = bones["spine.006"].head.copy()
    head_box = (
        measure_head_box(axes["signature"], lm, rv)
        if axes is not None and rv is not None and "face" in bones
        else None
    )
    head_pivot_z = (
        max(
            lm["neck_z"] + 0.015 * lm["height"],
            min(
                head_box["min_z"] + 0.14 * head_box["head_h"],
                lm["zmax"] - 0.025 * lm["height"],
            ),
        )
        if head_box is not None
        else None
    )
    neck_span = old_head.z - lm["neck_z"]
    head_span = top - old_head.z
    if head_box is not None and neck_span > 0 and head_span > 0:
        k_neck = (head_pivot_z - lm["neck_z"]) / neck_span
        neck_m = Matrix.Identity(4)
        neck_m[2][2] = k_neck
        hp.apply(
            bones,
            ["spine.004", "spine.005"],
            hp.about(Vector((0.0, 0.0, lm["neck_z"])), neck_m),
        )
        hp.apply(
            bones,
            ["spine.006"],
            Matrix.Translation(bones["spine.005"].tail - old_head),
        )
        k_head = (lm["zmax"] - bones["spine.006"].head.z) / head_span
        head_m = Matrix.Identity(4)
        head_m[2][2] = k_head
        hp.apply(bones, ["spine.006"], hp.about(bones["spine.006"].head.copy(), head_m))
        fit_face(hp, bones, lm, old_head, head_box, env=axes.get("env"))
        return dz, k, k_torso

    stretch = Matrix.Identity(4)
    stretch[2][2] = k
    pivot = Vector((0.0, 0.0, lm["neck_z"]))
    chain = ["spine.004", "spine.005", "spine.006"]
    hp.apply(bones, chain, hp.about(pivot, stretch))
    shift = bones["spine.006"].head - old_head
    hp.apply(bones, hp.descendants(bones, "face"), Matrix.Translation(shift))
    return dz, k, k_torso


def joint_positions(bones, edit):
    return extract_joint_positions(bones, edit, BIPED_JOINT_MAP)


def fit_hero(hp, align_roll, obj, lm, axes, rv):
    """Solve the biped_hero metarig `obj` onto landmarks; returns fit record."""
    preset, snap = snapshot_preset(hp, obj)
    targets, fallbacks, cautions, s = resolve_targets(lm, axes, preset, rv)
    before = {k: v * s for k, v in joint_positions(obj.data.bones, False).items()}

    bpy.ops.object.mode_set(mode="EDIT")
    bones = obj.data.edit_bones
    for b in bones:  # as in make_presets: no connected dragging
        b.use_connect = False
    hp.apply(bones, [b.name for b in bones], Matrix.Scale(s, 4))  # coarse fit
    spine_before = {
        "neck_base": bones["spine.004"].head.copy(),
        "head_top": bones["spine.006"].tail.copy(),
    }
    neck_dz, neck_k, torso_k = fit_spine(hp, obj, lm, axes=axes, rv=rv)

    hp.apply(
        bones,
        hp.descendants(bones, "shoulder.L"),
        Matrix.Translation(targets["shoulder"] - bones["upper_arm.L"].head),
    )
    angles = {}
    for bone, head_k, tail_k in (
        ("upper_arm.L", "shoulder", "elbow"),
        ("forearm.L", "elbow", "wrist"),
        ("hand.L", "wrist", "hand"),
        ("thigh.L", "hip", "knee"),
        ("shin.L", "knee", "ankle"),
        ("foot.L", "ankle", "foot"),
    ):
        angles[bone], _ = solve_bone(
            hp, align_roll, obj, snap, bone, targets[head_k], targets[tail_k]
        )
    realigned_bones = [b for b in angles if angles[b] > ROLL_RECOMPUTE_DEG]
    mirror_subtree(hp, bones, "shoulder.L")
    mirror_subtree(hp, bones, "thigh.L")
    hp.reconnect_from(obj, "rigforge_biped_hero_metarig_add")
    fingers_fixed = fit_fingers(rv, axes["env"], lm, obj, cautions)

    max_gap, open_drivers = check_closure(obj)
    bones = obj.data.edit_bones  # refetch: reconnect_from switched modes
    symmetry = check_symmetry(bones)
    after = joint_positions(bones, True)
    joints = {}
    for key, tgt in targets.items():
        joints[key] = {
            "target": [round(v, 4) for v in tgt],
            "before_d": round((before[key] - tgt).length, 4),
            "after_d": round((after[key] - tgt).length, 4),
        }
    joints["neck_base"] = {
        "target": [0.0, round(spine_before["neck_base"].y, 4), lm["neck_z"]],
        "before_d": round(abs(spine_before["neck_base"].z - lm["neck_z"]), 4),
        "after_d": round(abs(bones["spine.004"].head.z - lm["neck_z"]), 4),
    }
    joints["head_top"] = {
        "target": [0.0, round(spine_before["head_top"].y, 4), lm["zmax"]],
        "before_d": round(abs(spine_before["head_top"].z - lm["zmax"]), 4),
        "after_d": round(abs(bones["spine.006"].tail.z - lm["zmax"]), 4),
    }
    lengths = {}
    for bone in DRIVERS:
        old = (snap[bone]["tail"] - snap[bone]["head"]).length * s
        new = (bones[bone].tail - bones[bone].head).length
        lengths[bone] = round(100 * (new - old) / old, 1)
    return {
        "scale": s,
        "fallbacks": fallbacks,
        "cautions": cautions,
        "fingers_fixed": fingers_fixed,
        "neck": {"dz": neck_dz, "stretch": neck_k, "torso_k": torso_k},
        "joints": joints,
        "length_change_pct": lengths,
        "roll_realigned": sorted(realigned_bones),
        "closure": {"max_gap": max_gap, "open_drivers": open_drivers},
        "symmetry_max": symmetry,
    }
