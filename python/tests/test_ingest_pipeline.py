import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path

from memu.app.ingest import ingest_document
from memu.app.settings import DatabaseConfig, MemUConfig
from memu.chunking import chunk_text
from memu.database.hybrid_factory import HybridDatabaseManager
from tests.shared_user_model import SharedUserModel


class _DummyEmbedClient:
    async def embed(self, inputs: list[str]):
        return [[0.1, 0.2, 0.3] for _ in inputs]


def test_chunk_text_overlap_metadata():
    text = " ".join(["token"] * 200)
    chunks = chunk_text(text, chunk_size=32, overlap=8)
    assert chunks
    assert chunks[0].start_token == 0
    assert chunks[0].index == 0
    for i, ch in enumerate(chunks):
        assert ch.index == i
        assert ch.end_token > ch.start_token
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start_token < prev.end_token


def test_ingest_document_writes_shared_rows():
    with tempfile.TemporaryDirectory(prefix="memu_ingest_test_") as td:
        root = Path(td)
        db_file = root / "shared.db"
        text_file = root / "doc.txt"
        text_file.write_text("hello world\n" * 800, encoding="utf-8")
        previous_memory_root = os.environ.get("MEMU_MEMORY_ROOT")
        os.environ["MEMU_MEMORY_ROOT"] = str(root)
        try:
            cfg = MemUConfig(chunkSize=64, chunkOverlap=8)
            db_cfg = DatabaseConfig.model_validate(
                {
                    "metadata_store": {
                        "provider": "sqlite",
                        "dsn": f"sqlite:///{db_file}",
                    }
                }
            )
            manager = HybridDatabaseManager(config=cfg, db_config=db_cfg, user_model=SharedUserModel)
            embed_client = _DummyEmbedClient()

            result = asyncio.run(
                ingest_document(
                    file_path=str(text_file),
                    agent_id="main",
                    user_id="user-1",
                    config=cfg,
                    db_manager=manager,
                    embed_client=embed_client,
                )
            )

            shared_db_path = root / "shared" / "memu.db"
            conn = sqlite3.connect(shared_db_path)
            cur = conn.cursor()
            cur.execute("SELECT filename, owner_agent_id FROM documents WHERE id = ?", (result.document_id,))
            doc_row = cur.fetchone()
            assert doc_row is not None
            assert doc_row[0] == text_file.name
            assert doc_row[1] == "main"

            cur.execute(
                "SELECT COUNT(*), MIN(start_token), MAX(end_token), MIN(filename), MIN(owner_agent_id) "
                "FROM document_chunks WHERE document_id = ?",
                (result.document_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == result.chunk_count
            assert row[1] == 0
            assert row[2] > 0
            assert row[3] == text_file.name
            assert row[4] == "main"
            conn.close()
        finally:
            if previous_memory_root is None:
                os.environ.pop("MEMU_MEMORY_ROOT", None)
            else:
                os.environ["MEMU_MEMORY_ROOT"] = previous_memory_root
