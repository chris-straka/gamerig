# rigforge TODO

## Active / parked maintenance

- [x] Export/loader checks target Bevy 0.20.0 (2026-10-05; pinned in
      `rfcheck/glbkit/compat/bevy`): `tools/bevy_import_check.py` runs
      glbkit's headless Bevy 0.20.0 loader (skipped when not built).
- [x] Caller contract (`gamerig-1`, 2026-11-19): `tests/test_callers.py`
      covers `bodywrap`'s landmark detector + `biped_hero` regeneration and
      `assetgen`'s `genforge_adapter.sh repair-rig`; in CI's matrix.
- [x] Recorded joint-placement run (`gamerig-2`, 2026-11-19):
      `bench/history/2026-11-19.{json,md}` (8 CC0 bodies vs scaled metarig).
- [x] Self-owned hint fixtures (`gamerig-3`, 2026-11-19):
      `tests/fixtures/hints/` replaces retired external repo paths so
      `test_auto_placement.py` runs every time in CI.
- [ ] Optional: upstream watch — Blender keeps fixing Rigify; review
      new upstream commits periodically and hand-port (re-apply by
      judgment, never `git merge`: this tree has structurally diverged).

## Refactor / codebase health backlog

- [x] Lint debt (2026-10-02): all rigforge-authored code clean
      under `ruff check` + `ruff format` (weights, operators,
      auto_place, metarigs, tools, tests). Remaining ~253 findings
      are pristine upstream-fork files, deliberately untouched:
      F401s there are re-exports (removal risk) and any rebase
      re-copy would wipe the churn. RUF012 ignored repo-wide
      (Blender requires class-level `bl_options` sets).
- [x] Weight-paint assistance (complete 2026-10-02): smoothing,
      mirror, and cleanup operators. (W1: voxel auto-weights, tube
      + robustness green; W1b: best-safe defaults, bends
      near-parity, shoulder/neck tracked (method ceiling); W2:
      nudge strokes, solver + operator green; W3: piece transfer
      with inpainting, solver + operator green; W4: cleanup ops,
      rows + operator green.)
- [x] Error diagnostics (2026-10-02): wm.rigforge_diagnose_metarig
      pre-flight (read-only, armature panel): unknown Rig Type per
      bone with did-you-mean (typos threw a bare KeyError), root
      parent/type rules, no-types rig, spaced-type / degenerate /
      stray warnings (strays proven to become dead ORG bones).
- [x] Optional: renamed `RIGIFY-` fcurve data-path prefix in
      `generate.py` to `RIGFORGE-` (2026-10-02); restore still heals
      legacy `RIGIFY-` paths, round-trip pinned in test_rename.py.
- [x] Review fixes (2026-10-03): cleanup Mirror now flips Rigify
      `.L.001` twist/face names (was leaving them on the wrong side;
      reuses utils.naming.mirror_name); mobile face anchor reads
      armature-space `head_local` (Bone.head is parent-relative);
      mobile weight merge is one pass over verts (was one per dropped
      group, ~95x). Fixed test_game_export follow-up (2026-10-03,
      found on the bpy wheel): the jaw check now reads only the jaw
      mesh (importer gives every skinned mesh every joint group).
- [x] Weight-solver speed (2026-10-03, no output change): voxel
      geodesics are a numpy level-synchronous BFS (uniform step, so
      the same floats as the heap Dijkstra), CSR voxel adjacency,
      vectorized kernel, smoothing on each bone's support box
      (+iters cells) with shared neighbor counts; nudge adjacency
      and top-k softmax rows vectorized. Proven offline against the
      old code with stubbed mathutils: bit-identical on randomized
      grids/meshes (k < 8). Synthetic 92k-voxel / 60-bone bind ~3.7x
      faster outside KD lookups; all suites green on the bpy wheel.
- [x] Non-bone vertex groups (2026-10-03): cleanup (new Subset:
      Deform Bones default / All Groups), nudge, piece transfer,
      fill_unassigned and the mobile cap-4 read + write only the
      rig's deform-bone groups; masks, cloth pins, group order and
      locks survive (they used to be solved/normalized as bones and
      the groups recreated). Unrigged meshes: every group, as before.
      Also fixed a test_game_export fixture whose "5 bones" were 4
      bones + a stray non-bone DEF-jaw.001.
- [x] tools/run_test_bpy.py (2026-10-03): run any suite on the `bpy`
      pip wheel (no Blender install); used for the entries above.

## History (early milestones)

- [x] Day 0: pristine 0.6.10 copy, headless register + metarig + generate
- [x] Rename pass: module rigify -> rigforge (ids + generated imports),
      coexists with bundled Rigify (smoke loads our copy, generates 706 bones)
- [x] One-click game export: deform-bones-only GLB export (`wm.rigforge_game_export`,
      Armature panel button, `tests/test_game_export.py`)
- [x] rigforge presets: stylized biped + creature metarig starting points
      (biped_hero + quadruped_stalker, built by tools/make_presets.py,
      covered by tests/test_presets.py; baked-anim export in
      tests/test_game_export.py)
- [x] Bake-off vs retired unirig-mac on one hero + one creature (joints,
      weights, tweak time, engine import). Done 2026-09-30, results in
      docs/archive/bakeoff/final.md: rigforge won hero and creature quality,
      unirig won only zero-touch time; Mixamo added as baseline.
- [x] Auto-placement research: deterministic landmark detector +
      metarig fitter + validation gate for hero and quadruped, joint
      files as optional hints (docs/auto_placement.md, phases 1-4.5).
- [x] `wm.rigforge_auto_place` operator + panel button (thin UI over
      the headless fitter; docs/auto_placement.md section 11,
      tests/test_auto_place.py)
- [x] Re-run the bake-off comparison with the fitter in the loop
      (docs/archive/bakeoff/rerun.md: fit-delta 0.0 on both subjects,
      11.6 s / 9.3 s wall, zero touch — under unirig's ~7 min)
- [x] Fork-or-add-on audit (docs/archive/fork-audit.md, 2026-10-02):
      verdict is companion-feasible — zero functional edits inside
      Rigify's own files (renames + ruff autofixes proven mechanical,
      stock-vs-fork human rigs bit-identical at 706 bones / 198
      drivers / 220 widgets); only adds are 19 new files + 5 panels +
      6 submodule lines. One migration cost: renamed id-props in old
      .blends need a one-time adopt operator. Restructure pending
      owner's go-ahead (not started).
- [x] Mobile bone budget (2026-10-02): game-export MOBILE profile
      (Full/Mobile buttons) merges 95 face+twist DEF bones up to kept
      ancestors (face roots ride the topmost kept bone) on temp copies
      — 160 -> 65 joints, originals untouched, anims on kept joints.
      Both GLBs imported clean in headless engine checks (160/65 bones).
      In-game FPS-side skinning measurement stays a game-side step
      (needs a game's scenes + profiler).
- [x] Public-repo follow-ups (2026-10-02): smart paths where cheap
      (BLENDER_BIN env override, tools/run_test.sh runner, skip-if-missing for
      external-asset suites).
- [x] genforge repair-rig adapter (2026-10-05): tools/genforge_adapter.sh
      wraps auto_rig.py in genforge's adapter contract (README);
      tests/test_genforge_adapter.py (broken one-bone rig -> 160 DEF).
- [x] Game-export depth (2026-10-02): validate_export fails early
      (unweighted mesh, no DEF bones) + warns (partial weights,
      negative scale, mobile >4 influences); mobile cap-4 toggle
      (panel checkbox, auto-fix on copies); replaced legacy engine import
      check (2026-10-05) with `tools/bevy_import_check.py`.
