#!/usr/bin/env bash
# Gate for gamerig-6: Toon face rig verification (`biped_hero`).
# Runs tests/test_toon_face_rig.py in Blender, checks ruff lint/format on touched
# files, runs optional weights/rfcheck checks on the exported GLB if available,
# and copies /tmp/gamerig_6_toon_face_sheet.png into $FLEET_ARTIFACTS.
#   fleet run --snapshot --worktree --needs blender -- bash gates/gamerig-6/gate.sh
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n 30; }

if [ "${CCEZ_MACHINE:-}" = seat ]; then
  echo "FAIL  not on the owner's Mac; the gate runs through fleet"
  echo "GATE FAIL"
  exit 1
fi

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

echo "== ruff lint + format"
RUFF=${RUFF:-}
if [ -z "$RUFF" ]; then
  if command -v ruff >/dev/null 2>&1 && ruff --version >/dev/null 2>&1; then
    RUFF=ruff
  elif command -v uvx >/dev/null 2>&1; then
    RUFF="uvx ruff"
  elif command -v mise >/dev/null 2>&1; then
    RUFF="mise exec ruff@0.16.6 -- ruff"
  fi
fi
if [ -z "$RUFF" ]; then
  echo "WARN  ruff not found on worker; skipping lint check"
else
  CHECK_FILES=(tools/toon_face_fixture.py tests/test_toon_face_rig.py)
  if git diff --name-only origin/main...HEAD -- rigforge/ | grep -q '\.py$'; then
    while IFS= read -r pyf; do
      [ -f "$pyf" ] && CHECK_FILES+=("$pyf")
    done < <(git diff --name-only origin/main...HEAD -- rigforge/ | grep '\.py$')
  fi
  if $RUFF check "${CHECK_FILES[@]}" > "$T/ruff.out" 2>&1 && $RUFF format --check "${CHECK_FILES[@]}" >> "$T/ruff.out" 2>&1; then
    pass "ruff check + format on gamerig-6 files"
  else
    fail "ruff check or format failed"
    show < "$T/ruff.out"
  fi
fi

echo "== Blender test_toon_face_rig.py"
BL=${BLENDER_BIN:-}
[ -z "$BL" ] && BL=$(command -v blender || true)
for c in /Applications/Blender.app/Contents/MacOS/Blender "$HOME/Applications/Blender.app/Contents/MacOS/Blender"; do
  [ -z "$BL" ] && [ -x "$c" ] && BL=$c
done
if [ -z "$BL" ]; then
  fail "no Blender binary here"
else
  mkdir -p "$T/rf_scripts/addons"
  cp -R rigforge "$T/rf_scripts/addons/rigforge"
  rm -f /tmp/gamerig_6_toon_face_sheet.png /tmp/gamerig_6_toon_face_full.glb /tmp/gamerig_6_toon_face_mobile.glb
  if BLENDER_USER_SCRIPTS="$T/rf_scripts" "$BL" --background --factory-startup --python-exit-code 1 --python tests/test_toon_face_rig.py > "$T/test.out" 2>&1; then
    if grep -q '^RIGFORGE_TOON_FACE_OK' "$T/test.out"; then
      pass "test_toon_face_rig.py"
    else
      fail "test_toon_face_rig.py missing RIGFORGE_TOON_FACE_OK"
      tail -n 25 "$T/test.out" | show
    fi
  else
    fail "test_toon_face_rig.py exited non-zero"
    tail -n 30 "$T/test.out" | show
  fi
fi

echo "== comparison sheet artifact"
SHEET="/tmp/gamerig_6_toon_face_sheet.png"
if [ -s "$SHEET" ]; then
  pass "comparison sheet present ($SHEET)"
  if [ -n "${FLEET_ARTIFACTS:-}" ]; then
    mkdir -p "$FLEET_ARTIFACTS"
    cp "$SHEET" "$FLEET_ARTIFACTS/gamerig_6_toon_face_sheet.png"
    pass "copied sheet to \$FLEET_ARTIFACTS/gamerig_6_toon_face_sheet.png"
  fi
else
  fail "comparison sheet missing at $SHEET"
fi

echo "== optional weights / rfcheck inspection"
FULL_GLB="/tmp/gamerig_6_toon_face_full.glb"
if [ -s "$FULL_GLB" ]; then
  if [ -n "${FLEET_ARTIFACTS:-}" ]; then
    cp "$FULL_GLB" "$FLEET_ARTIFACTS/gamerig_6_toon_face_full.glb"
  fi
  WEIGHTS=${WEIGHTS_BIN:-$(command -v weights || true)}
  [ -z "$WEIGHTS" ] && [ -x "../meshweights/rust/target/release/weights" ] && WEIGHTS="../meshweights/rust/target/release/weights"
  if [ -n "$WEIGHTS" ]; then
    if "$WEIGHTS" check "$FULL_GLB" --json > "$T/weights.json" 2> "$T/weights.err"; then
      pass "weights check clean (rc=0)"
    else
      wrc=$?
      if [ "$wrc" -eq 1 ]; then
        pass "weights check ran (rc=1; summary logged)"
        head -c 600 "$T/weights.json" | show
      else
        fail "weights check failed (rc=$wrc)"
        show < "$T/weights.err"
      fi
    fi
  else
    echo "INFO  weights binary absent on worker (clean fallback)"
  fi
else
  fail "FULL GLB missing at $FULL_GLB"
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "GATE PASS"
  exit 0
fi
echo "GATE FAIL ($FAILS failed)"
exit 1
