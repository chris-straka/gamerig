# gamerig-4: stale facts out

Repo: `gamerig` (with matching roadmap status in the workspace roadmap).

## Why

Code health cleanup:
- `tools/bevy_import_check.py` named Bevy `0.19.1` even though downstream
  projects and `rfcheck/glbkit/compat/bevy` (`rfcheck-1`) pin Bevy `0.20.0`.
- `README.md` still said `Day-0 state: pristine copy`.
- `TODO.md` still listed legacy-engine milestones in the main backlog alongside
  active items and named Bevy `0.19.1`.
- The workspace roadmap still placed assisted weights under `gamerig` instead of
  `meshweights`.

## What to build

1. `tools/bevy_import_check.py`: update the Bevy version reference from `0.19.1` to `0.20.0`.
2. `README.md`: replace the stale `Day-0 state` bullet with the current parked state.
3. `docs/archive/TODO.md`: update the Bevy checker note to `0.20.0` and keep legacy-engine /
   early milestones under `## History` so no live section names `0.19`.
4. Workspace roadmap: make `meshweights` the owner of automatic/assisted weights and note
   that `gamerig` keeps interactive Blender hint strokes.

## Evidence

- `bash gates/gamerig-4/gate.sh` prints `GATE PASS` (`ruff check` on touched files,
  `ruff format --check .`, `grep -rn "0\.19" README.md tools AGENTS.md` clean,
  no `Day-0 state` in `README.md`, no `0.19` above `## History` in `docs/archive/TODO.md`).
- GitHub Actions `tests` workflow on `main` is green.
