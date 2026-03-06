-- corrupted_schema.sql
-- Schema intentionally missing legacy/new columns (e.g., agentName).

BEGIN TRANSACTION;
CREATE TABLE memu_memory_items (
  id TEXT PRIMARY KEY,
  summary TEXT NOT NULL
);

CREATE TABLE memu_memory_categories (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL
);

INSERT INTO memu_memory_items (id, summary) VALUES ('corrupt-001', 'row without expected columns');
COMMIT;
