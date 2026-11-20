# gamerig-4: stale facts out

Repo: `gamerig` (`~/Games/_blender/gamerig`), plus the matching §3.3 row in `~/Games/_tools/roadmap.md`.

## Why

Code health item 4 in `_tools/docs/plans/gamerig.md`:
- `tools/bevy_import_check.py` still names Bevy `0.19.1` even though the games and
  `rfcheck/glbkit/compat/bevy` (`rfcheck-1`) are pinned to Bevy `0.20.0`.
- `README.md` still says `Day-0 state: pristine copy`.
- `TODO.md` still lists Godot 4 milestones in the main backlog alongside active items and names
  Bevy `0.19.1`.
- `_tools/roadmap.md` §3.3 still puts assisted weights under `gamerig` instead of `meshweights`.

## What to build

1. `tools/bevy_import_check.py`: update the Bevy version reference from `0.19.1` to `0.20.0`.
2. `README.md`: replace the stale `Day-0 state` bullet with the current parked state.
3. `TODO.md`: update the Bevy checker note to `0.20.0` and move Godot-era / early milestones into
   a dedicated `## History` section so no live section names `0.19` or `Godot 4`.
4. `_tools/roadmap.md` §3.3: make `meshweights` the owner of automatic/assisted weights and note
   that parked `gamerig` keeps only interactive Blender hint strokes.

## Evidence

- `bash gates/gamerig-4/gate.sh` prints `GATE PASS` (`ruff check` on touched files,
  `ruff format --check .`, `grep -rn "0\.19\|Godot 4" README.md tools AGENTS.md` clean,
  no `Day-0 state` in `README.md`, no `0.19` or `Godot 4` above `## History` in `TODO.md`).
- GitHub Actions `tests` workflow on `main` is green.
