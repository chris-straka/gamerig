#!/bin/bash
# Proof that tests/test_callers.py can fail (work item gamerig-1): it passes on
# this tree, and fails on three copies with a fault planted where a caller
# would break:
#   markers  the detector prints RIGFORGE_LANDMARKS_START, not _BEGIN (bodywrap's parser)
#   args     tools/genforge_adapter.sh drops its arguments (assetgen's repair-rig)
#   moved    tools/detect_landmarks.py moved away (bodywrap's detector_path)
#   bash tests/callers_planted.sh     (needs a Blender binary: BLENDER_BIN, `blender`, or the Mac app)
# Prints PASS/FAIL per case, ends with TEST PASS or TEST FAIL (exit 1).
set -u
here=$(cd "$(dirname "$0")/.." && pwd)
if [ "${CCEZ_MACHINE:-}" = seat ]; then echo "not on the owner's Mac; run it through fleet"; exit 2; fi
BL=${BLENDER_BIN:-}
[ -z "$BL" ] && BL=$(command -v blender || true)
for b in /Applications/Blender.app/Contents/MacOS/Blender "$HOME/Applications/Blender.app/Contents/MacOS/Blender"; do
  [ -z "$BL" ] && [ -x "$b" ] && BL=$b
done
[ -n "$BL" ] || { echo "FAIL  no Blender binary here"; echo "TEST FAIL"; exit 1; }
export BLENDER_BIN=$BL
tmp=$(mktemp -d "${TMPDIR:-/tmp}/rf-callers-planted.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
FAILS=0

run() {  # run DIR -> 0 when test_callers passes there
  local dir=$1 scripts=$tmp/scripts-$(basename "$1")
  mkdir -p "$scripts/addons"
  rm -rf "$scripts/addons/rigforge"
  cp -R "$dir/rigforge" "$scripts/addons/rigforge"
  BLENDER_USER_SCRIPTS="$scripts" "$BL" --background --factory-startup --python-exit-code 1 --python "$dir/tests/test_callers.py" > "$tmp/$(basename "$dir").log" 2>&1
  grep -q '^RIGFORGE_CALLERS_OK' "$tmp/$(basename "$dir").log"
}
why() {  # why CASE: the test's own failure line, or the error it crashed with
  grep -E '^RIGFORGE_CALLERS_FAIL|Error|Traceback|^  File ' "$tmp/$1.log" | grep -v -E 'RIGFORGE: initial load order' | tail -n 12
}
copy() {  # copy NAME -> a copy of the tree (tracked files only)
  mkdir -p "$tmp/$1"
  (cd "$here" && git ls-files -z | xargs -0 tar cf - 2>/dev/null) | (cd "$tmp/$1" && tar xf -)
  echo "$tmp/$1"
}

head=$(copy head)
if run "$head"; then echo "PASS  head: test_callers passes"; else echo "FAIL  head: test_callers fails"; why head | sed 's/^/      /'; FAILS=$((FAILS + 1)); fi

d=$(copy markers)
sed -i.bak 's/print("RIGFORGE_LANDMARKS_BEGIN")/print("RIGFORGE_LANDMARKS_START")/' "$d/rigforge/auto_place/detect_landmarks.py"
d=$(copy args)
sed -i.bak 's/-- "\$@"/--/' "$d/tools/genforge_adapter.sh"
d=$(copy moved)
mv "$d/tools/detect_landmarks.py" "$d/tools/landmarks_cli.py"
for case in markers args moved; do
  d=$tmp/$case
  if cmp -s "$head/rigforge/auto_place/detect_landmarks.py" "$d/rigforge/auto_place/detect_landmarks.py" && cmp -s "$head/tools/genforge_adapter.sh" "$d/tools/genforge_adapter.sh" && [ -f "$d/tools/detect_landmarks.py" ]; then
    echo "FAIL  $case: the fault was not planted"; FAILS=$((FAILS + 1)); continue
  fi
  if run "$d"; then
    echo "FAIL  $case: test_callers passes with the fault planted"; FAILS=$((FAILS + 1))
  else
    echo "PASS  $case: test_callers fails: $(why "$case" | tail -n 1 | cut -c1-160)"
  fi
done
echo
if [ "$FAILS" -eq 0 ]; then echo "TEST PASS"; exit 0; fi
echo "TEST FAIL ($FAILS failed)"
exit 1
