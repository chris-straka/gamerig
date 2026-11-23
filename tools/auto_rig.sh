#!/bin/bash
# One-command entry to the rigforge auto-rig pipeline.
#
# For an external harness (e.g. eval triage or `autorig --method gamerig`) that
# runs a single executable: this wraps the addon overlay sync + headless Blender
# invocation. CLI contract lives in tools/auto_rig.py:
#   MESH --preset biped_hero|quadruped_stalker --out FILE ...
# Exit 0 + GLB on success; exit 1 naming stage+reason on failure.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_BLENDER="$(command -v blender || true)"
BLENDER="${BLENDER_BIN:-${DEFAULT_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}}"

AUTO_TMP=""
cleanup() {
    if [ -n "$AUTO_TMP" ] && [ -d "$AUTO_TMP" ]; then
        rm -rf "$AUTO_TMP"
    fi
}
trap cleanup EXIT

if [ -n "${RF_SCRIPTS:-${RIGFORGE_SCRIPTS:-}}" ]; then
    SCRIPTS="${RF_SCRIPTS:-$RIGFORGE_SCRIPTS}"
else
    AUTO_TMP="$(mktemp -d /tmp/rf_scripts.XXXXXX)"
    SCRIPTS="$AUTO_TMP"
fi

if [ ! -x "$BLENDER" ]; then
    echo "auto_rig.sh: Blender not found at $BLENDER (set BLENDER_BIN)" >&2
    exit 1
fi

mkdir -p "$SCRIPTS/addons"
rsync -a --delete "$ROOT/rigforge/" "$SCRIPTS/addons/rigforge/"
env BLENDER_USER_SCRIPTS="$SCRIPTS" "$BLENDER" \
    --background --factory-startup \
    --python "$ROOT/tools/auto_rig.py" -- "$@"
