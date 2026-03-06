#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
OUTPUT_DB="${SCRIPT_DIR}/v026_real.db"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/memu-v026-fixture-XXXXXX")"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

for cmd in git python3; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Error: required command not found: ${cmd}" >&2
    exit 1
  fi
done

echo "[1/4] Checking out v0.2.6 in temporary directory..."
git clone --quiet --no-checkout "${REPO_ROOT}" "${TMP_DIR}/repo"
git -C "${TMP_DIR}/repo" checkout --quiet "v0.2.6"

SQL_FILE="${TMP_DIR}/v026_fixture.sql"

echo "[2/4] Generating v0.2.6-compatible SQLite schema..."
cat > "${SQL_FILE}" <<'SQL'
PRAGMA foreign_keys = ON;

CREATE TABLE memu_resources (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  updated_at TEXT,
  url TEXT NOT NULL,
  modality TEXT NOT NULL,
  local_path TEXT NOT NULL,
  caption TEXT,
  embedding_json TEXT
);

CREATE TABLE memu_memory_categories (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  updated_at TEXT,
  name TEXT NOT NULL,
  description TEXT NOT NULL,
  embedding_json TEXT,
  summary TEXT
);

CREATE TABLE memu_memory_items (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  updated_at TEXT,
  resource_id TEXT,
  memory_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  embedding_json TEXT,
  happened_at TEXT,
  extra TEXT,
  FOREIGN KEY (resource_id) REFERENCES memu_resources(id)
);

CREATE TABLE memu_category_items (
  id TEXT PRIMARY KEY,
  created_at TEXT,
  updated_at TEXT,
  item_id TEXT NOT NULL,
  category_id TEXT NOT NULL,
  UNIQUE(item_id, category_id),
  FOREIGN KEY (item_id) REFERENCES memu_memory_items(id),
  FOREIGN KEY (category_id) REFERENCES memu_memory_categories(id)
);

CREATE INDEX ix_memu_memory_categories_name ON memu_memory_categories(name);

INSERT INTO memu_resources (id, created_at, updated_at, url, modality, local_path, caption, embedding_json) VALUES
  ('res-001', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'https://example.com/r1', 'text', '/tmp/r1.md', 'resource 1', '[0.1,0.2]'),
  ('res-002', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'https://example.com/r2', 'text', '/tmp/r2.md', 'resource 2', '[0.2,0.3]'),
  ('res-003', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'https://example.com/r3', 'text', '/tmp/r3.md', 'resource 3', '[0.3,0.4]');

INSERT INTO memu_memory_categories (id, created_at, updated_at, name, description, embedding_json, summary) VALUES
  ('cat-001', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'personal_info', 'Personal facts', '[0.1,0.1]', 'profile details'),
  ('cat-002', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'preferences', 'Likes/dislikes', '[0.1,0.2]', 'preference summary'),
  ('cat-003', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'activities', 'Hobbies and tasks', '[0.2,0.1]', 'activity summary'),
  ('cat-004', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'knowledge', 'Facts and learnings', '[0.2,0.2]', 'knowledge summary'),
  ('cat-005', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'goals', 'Goals and plans', '[0.3,0.2]', 'goal summary');

INSERT INTO memu_memory_items (id, created_at, updated_at, resource_id, memory_type, summary, embedding_json, happened_at, extra) VALUES
  ('mem-001', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-001', 'profile', 'User is based in Berlin', '[0.1,0.1]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-002', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-001', 'preference', 'Prefers concise answers', '[0.1,0.2]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-003', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-001', 'activity', 'Runs every morning', '[0.2,0.1]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-004', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-002', 'knowledge', 'Knows Python and SQL', '[0.2,0.2]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-005', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-002', 'goal', 'Wants to learn Rust', '[0.3,0.2]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-006', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-002', 'event', 'Attended a DB meetup', '[0.3,0.1]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-007', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-003', 'profile', 'Uses Linux daily', '[0.1,0.3]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-008', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-003', 'preference', 'Dislikes noisy logs', '[0.2,0.3]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-009', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-003', 'activity', 'Writes integration tests weekly', '[0.3,0.3]', '2026-01-01T00:00:00Z', '{}'),
  ('mem-010', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'res-003', 'knowledge', 'Understands SQLite pragmas', '[0.4,0.3]', '2026-01-01T00:00:00Z', '{}');

INSERT INTO memu_category_items (id, created_at, updated_at, item_id, category_id) VALUES
  ('rel-001', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-001', 'cat-001'),
  ('rel-002', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-002', 'cat-002'),
  ('rel-003', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-003', 'cat-003'),
  ('rel-004', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-004', 'cat-004'),
  ('rel-005', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-005', 'cat-005'),
  ('rel-006', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-006', 'cat-003'),
  ('rel-007', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-007', 'cat-001'),
  ('rel-008', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-008', 'cat-002'),
  ('rel-009', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-009', 'cat-003'),
  ('rel-010', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'mem-010', 'cat-004');
SQL

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "${TMP_DIR}/v026_real.db" < "${SQL_FILE}"
else
  python3 - "${TMP_DIR}/v026_real.db" "${SQL_FILE}" <<'PY'
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1])
sql_path = Path(sys.argv[2])
conn = sqlite3.connect(db_path)
try:
    conn.executescript(sql_path.read_text(encoding="utf-8"))
    conn.commit()
finally:
    conn.close()
PY
fi

echo "[3/4] Copying fixture to ${OUTPUT_DB}"
cp "${TMP_DIR}/v026_real.db" "${OUTPUT_DB}"

python3 - "${OUTPUT_DB}" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
try:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(memu_memory_items)")
    cols = [row[1] for row in cur.fetchall()]
    if "agentName" in cols:
        raise SystemExit("Validation failed: agentName unexpectedly exists in memu_memory_items")
finally:
    conn.close()
PY

echo "[4/4] Done. Fixture generated: ${OUTPUT_DB}"
