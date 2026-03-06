-- multi_agent.sql
-- Three-agent synthetic fixture with cross references.

BEGIN TRANSACTION;
CREATE TABLE memu_memory_items (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  summary TEXT NOT NULL
);

CREATE TABLE memu_relations (
  id TEXT PRIMARY KEY,
  src_item_id TEXT NOT NULL,
  dst_item_id TEXT NOT NULL,
  relation_type TEXT NOT NULL
);

INSERT INTO memu_memory_items (id, agent_id, summary) VALUES
  ('ma-a-001', 'agent-a', 'agent a seed memory'),
  ('ma-b-001', 'agent-b', 'agent b seed memory'),
  ('ma-c-001', 'agent-c', 'agent c seed memory');

INSERT INTO memu_relations (id, src_item_id, dst_item_id, relation_type) VALUES
  ('rel-001', 'ma-a-001', 'ma-b-001', 'references'),
  ('rel-002', 'ma-b-001', 'ma-c-001', 'references'),
  ('rel-003', 'ma-c-001', 'ma-a-001', 'references');
COMMIT;
