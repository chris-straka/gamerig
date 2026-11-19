Make gamerig's hint tests use fixtures owned here and run every time
(work items gamerig-3 and unirig-mac-1 in game-tools
`docs/plans/gamerig.md` and `docs/plans/unirig-mac.md`).

`tests/test_auto_placement.py` reads two joint samples from other repos
and skips its checks when they are missing: block 10h (lines 690-754)
reads `~/Games/_blender/unirig-mac/tools/joints_samples/stalker_seed42.json`
(a retired repo), block 10i (lines 756-801) reads
`~/Games/_blender/skintokens/tools/joints_samples/stalker_seed0.json`.
Copies of both are now committed here as
`tests/fixtures/hints/unirig_stalker_seed42.json` and
`tests/fixtures/hints/skintokens_stalker_seed0.json`.

Do exactly this:

1. In `tests/test_auto_placement.py`, `HINTS_SAMPLE` and `ST_HINTS` point
   at those two fixtures, found relative to the test file (the way the
   test finds its other files, through `__file__` or the repo root it
   already uses), not through `~`.
2. Remove the two skip branches (`if not os.path.exists(...): print(...
   SKIP ...) else:`): the blocks always run, and a missing fixture is a
   failure through the test's own `check(...)`. Keep every `check(...)`
   and every printed line as it is; only un-indent the bodies.
3. Reword the comments of blocks 10h and 10i and `tools/auto_rig.sh` line 4
   so they no longer name unirig-mac (say "the retired UniRig port" or
   "an external harness"). Keep what they say otherwise.
4. Keep `ruff format --check tests/test_auto_placement.py` clean.

Change only `tests/test_auto_placement.py` and `tools/auto_rig.sh`. Out
of scope: the fixtures, the other tests, `.github/`, `AGENTS.md`, every
other file. Leave the other skip-if-absent subjects (the downloaded
meshes, the stalker mesh) as they are.

Evidence that it is done: the gate `gates/gamerig-3/gate.sh` prints GATE
PASS on a clean checkout. It checks the scope, that no `check(` line was
dropped, that nothing in `tests/` or `tools/` names unirig-mac or reads
another repo's folder, that no hint block skips, that ruff format is
clean, and that `test_auto_placement.py` passes in Blender with both hint
blocks printing their results.

Never edit anything under `gates/`, `tests/fixtures/` or `.github/`. Read
`tests/test_auto_placement.py` in parts (it is about 800 lines). If a test
looks wrong, say so in your reply; do not work around it.
