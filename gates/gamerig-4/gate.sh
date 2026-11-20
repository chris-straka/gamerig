#!/usr/bin/env bash
# Gate for gamerig-4: stale facts out (Bevy 0.20.0 pin, no Day-0 README claim, Godot items in
# TODO.md History, ruff clean).
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
show() { sed 's/^/      /' | head -n 30; }

RUFF=${RUFF:-}
if [ -z "$RUFF" ]; then
  if command -v ruff >/dev/null 2>&1 && ruff --version >/dev/null 2>&1; then
    RUFF=ruff
  elif command -v mise >/dev/null 2>&1; then
    RUFF="mise exec ruff@0.16.6 -- ruff"
  fi
fi

echo "== Bevy 0.20 reference in tools/bevy_import_check.py"
if grep -q "Bevy 0\.20" tools/bevy_import_check.py && ! grep -q "0\.19" tools/bevy_import_check.py; then
  pass "tools/bevy_import_check.py names Bevy 0.20 (no 0.19)"
else
  fail "tools/bevy_import_check.py still has stale Bevy pin"
  grep -n "Bevy\|0\.19\|0\.20" tools/bevy_import_check.py | show
fi

echo "== no Day-0 state claim in README.md"
if ! grep -q "Day-0 state" README.md; then
  pass "README.md has no stale Day-0 state line"
else
  fail "README.md still contains Day-0 state"
  grep -n "Day-0" README.md | show
fi

echo "== no 0.19 or Godot 4 in README.md, tools/, AGENTS.md"
hits=$(grep -rn "0\.19\|Godot 4" README.md tools AGENTS.md || true)
if [ -z "$hits" ]; then
  pass "README.md, tools/, AGENTS.md have no 0.19 or Godot 4 hits"
else
  fail "stale 0.19 or Godot 4 hits in README.md / tools / AGENTS.md:"
  echo "$hits" | show
fi

echo "== TODO.md: Godot 4 items moved under ## History, no 0.19 in live items"
if grep -q "^## History" TODO.md; then
  live=$(sed '/^## History/,$d' TODO.md)
  bad=$(printf '%s\n' "$live" | grep -n "0\.19\|Godot 4" || true)
  if [ -z "$bad" ]; then
    pass "TODO.md live sections have no 0.19 or Godot 4 hits (moved under ## History)"
  else
    fail "TODO.md still has 0.19 or Godot 4 above ## History:"
    echo "$bad" | show
  fi
else
  fail "TODO.md has no ## History section"
fi

echo "== ruff lint + format"
if [ -z "$RUFF" ]; then
  fail "ruff not found"
else
  if $RUFF check tools/bevy_import_check.py >/dev/null 2>&1 && $RUFF format --check . >/dev/null 2>&1; then
    pass "ruff check tools/bevy_import_check.py && ruff format --check ."
  else
    fail "ruff check or format failed:"
    $RUFF check tools/bevy_import_check.py 2>&1 | show
    $RUFF format --check . 2>&1 | show
  fi
fi

if [ $FAILS -eq 0 ]; then echo "GATE PASS"; exit 0; else echo "GATE FAIL ($FAILS)"; exit 1; fi
