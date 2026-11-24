# SPDX-License-Identifier: GPL-2.0-or-later
"""Fit a preset metarig to a subject mesh (auto-placement Phase 2+).

Headless (from the repo root; the preset + generate need the addon overlay):
    rsync -a --delete rigforge/ /tmp/rf_scripts/addons/rigforge/
    BLENDER_USER_SCRIPTS=/tmp/rf_scripts \\
      /Applications/Blender.app/Contents/MacOS/Blender --background \\
      --factory-startup --python tools/fit_metarig.py -- \\
      MESH LANDMARKS.json [--out FILE] [--blend FILE] [--no-generate]
      [--preset biped_hero|quadruped_stalker] [--hints HINTS.json] [--hints-rotated]

--preset defaults from the landmarks kind (biped -> biped_hero,
quadruped -> quadruped_stalker) and must match it. --hints feeds a
skintokens-joints/1 (or legacy unirig-joints/1) document as soft priors
(agree -> trust, disagree -> measurement wins + divergence report; see
tools/unirig_hints.py).

Inputs: subject mesh (glb/fbx/obj, transforms applied + joined, same as
Phase 1) and the Phase-1 landmark JSON from tools/detect_landmarks.py.
Output: fitted metarig module in write_metarig format (default
/tmp/rigforge_fitted_hero.py), fit report JSON between
RIGFORGE_FIT_BEGIN/OK markers, and (default) an in-session headless
generate of the fitted metarig via the real generate path
(RIGFORGE_FIT_GENERATE_OK + DEF-bone count as proof).

Implementation lives in focused submodules (`fit_common`, `fit_biped`,
`fit_face`, `fit_fingers`, `fit_quadruped`); this module re-exports the
full API and provides the headless CLI entrypoint `main()`.
"""

import json
import sys

import bpy

from . import detect_landmarks as _detect
from . import make_presets as _presets
from . import unirig_hints as _hints
from . import validate_fit as _validate
from .fit_biped import (
    ANKLE_DIP_MIN as ANKLE_DIP_MIN,
)
from .fit_biped import (
    ANKLE_MAX_Z_FRAC as ANKLE_MAX_Z_FRAC,
)
from .fit_biped import (
    ANKLE_SOLE_FRAC as ANKLE_SOLE_FRAC,
)
from .fit_biped import (
    ELBOW_CAUTION_DIP as ELBOW_CAUTION_DIP,
)
from .fit_biped import (
    ELBOW_OVERRIDE_MIN as ELBOW_OVERRIDE_MIN,
)
from .fit_biped import (
    FOOT_TOP_MARGIN as FOOT_TOP_MARGIN,
)
from .fit_biped import (
    HAND_COL_MIN_BANDS as HAND_COL_MIN_BANDS,
)
from .fit_biped import (
    HAND_SPAN_FRAC as HAND_SPAN_FRAC,
)
from .fit_biped import (
    LEG_LINE_MIN_Z_FRAC as LEG_LINE_MIN_Z_FRAC,
)
from .fit_biped import (
    MIN_BLOB_N as MIN_BLOB_N,
)
from .fit_biped import (
    MIN_BLOB_R as MIN_BLOB_R,
)
from .fit_biped import (
    MIN_LINE_BANDS as MIN_LINE_BANDS,
)
from .fit_biped import (
    SHOULDER_MARCH_MIN as SHOULDER_MARCH_MIN,
)
from .fit_biped import (
    SHOULDER_MARCH_STEP as SHOULDER_MARCH_STEP,
)
from .fit_biped import (
    UPPER_RAY_MIN_BANDS as UPPER_RAY_MIN_BANDS,
)
from .fit_biped import (
    WRIST_DIP_MIN as WRIST_DIP_MIN,
)
from .fit_biped import (
    finger_column as finger_column,
)
from .fit_biped import (
    fit_hero as fit_hero,
)
from .fit_biped import (
    fit_spine as fit_spine,
)
from .fit_biped import (
    forefoot_top as forefoot_top,
)
from .fit_biped import (
    joint_positions as joint_positions,
)
from .fit_biped import (
    limb_samples as limb_samples,
)
from .fit_biped import (
    nearest_cy as nearest_cy,
)
from .fit_biped import (
    rederive_elbow as rederive_elbow,
)
from .fit_biped import (
    resolve_targets as resolve_targets,
)
from .fit_biped import (
    robust_arm_bands as robust_arm_bands,
)
from .fit_biped import (
    snapshot_preset as snapshot_preset,
)
from .fit_biped import (
    track_axes as track_axes,
)
from .fit_biped import (
    track_axes_from_subject as track_axes_from_subject,
)
from .fit_biped import (
    valid_arm_pair as valid_arm_pair,
)
from .fit_common import (
    CLOSE_EPS as CLOSE_EPS,
)
from .fit_common import (
    DRIVERS as DRIVERS,
)
from .fit_common import (
    NUDGE_HEADROOM as NUDGE_HEADROOM,
)
from .fit_common import (
    NUDGE_MAX_STEPS as NUDGE_MAX_STEPS,
)
from .fit_common import (
    NUDGE_STEP as NUDGE_STEP,
)
from .fit_common import (
    ROLL_RECOMPUTE_DEG as ROLL_RECOMPUTE_DEG,
)
from .fit_common import (
    SYMMETRY_TOL as SYMMETRY_TOL,
)
from .fit_common import (
    _renorm as _renorm,
)
from .fit_common import (
    arg_value as arg_value,
)
from .fit_common import (
    chain_fractions as chain_fractions,
)
from .fit_common import (
    check_closure as check_closure,
)
from .fit_common import (
    check_symmetry as check_symmetry,
)
from .fit_common import (
    emit_module as emit_module,
)
from .fit_common import (
    eval_envelope as eval_envelope,
)
from .fit_common import (
    extract_joint_positions as extract_joint_positions,
)
from .fit_common import (
    fail as fail,
)
from .fit_common import (
    fit_line as fit_line,
)
from .fit_common import (
    hint_target_overrides as hint_target_overrides,
)
from .fit_common import (
    lerp_points as lerp_points,
)
from .fit_common import (
    mid_state as mid_state,
)
from .fit_common import (
    mirror_bones as mirror_bones,
)
from .fit_common import (
    mirror_subtree as mirror_subtree,
)
from .fit_common import (
    mirror_x as mirror_x,
)
from .fit_common import (
    nearest_surface_dir as nearest_surface_dir,
)
from .fit_common import (
    nudge_target as nudge_target,
)
from .fit_common import (
    run_generate as run_generate,
)
from .fit_common import (
    snapshot_bones as snapshot_bones,
)
from .fit_common import (
    solve_bone as solve_bone,
)
from .fit_common import (
    solve_chain as solve_chain,
)
from .fit_face import (
    SURFACE_FACE_PREFIXES as SURFACE_FACE_PREFIXES,
)
from .fit_face import (
    _seat_face_to_surface as _seat_face_to_surface,
)
from .fit_face import (
    _surface_front_y as _surface_front_y,
)
from .fit_face import (
    _warp_toon_face_z as _warp_toon_face_z,
)
from .fit_face import (
    fit_face as fit_face,
)
from .fit_face import (
    measure_head_box as measure_head_box,
)
from .fit_fingers import (
    FINGER_CAP_DEG as FINGER_CAP_DEG,
)
from .fit_fingers import (
    FINGER_CHAINS as FINGER_CHAINS,
)
from .fit_fingers import (
    FINGER_SLAB_LATERAL as FINGER_SLAB_LATERAL,
)
from .fit_fingers import (
    FINGER_SLAB_MEDIAL as FINGER_SLAB_MEDIAL,
)
from .fit_fingers import (
    FINGER_SLAB_MIN_N as FINGER_SLAB_MIN_N,
)
from .fit_fingers import (
    FINGER_TIP_MIN_DEPTH as FINGER_TIP_MIN_DEPTH,
)
from .fit_fingers import (
    THUMB_MASK_R as THUMB_MASK_R,
)
from .fit_fingers import (
    dist_to_seg as dist_to_seg,
)
from .fit_fingers import (
    finger_row_axis as finger_row_axis,
)
from .fit_fingers import (
    fit_fingers as fit_fingers,
)
from .fit_fingers import (
    mesh_finger_columns as mesh_finger_columns,
)
from .fit_quadruped import (
    SPINE_END_MAX as SPINE_END_MAX,
)
from .fit_quadruped import (
    SPINE_END_NEED as SPINE_END_NEED,
)
from .fit_quadruped import (
    SPINE_END_STEP as SPINE_END_STEP,
)
from .fit_quadruped import (
    STALKER_CLOSURE_DRIVERS as STALKER_CLOSURE_DRIVERS,
)
from .fit_quadruped import (
    STALKER_DRIVERS as STALKER_DRIVERS,
)
from .fit_quadruped import (
    STALKER_FRONT_LEG as STALKER_FRONT_LEG,
)
from .fit_quadruped import (
    STALKER_NECK as STALKER_NECK,
)
from .fit_quadruped import (
    STALKER_REAR_LEG as STALKER_REAR_LEG,
)
from .fit_quadruped import (
    STALKER_SPINE as STALKER_SPINE,
)
from .fit_quadruped import (
    STALKER_TAIL as STALKER_TAIL,
)
from .fit_quadruped import (
    TAIL_SPAN_MIN as TAIL_SPAN_MIN,
)
from .fit_quadruped import (
    TOE_SPAN_MIN as TOE_SPAN_MIN,
)
from .fit_quadruped import (
    _deep_enough as _deep_enough,
)
from .fit_quadruped import (
    fit_stalker as fit_stalker,
)
from .fit_quadruped import (
    fit_stalker_object as fit_stalker_object,
)
from .fit_quadruped import (
    inset_chest_top as inset_chest_top,
)
from .fit_quadruped import (
    inset_spine_end as inset_spine_end,
)
from .fit_quadruped import (
    main_stalker as main_stalker,
)
from .fit_quadruped import (
    snapshot_stalker_preset as snapshot_stalker_preset,
)
from .fit_quadruped import (
    stalker_joint_positions as stalker_joint_positions,
)
from .fit_quadruped import (
    stalker_targets as stalker_targets,
)

BIPED_REQUIRED_LANDMARKS = (
    "facing",
    "height",
    "zmin",
    "zmax",
    "neck_z",
    "armpit_z",
    "elbow_z",
    "wrist_z",
    "hand_tip_z",
    "wrist_x",
    "crotch_z",
    "knee_z",
    "ankle_z",
    "ankle_x",
    "wrist_dip",
    "elbow_dip",
    "knee_dip",
    "ankle_dip",
)


def main():
    """CLI entrypoint for headless biped_hero / quadruped_stalker metarig fit."""
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    if len(args) < 2 or args[0].startswith("-") or args[1].startswith("-"):
        fail("usage: fit_metarig.py MESH LANDMARKS.json [--out F] [--blend F]")
    mesh_path, lm_path = args[0], args[1]
    try:
        with open(lm_path, encoding="utf-8") as f:
            lm = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read landmarks JSON {lm_path!r}: {exc}")
    if not isinstance(lm, dict):
        fail(f"landmark JSON must be an object, got {type(lm).__name__}")
    kind = lm.get("kind", "biped")
    preset = (
        arg_value(args, "--preset")
        if "--preset" in args
        else {"biped": "biped_hero", "quadruped": "quadruped_stalker"}.get(kind)
    )
    if preset not in ("biped_hero", "quadruped_stalker"):
        fail(f"--preset must be biped_hero or quadruped_stalker, got {preset!r}")
    if (preset == "quadruped_stalker") != (kind == "quadruped"):
        fail(f"preset {preset} needs kind={kind!r} landmarks")
    tag = "stalker" if preset == "quadruped_stalker" else "hero"
    out_path = (
        arg_value(args, "--out")
        if "--out" in args
        else f"/tmp/rigforge_fitted_{tag}.py"
    )
    blend_path = (
        arg_value(args, "--blend")
        if "--blend" in args
        else f"/tmp/rigforge_fitted_{tag}.blend"
    )
    want_generate = "--no-generate" not in args
    if "--no-blend" in args:
        blend_path = None

    if preset == "quadruped_stalker":
        main_stalker(
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
        )
        return

    for key in BIPED_REQUIRED_LANDMARKS:
        if lm.get(key) is None:
            fail(f"landmark JSON missing {key}")

    dl, hp, rv = _detect, _presets, _validate
    try:
        bpy.ops.preferences.addon_enable(module="rigforge")
    except RuntimeError:
        fail("rigforge addon not found; set BLENDER_USER_SCRIPTS overlay")
    import rigforge
    from rigforge.utils.bones import align_bone_z_axis

    print("rigforge:", rigforge.__file__)

    axes = track_axes(dl, mesh_path, lm)

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.rigforge_biped_hero_metarig_add()
    obj = bpy.context.view_layer.objects.active
    fit = fit_hero(hp, align_bone_z_axis, obj, lm, axes, rv)

    emit_module(obj, out_path, mesh_path, lm_path)

    generate = run_generate(obj) if want_generate else None

    if blend_path:
        bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    report = {
        "subject": mesh_path,
        "facing": lm["facing"],
        "height": lm["height"],
        "scale": round(fit["scale"], 4),
        "axis_bands": {"arm": axes["arm_bands"], "leg": axes["leg_bands"]},
        "band_mode": axes["band_mode"],
        "input_asym": axes["input_asym"],
        "fallbacks": fit["fallbacks"],
        "cautions": fit["cautions"],
        "fingers_fixed": fit["fingers_fixed"],
        "neck": {
            "dz": round(fit["neck"]["dz"], 4),
            "stretch": round(fit["neck"]["stretch"], 3),
            "torso_k": round(fit["neck"]["torso_k"], 3),
        },
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


if __name__ == "__main__":
    main()
