# gamerig (add-on `rigforge`)

Fork of Blender's Rigify (0.6.10, copied from the Blender 5.2 app bundle),
module-renamed to `rigforge` so it coexists with bundled Rigify. Built for
standalone multi-game character and creature rigging: deterministic metarig
auto-placement, voxel skin weights, weight editing tools, presets, and
deform-only GLB export for Bevy (Rust) and other game engines.

- **License:** GPL-2.0-or-later (see [COPYING](COPYING); Blender add-ons must be GPL).
- **Upstream:** copied from Blender's bundled Rigify rather than vendored as a
  remote; re-copy from a newer Blender release when rebasing.
- **Docs index:** see [docs/README.md](docs/README.md) for architecture,
  auto-placement details, weight-assistance notes, and archived records.

## Layout

- `rigforge/` — Blender add-on package (zip this folder for Install-from-Disk).
  - `rigforge/auto_place/` — landmark detector, biped/quadruped metarig fitter,
    validation gate, preset builder, and joint-hint arbitration.
  - `rigforge/weights/` — geodesic voxel weights, nudge strokes, piece
    transfer with inpainting, and weight cleanup ops.
  - `rigforge/operators/` — Blender operators (`auto_place`, `game_export`,
    `voxel_weights`, `nudge`, `transfer`, `cleanup`, `diagnose`).
  - `rigforge/metarigs/` — built-in metarigs plus `biped_hero` and
    `quadruped_stalker` presets.
- `tools/` — headless CLI shims and external pipeline adapters (`auto_rig.py`,
  `auto_rig.sh`, `retarget_mixamo.py`, `genforge_adapter.sh`,
  `bevy_import_check.py`).
- `tests/` — headless Blender and `bpy` test suites.
- `bench/` — joint-placement benchmark harness and historical scorecards.

## Presets & Auto-Placement

- **Presets:** `biped_hero` (stylized biped) and `quadruped_stalker`
  (quadruped) metarigs in **Add > Armature > Rigforge Meta-Rigs**. Generated
  modules — rebuild after tuning constants:

```sh
BLENDER_USER_SCRIPTS=/tmp/rf_scripts \
  /Applications/Blender.app/Contents/MacOS/Blender --background \
  --factory-startup --python tools/make_presets.py -- .
```

- **UI Auto-Place:** with a preset metarig active and subject mesh(es)
  selected, run **Armature Properties > Rigforge > Auto-Place to Mesh**
  (`bpy.ops.wm.rigforge_auto_place()`). Runs landmark detection, fitting, and
  strict validation in place (restoring the metarig untouched on failure).
- **Headless Auto-Rig:** single-command mesh-in, GLB-out pipeline:

```sh
tools/auto_rig.sh MESH.glb --preset biped_hero|quadruped_stalker --out RIGGED.glb
```

See [docs/auto_placement.md](docs/auto_placement.md) for the detector, fitter,
validation gate, and SkinTokens (`skintokens-joints/1`) hint details.

## Weight Assistance & Diagnostics

- **Voxel Auto-Weights** (`wm.rigforge_voxel_weights`): geodesic voxel binding
  for closed and stacked-shell meshes without air-gap bleeding.
- **Nudge Strokes** (`wm.rigforge_nudge`): pin or exclude vertices for a bone
  and smoothly re-solve surrounding weights.
- **Piece Transfer** (`wm.rigforge_piece_transfer`): copy nearby body weights
  onto garments/accessories and inpaint hanging regions.
- **Cleanup** (`wm.rigforge_cleanup`): limit influences, normalize, prune,
  mirror L/R, and smooth (operating on deform-bone groups by default).
- **Diagnose Metarig** (`wm.rigforge_diagnose_metarig`): pre-flight checks for
  unknown rig types (with suggestions), parented/typed roots, and stray bones.

## Game Export, Retargeting & Adapter

- **Game Export:** with a generated rig active, **Armature Properties >
  Rigforge Game Export** (`bpy.ops.wm.rigforge_game_export(filepath=...)`)
  exports a deform-bones-only GLB (FULL or MOBILE merged-face/twist profile with
  optional 4-influence capping).
- **Mixamo Retargeting:** `tools/retarget_mixamo.py` imports/normalizes Mixamo
  FBX clips and delegates standardization and retargeting to the `motionforge`
  CLI (`MOTIONFORGE_BIN`, `PATH`, or sibling `../motion` checkout).
- **Repair-Rig Adapter (`repair-rig`):** drops a broken armature while keeping
  meshes, materials, and textures, then re-rigs with `tools/auto_rig.py` and
  writes `RESULT.json`:

```sh
tools/genforge_adapter.sh repair-rig IN.glb OUT.glb RESULT.json \
  [--class humanoid|quadruped|custom]
```

Exit codes: `0` rigged, `1` an auto-rig stage failed (`ok: false` in JSON), `2`
usage/input error (no result written).

## Testing & CI

Smoke test (full Blender install):

```sh
tools/run_test.sh tests/smoke.py
```

Headless test suites (`tests/test_*.py`):

```sh
tools/run_test.sh tests/test_game_export.py
```

Environment overrides (with standard platform/sibling fallbacks when unset):
- `BLENDER_BIN` — Blender executable (default: `blender` on `PATH` or
  `/Applications/Blender.app/Contents/MacOS/Blender` on macOS).
- `RIGFORGE_SCRIPTS` / `RF_SCRIPTS` — custom `BLENDER_USER_SCRIPTS` overlay dir.
- `BEVY_GLB_CHECK` — headless Bevy 0.20.0 glTF compatibility checker binary
  used by `tools/bevy_import_check.py` (skipped when absent).
- `MOTIONFORGE_BIN`, `RFCHECK`, `WEIGHTS_BIN` — optional external validation
  and retargeting binaries (skipped when absent).

Without a full Blender binary (e.g. Linux CI or headless Python environments),
use the `bpy` wheel (`pip install bpy`, CPython 3.11):

```sh
python tools/run_test_bpy.py tests/test_cleanup.py
```

CI ([.github/workflows/tests.yml](.github/workflows/tests.yml)) checks
formatting with `ruff format --check .`, verifies every `tests/test_*.py`
suite appears in the matrix, and runs the full matrix against `bpy`.
