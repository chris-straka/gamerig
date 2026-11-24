# Documentation Index

Overview and architecture documentation for `gamerig` (the `rigforge` Blender
add-on and headless auto-rigging tools).

## Start here

- [Project README](../README.md) — installation, CLI entry points, operators,
  environment variables, and test execution.
- [Agent Standing Rules](../AGENTS.md) — licensing, public-repo privacy rules,
  lint/format gates, and external tool boundaries.
- [CI Test Workflow](../.github/workflows/tests.yml) — formatting gate and
  headless `bpy` suite matrix.

## Technical references

- [Auto-Placement Pipeline](auto_placement.md) — slice/blob landmark detector,
  biped/quadruped metarig fitter, strict validation gate, SkinTokens joint
  hints, and `wm.rigforge_auto_place` UI integration.
- [Weight Assistance Plan & Findings](weight-assist-plan.md) — geodesic voxel
  auto-weights (W1/W1b), logit-space nudge strokes (W2), piece transfer with
  inpainting (W3), and weight cleanup operators (W4).
- [CC0 Joint Placement Benchmark (2026-11-19)](../bench/history/2026-11-19.md) —
  tracked scorecard comparing `auto_rig.py` against a scaled stock metarig.

## Archive (historical milestones & audits)

- [Historical TODO & Milestones](archive/TODO.md) — completed bring-up log,
  refactor notes, and upstream-watch reminder.
- [Fork-vs-Companion Audit (2026-10-02)](archive/fork-audit.md) — git
  archaeology proving `rigforge` adds cleanly over stock Rigify 0.6.10.
- [Bake-off Comparison (2026-09-30)](archive/bakeoff/final.md) — baseline
  comparison (`rigforge` vs retired UniRig port vs Mixamo).
- [Bake-off Interim Notes](archive/bakeoff/rigforge_half.md) — pre-fitter
  rigforge measurements.
- [Bake-off Re-run with Fitter (2026-10-01)](archive/bakeoff/rerun.md) —
  zero-touch fit-delta closure measurements.
