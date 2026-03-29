#!/bin/bash
set -euo pipefail

PLUGIN_ROOT=$(cd "$(dirname "$0")" && pwd)
PYTHON_ROOT="$PLUGIN_ROOT/python"
UPSTREAM_REPO="https://github.com/NevaMind-AI/MemU"
UPSTREAM_REF="${UPSTREAM_REF:-v1.5.1}"
TEMP_DIR="${TMPDIR:-/tmp}/memu_upstream_update.$$"

OVERLAY_BACKUP="$TEMP_DIR/overlay-backup"

UPSTREAM_PYPROJECT="$TEMP_DIR/upstream-pyproject.toml"

OVERLAY_FILES=(
  "python/src/memu/app/memorize.py"
  "python/src/memu/database/models.py"
  "python/src/memu/database/sqlite/session.py"
  "python/src/memu/database/sqlite/models.py"
  "python/src/memu/llm/openai_sdk.py"
)

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

mkdir -p "$TEMP_DIR" "$OVERLAY_BACKUP"

backup_overlay_file() {
  local rel="$1"
  local src="$PLUGIN_ROOT/$rel"
  local dst="$OVERLAY_BACKUP/$rel"
  if [ ! -f "$src" ]; then
    echo "Missing required overlay file: $src" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
}

restore_overlay_file() {
  local rel="$1"
  local src="$OVERLAY_BACKUP/$rel"
  local dst="$PLUGIN_ROOT/$rel"
  if [ ! -f "$src" ]; then
    echo "Missing overlay backup for: $rel" >&2
    exit 1
  fi
  cp "$src" "$dst"
}

echo "[1/6] Fetching upstream $UPSTREAM_REF from $UPSTREAM_REPO"
git clone --depth 1 --branch "$UPSTREAM_REF" "$UPSTREAM_REPO" "$TEMP_DIR/upstream"

if [ ! -d "$TEMP_DIR/upstream/src/memu" ]; then
  echo "Upstream source tree not found at $TEMP_DIR/upstream/src/memu" >&2
  exit 1
fi

echo "[2/6] Backing up local overlay files"
for rel in "${OVERLAY_FILES[@]}"; do
  backup_overlay_file "$rel"
done

echo "[3/6] Syncing upstream runtime package into python/src"
rsync -a --delete --exclude '__pycache__' "$TEMP_DIR/upstream/src/" "$PYTHON_ROOT/src/"

echo "[4/6] Reapplying local overlay snapshots"
for rel in "${OVERLAY_FILES[@]}"; do
  restore_overlay_file "$rel"
done

echo "[5/6] Verifying upstream baseline metadata"
cp "$TEMP_DIR/upstream/pyproject.toml" "$UPSTREAM_PYPROJECT"
grep -q 'version = "1.5.1"' "$UPSTREAM_PYPROJECT"
grep -q 'requires-python = ">=3.13"' "$UPSTREAM_PYPROJECT"

echo "[6/6] Sync complete"
echo "Next steps:"
echo "  - review python/pyproject.toml against upstream v1.5.1"
echo "  - refresh python/uv.lock if dependency pins changed"
echo "  - run python-side smoke tests before publishing"
