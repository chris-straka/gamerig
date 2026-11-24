# gamerig (add-on `rigforge`) — Agent Standing Rules

One rules file only: this `AGENTS.md` is it — never create a `CLAUDE.md` in
this folder.

Fork of Blender's Rigify (0.6.10, copied from the Blender 5.2 app bundle),
module-renamed `rigforge` so it coexists with bundled Rigify. Built as a
standalone multi-game rigging add-on and CLI pipeline targeting game-ready rigs
(Bevy / Rust GLB export, auto-placement, voxel/assisted weights, presets).

## Standing rules

- **License:** GPL-2.0-or-later (same as upstream; see [COPYING](COPYING)).
  Never mix in MIT/permissive code with the intent to keep it permissive —
  this tree is GPL, like all Blender add-ons.
- **Public repo privacy:** `github.com/chris-straka/gamerig` is PUBLIC. Never
  commit secrets, tokens, game assets, private game/character names, machine
  hostnames, or personal filesystem paths. Resolve optional external binaries
  via environment variables (`BLENDER_BIN`, `BEVY_GLB_CHECK`, `MOTIONFORGE_BIN`,
  `RFCHECK`, `WEIGHTS_BIN`), `PATH`, or relative sibling directories.
- **Smoke test & CI matrix:** `tests/smoke.py` and all `tests/test_*.py`
  suites must stay green under `tools/run_test.sh` and `tools/run_test_bpy.py`.
  Every new `tests/test_*.py` file must be added to the matrix in
  [`.github/workflows/tests.yml`](.github/workflows/tests.yml).
- **Blender binary:** headless only (`BLENDER_BIN`, `blender` on `PATH`, or
  `/Applications/Blender.app/Contents/MacOS/Blender` on macOS) unless the user
  approves a GUI launch.
- **Lint & format gate:** `ruff check` and `ruff format --check .` must be
  clean on all files you touch (config: [`ruff.toml`](ruff.toml)). Pristine
  upstream-fork files carry intentional pre-existing lint debt (see
  [docs/archive/TODO.md](docs/archive/TODO.md)); do not bulk-reformat untouched
  upstream files, and leave every file you touch clean.

## External tool boundaries (separate repos, subprocess/data only)

- **Mesh & deformation benchmarks (`retopo`):** mesh preprocessing and
  deformation-test methodology live externally; `gamerig` keeps only synthetic
  and procedural test fixtures in `tests/`.
- **ML joint hints (`skintokens`):** optional coordinates-only `skintokens-joints/1`
  JSON hints (`skintokens joints`), plus legacy `unirig-joints/1` data fixtures
  under `tests/fixtures/hints/` (coordinates in, never external code).
- **Retargeting & motion cleanup (`motionforge`):** `tools/retarget_mixamo.py`
  imports/normalizes Mixamo clips and delegates `standardize` and `animate` to
  the `motionforge` CLI (`MOTIONFORGE_BIN` or sibling `../motion` checkout).
- **GLB & weight checks (`rfcheck`, `weights`, Bevy compat):** optional external
  validators discovered via env vars or sibling checkouts; tests skip cleanly
  when absent.
- **Game subjects:** never commit private game meshes, textures, or rigs into
  this repository.
