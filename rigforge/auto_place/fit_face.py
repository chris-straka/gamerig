# SPDX-License-Identifier: GPL-2.0-or-later
"""Facial sub-rig measurement, toon orbital warp, and skin seating."""

from mathutils import Matrix, Vector

SURFACE_FACE_PREFIXES = (
    "lid.",
    "brow.",
    "lip.",
    "chin",
    "nose",
    "cheek.",
)


def measure_head_box(signature, lm, rv):
    """Measure head base, skull top, width, and front prominence from slices."""
    bands = rv.head_bands(signature, lm)
    cap, _ = rv.skullcap_z(signature, lm)
    if len(bands) < 6 or cap is None:
        return None
    radii_td = [s["blobs"][0]["r"] for _, s in bands]
    half = len(bands) // 2
    dip = half + min(range(len(bands) - half), key=radii_td[half:].__getitem__)
    if dip >= len(bands) - 1:
        dip = len(bands) - 1
    if dip < 2:
        return None
    wide_td = max(range(dip), key=radii_td[:dip].__getitem__)
    wide_blob = bands[wide_td][1]["blobs"][0]
    r_head = wide_blob["r"]
    r_neck = min(radii_td[dip:])
    chin_floor = bands[-1][1]["z"]
    head_min_z = chin_floor
    if r_head > 1.25 * r_neck:
        r_thresh = r_neck + 0.18 * (r_head - r_neck)
        for i in range(dip, wide_td - 1, -1):
            if radii_td[i] >= r_thresh:
                z_hi = bands[i][1]["z"]
                if i < dip and radii_td[i] > radii_td[i + 1]:
                    t = (r_thresh - radii_td[i + 1]) / (radii_td[i] - radii_td[i + 1])
                    z_lo = bands[i + 1][1]["z"]
                    head_min_z = z_lo + t * (z_hi - z_lo)
                else:
                    head_min_z = z_hi
                break
    head_min_z = max(chin_floor, min(head_min_z, bands[wide_td][1]["z"] - 0.02))
    top_z = cap if head_min_z < cap <= lm["zmax"] else lm["zmax"]
    head_h = lm["zmax"] - head_min_z
    if head_h <= 0.02:
        return None
    half_x = 0.5 * (wide_blob["xmax"] - wide_blob["xmin"])
    cy = wide_blob["cy"]
    facing = lm.get("facing", "-Y")
    face_slices = [
        s
        for _, s in bands
        if head_min_z + 0.20 * head_h <= s["z"] <= head_min_z + 0.75 * head_h
    ] or [bands[wide_td][1]]
    if facing == "-Y":
        front_y = min(s["blobs"][0]["ymin"] for s in face_slices)
    else:
        front_y = max(s["blobs"][0]["ymax"] for s in face_slices)
    front_depth = max(abs(front_y - cy), half_x * 0.5)
    return {
        "min_z": head_min_z,
        "top_z": top_z,
        "chin_floor": chin_floor,
        "half_x": half_x,
        "cy": cy,
        "front_y": front_y,
        "front_depth": front_depth,
        "head_h": head_h,
    }


def _surface_front_y(env, inv_mw, x, z, start_y, dir_y, cy, min_depth):
    """Raycast env along facing direction at (abs(x), z) for symmetric skin Y."""
    origin = inv_mw @ Vector((abs(x), start_y, z))
    direction = (inv_mw.to_3x3() @ Vector((0.0, dir_y, 0.0))).normalized()
    ok, loc, _norm, _idx = env.ray_cast(origin, direction)
    if not ok:
        return None
    hit = env.matrix_world @ loc
    if (hit.y - cy) * dir_y >= -min_depth:
        return None
    return hit.y


def _seat_face_to_surface(env, bones, child_names, lm, head_box):
    """Project front-face bones just inside the facial skin surface of env."""
    if env is None:
        return
    inv_mw = env.matrix_world.inverted()
    facing = lm.get("facing", "-Y")
    dir_y = 1.0 if facing == "-Y" else -1.0
    start_y = head_box["front_y"] - dir_y * 0.5
    inset = max(0.003, 0.025 * head_box["half_x"])
    min_depth = 0.15 * head_box["front_depth"]

    def _pt_key(pt):
        return (round(abs(pt.x), 5), round(pt.y, 5), round(pt.z, 5))

    seated_y = {}
    for name in child_names:
        if not any(name.startswith(p) for p in SURFACE_FACE_PREFIXES):
            continue
        b = bones[name]
        for pt in (b.head, b.tail):
            key = _pt_key(pt)
            if key in seated_y:
                continue
            hit_y = _surface_front_y(
                env, inv_mw, pt.x, pt.z, start_y, dir_y, head_box["cy"], min_depth
            )
            if hit_y is not None:
                seated_y[key] = hit_y + dir_y * inset

    if not seated_y:
        return
    # Apply to every face child endpoint sharing a seated key (keeps jaw/chin
    # connected closures and L/R symmetry intact without breaking non-surface pivots).
    for name in child_names:
        b = bones[name]
        h_new = b.head.copy()
        t_new = b.tail.copy()
        kh = _pt_key(b.head)
        kt = _pt_key(b.tail)
        if kh in seated_y:
            h_new.y = seated_y[kh]
        if kt in seated_y:
            t_new.y = seated_y[kt]
        if name.startswith("forehead."):
            # Keep upper forehead anchors inside the cranium so spine.006 owns
            # the upper skull while forehead tails stay seated at the brow.
            hit_y = _surface_front_y(
                env, inv_mw, h_new.x, h_new.z, start_y, dir_y, head_box["cy"], min_depth
            )
            if hit_y is not None:
                fh_inset = max(inset, 0.50 * head_box["front_depth"])
                target_y = hit_y + dir_y * fh_inset
                # Clamp so target_y never crosses behind head center cy
                if (target_y - head_box["cy"]) * dir_y > 0.0:
                    target_y = 0.5 * (hit_y + head_box["cy"])
                h_new.y = target_y
        if (t_new - h_new).length > 1e-4:
            b.head = h_new
            b.tail = t_new


def _warp_toon_face_z(bones, child_names, lm, head_box, tgt_zmin, tgt_zmax):
    """Expand stylized orbital/brow spacing vertically when head ratio is large."""
    toon_t = max(0.0, min(1.0, (head_box["head_h"] / lm["height"] - 0.18) / 0.09))
    if toon_t <= 1e-4:
        return
    req = ("eye.L", "lid.B.L.002", "lid.T.L.002", "brow.T.L.002")
    if not all(n in bones for n in req):
        return
    face_h = tgt_zmax - tgt_zmin
    z_eye = tgt_zmin + 0.666 * face_h
    z_lid_b = tgt_zmin + 0.619 * face_h
    z_lid_t = tgt_zmin + 0.701 * face_h
    z_brow = tgt_zmin + 0.801 * face_h
    z_mouth = 0.5 * (tgt_zmin + z_lid_b)
    in_pts = [tgt_zmin, z_mouth, z_lid_b, z_eye, z_lid_t, z_brow, tgt_zmax]
    out_eye = z_eye - toon_t * 0.018 * head_box["head_h"]
    out_lid_b = out_eye - (1.0 + 1.15 * toon_t) * (z_eye - z_lid_b)
    out_lid_t = out_eye + (1.0 + 1.35 * toon_t) * (z_lid_t - z_eye)
    out_brow = out_eye + (1.0 + 0.72 * toon_t) * (z_brow - z_eye)
    out_pts = [tgt_zmin, z_mouth, out_lid_b, out_eye, out_lid_t, out_brow, tgt_zmax]
    if any(out_pts[i + 1] <= out_pts[i] + 1e-4 for i in range(len(out_pts) - 1)):
        return
    if (
        abs(bones["lid.T.L.002"].head.z - out_lid_t) <= 5e-4
        and abs(bones["lid.B.L.002"].head.z - out_lid_b) <= 5e-4
    ):
        return

    def _interp_z(z):
        if z <= in_pts[0]:
            return out_pts[0] + (z - in_pts[0])
        for i in range(len(in_pts) - 1):
            x0, x1 = in_pts[i], in_pts[i + 1]
            if z <= x1:
                y0, y1 = out_pts[i], out_pts[i + 1]
                return y0 + (z - x0) / (x1 - x0) * (y1 - y0)
        return out_pts[-1] + (z - in_pts[-1])

    for name in child_names:
        b = bones[name]
        h = b.head.copy()
        t = b.tail.copy()
        h.z = _interp_z(h.z)
        t.z = _interp_z(t.z)
        b.head = h
        b.tail = t
    # On stylized toon heads, seat the lateral TMJ hinge (temple tail / jaw.L/.R head)
    # closer to mouth-cheekbone height so jaw rotation opens downward cleanly.
    hinge_z = out_eye - 0.18 * head_box["head_h"]
    for side in ("L", "R"):
        temple = bones.get(f"temple.{side}")
        jaw_s = bones.get(f"jaw.{side}")
        jaw_lo = bones.get(f"jaw.{side}.001")
        if temple is None or jaw_s is None:
            continue
        cur_z = jaw_s.head.z
        new_z = (1.0 - toon_t) * cur_z + toon_t * hinge_z
        if z_mouth < new_z < cur_z:
            temple.tail.z = new_z
            jaw_s.head.z = new_z
            if jaw_lo is not None and jaw_lo.head.z < new_z - 0.01:
                mid_z = 0.5 * (new_z + jaw_lo.tail.z)
                jaw_s.tail.z = mid_z
                jaw_lo.head.z = mid_z


def fit_face(hp, bones, lm, old_head, head_box, env=None):
    """Scale and seat the face subtree inside the measured head volume."""
    sub_names = hp.descendants(bones, "face")
    child_names = [n for n in sub_names if n != "face"]
    if not child_names:
        shift = bones["spine.006"].head - old_head
        hp.apply(bones, sub_names, Matrix.Translation(shift))
        return
    child_bones = [bones[n] for n in child_names]
    cur_zmin = min(min(b.head.z, b.tail.z) for b in child_bones)
    cur_zmax = max(max(b.head.z, b.tail.z) for b in child_bones)
    cur_xmax = max(max(abs(b.head.x), abs(b.tail.x)) for b in child_bones)
    has_eye_ear = "ear.L" in bones and "eye.L" in bones
    if has_eye_ear:
        anchor_y = bones["ear.L"].head.y
        cur_depth = abs(anchor_y - bones["eye.L"].tail.y)
    else:
        anchor_y = old_head.y
        cur_depth = 0.82 * max(
            max(abs(anchor_y - b.head.y), abs(anchor_y - b.tail.y)) for b in child_bones
        )
    if cur_zmax <= cur_zmin or cur_xmax <= 1e-6 or cur_depth <= 1e-6:
        shift = bones["spine.006"].head - old_head
        hp.apply(bones, sub_names, Matrix.Translation(shift))
        return
    chin_margin = min(0.008, 0.04 * head_box["head_h"])
    tgt_zmin = max(
        head_box["chin_floor"] + chin_margin,
        head_box["min_z"] + 0.018 * head_box["head_h"],
    )
    toon_t = max(0.0, min(1.0, (head_box["head_h"] / lm["height"] - 0.18) / 0.09))
    top_frac = 0.89 + 0.05 * toon_t
    tgt_zmax = max(
        tgt_zmin + 0.05 * head_box["head_h"],
        head_box["min_z"] + top_frac * (head_box["top_z"] - head_box["min_z"]),
    )
    kx = (0.90 * head_box["half_x"]) / cur_xmax
    kz = (tgt_zmax - tgt_zmin) / (cur_zmax - cur_zmin)
    ky_mag = (0.78 * head_box["front_depth"]) / cur_depth
    want_sign = -1.0 if lm.get("facing", "-Y") == "-Y" else 1.0
    cur_sign = (
        (1.0 if bones["eye.L"].tail.y >= anchor_y else -1.0) if has_eye_ear else -1.0
    )
    sy = ky_mag * (want_sign * cur_sign)
    src = Vector((0.0, anchor_y, cur_zmin))
    dst = Vector((0.0, head_box["cy"], tgt_zmin))
    mat = (
        Matrix.Translation(dst)
        @ Matrix.Diagonal((kx, sy, kz, 1.0))
        @ Matrix.Translation(-src)
    )
    hp.apply(bones, child_names, mat)
    _warp_toon_face_z(bones, child_names, lm, head_box, tgt_zmin, tgt_zmax)
    _seat_face_to_surface(env, bones, child_names, lm, head_box)
    face_bone = bones["face"]
    face_bone.head = bones["spine.006"].head.copy()
    face_bone.tail = face_bone.head + Vector((0.0, 0.0, 0.40 * (tgt_zmax - tgt_zmin)))
