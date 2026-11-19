#!/usr/bin/env bash
# Gate for gamerig-3 + unirig-mac-1 (game-tools docs/plans/gamerig.md,
# unirig-mac.md): the hint tests read fixtures owned here
# (tests/fixtures/hints/, copied from the retired unirig-mac and from
# skintokens), run every time instead of skipping when another repo's file
# is missing, and nothing here names unirig-mac.
# Prints PASS/FAIL per check, ends with GATE PASS or GATE FAIL. Needs a
# Blender binary (BLENDER_BIN, `blender`, or the Mac app) and ruff (or uvx).
#   fleet run --snapshot --cores 4 -w -- bash gates/gamerig-3/gate.sh
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n 25; }
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
BASE=$(git log -1 --format=%H -- gates/gamerig-3)
[ -n "$BASE" ] || { fail "cannot find the commit that added the gate"; BASE=HEAD; }
if [ "${CCEZ_MACHINE:-}" = seat ]; then echo "FAIL  not on the owner's Mac; the gate runs through fleet"; echo "GATE FAIL"; exit 1; fi

echo "== scope"
for p in gates tests/fixtures tests/test_callers.py tests/callers_planted.sh .github; do
  if git diff --quiet "$BASE" -- "$p" && [ -z "$(git status --porcelain -- "$p")" ]; then pass "$p unchanged"; else fail "$p changed since the gate was written"; fi
done
other=$( (git diff --name-only "$BASE"; git status --porcelain | cut -c4-) | grep -v -E '^(tests/test_auto_placement\.py|tools/auto_rig\.sh)$' | sort -u)
if [ -z "$other" ]; then pass "only tests/test_auto_placement.py and tools/auto_rig.sh changed"; else fail "other files changed:"; echo "$other" | show; fi
n=$(git grep -c -E '^ *check\(' -- tests/test_auto_placement.py | awk -F: '{s+=$NF} END {print s+0}')
b=$(git grep -c -E '^ *check\(' "$BASE" -- tests/test_auto_placement.py | awk -F: '{s+=$NF} END {print s+0}')
if [ "$n" -ge "$b" ]; then pass "test_auto_placement.py: $n check( lines (base $b)"; else fail "test_auto_placement.py: $n check( lines, fewer than base $b"; fi

echo "== nothing reads from or names another repo's files"
left=$(git grep -n "unirig-mac" -- tests tools)
if [ -z "$left" ]; then pass "no unirig-mac in tests/ or tools/"; else fail "unirig-mac left:"; echo "$left" | show; fi
left=$(git grep -n -E 'Games/_blender/(unirig-mac|skintokens)' -- tests)
if [ -z "$left" ]; then pass "the tests read no other repo's folder"; else fail "tests still read another repo:"; echo "$left" | show; fi
left=$(git grep -n -E 'hints: SKIP' -- tests/test_auto_placement.py)
if [ -z "$left" ]; then pass "the hint blocks have no skip"; else fail "the hint blocks still skip:"; echo "$left" | show; fi
for f in unirig_stalker_seed42.json skintokens_stalker_seed0.json; do
  if git grep -q "$f" -- tests/test_auto_placement.py; then pass "test_auto_placement.py reads tests/fixtures/hints/$f"; else fail "test_auto_placement.py does not read $f"; fi
done

echo "== lint"
RUFF=$(command -v ruff || true)
[ -z "$RUFF" ] && command -v uvx > /dev/null && RUFF="uvx ruff"
if [ -z "$RUFF" ]; then
  fail "no ruff (or uvx) here"
elif $RUFF format --check tests/test_auto_placement.py > "$T/fmt.out" 2>&1; then
  pass "ruff format --check tests/test_auto_placement.py"
else
  fail "ruff format"; show < "$T/fmt.out"
fi

echo "== the test, in Blender"
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
  BLENDER_USER_SCRIPTS=$T/rf_scripts "$BL" --background --factory-startup --python tests/test_auto_placement.py > "$T/ap.out" 2>&1
  if grep -q '^RIGFORGE_AUTOPLACE_OK' "$T/ap.out"; then pass "test_auto_placement.py"; else fail "test_auto_placement.py"; grep -E 'RIGFORGE_AUTOPLACE_FAIL|Error|Traceback' "$T/ap.out" | grep -v 'load order' | tail -n 10 | show; fi
  if grep -q -E '^hints: [0-9]+ trusted' "$T/ap.out"; then pass "the unirig hints block ran ($(grep -m1 -E '^hints: ' "$T/ap.out"))"; else fail "the unirig hints block did not run: $(grep -m1 -E '^hints' "$T/ap.out")"; fi
  if grep -q -E '^skintokens hints: ' "$T/ap.out" && ! grep -q -E '^skintokens hints: SKIP' "$T/ap.out"; then pass "the skintokens hints block ran ($(grep -m1 -E '^skintokens hints: ' "$T/ap.out" | cut -c1-120))"; else fail "the skintokens hints block did not run: $(grep -m1 -E '^skintokens hints' "$T/ap.out")"; fi
fi

echo
if [ "$FAILS" -eq 0 ]; then echo "GATE PASS"; exit 0; fi
echo "GATE FAIL ($FAILS failed)"
exit 1
