# gamerig-6: Brell Toon Face Rig Verification

Verify `biped_hero` face rig (`jaw`, upper/lower `lid`, `brow`, `eye`) on a
deterministic procedural toon-proportioned Brell fixture (~0.27 head ratio,
1.70 m height, expressive facial loops + upper torso):

1. `tools/brell_face_fixture.py` generates the procedural fixture deterministically,
   drives `jaw_open`, `blink`, and `brow_raise` expressions, and renders a 4-panel
   side-by-side comparison PNG (`/tmp/gamerig_6_brell_face_sheet.png`) at Brell's
   talk camera (1.3 m, 38 deg vertical FOV, 24 deg yaw).
2. `tests/test_brell_face_rig.py` checks:
   - `biped_hero` auto-placement puts face bones onto the toon head's chin,
     eyeballs, upper/lower eyelids, and brows.
   - Geodesic voxel weights bind all verts with non-zero coverage on jaw, lid,
     and brow deform bones.
   - Expressions produce clean jaw/lid/brow vertex displacement without bleed.
   - No-face-rig control baseline has zero face DEF bones/controls/displacement.
   - FULL GLB export preserves `DEF-` face bones and face animation channels;
     MOBILE GLB export merges face bones up without regressing humanoid exports.
   - Optional `rfcheck` and `weights check` pass when present (clean fallback
     in standalone CI `bpy` wheel).
3. Wired into `.github/workflows/tests.yml` and clean under `ruff`.
