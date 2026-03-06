from __future__ import annotations

import logging
import os
import hashlib
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from memu.app.ingest import IngestDocumentResult, ingest_document
from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.hybrid_query import HybridSearchHit, hybrid_search, rank_rows
from memu.database.hybrid_schema import get_hybrid_schema_set
from memu.database.shared_db import build_document_search_query
from memu.database.lazy_db import ConnectionPool, execute_with_locked_retry, fetch_with_locked_retry
from memu.database.sqlite import build_sqlite_database
from memu.database.sqlite.sqlite import SQLiteStore

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_ROOT = "~/.openclaw/" + "memU" + "data/memory"


def _default_memory_root(memory_root: str | Path | None = None) -> Path:
    if memory_root is not None:
        return Path(memory_root).expanduser()
    raw = os.getenv("MEMU_MEMORY_ROOT", DEFAULT_MEMORY_ROOT)
    return Path(raw).expanduser()


class HybridDatabaseManager:
    def __init__(
        self,
        *,
        config: MemUConfig,
        db_config: DatabaseConfig,
        user_model: type[BaseModel],
        memory_root: str | Path | None = None,
        idle_timeout: int = 300,
    ) -> None:
        self.config = config
        self.db_config = db_config
        self.user_model = user_model
        self.memory_root = _default_memory_root(memory_root)
        self.connection_pool = ConnectionPool(max_size=20, idle_timeout=idle_timeout)
        self._schema = get_hybrid_schema_set()

        fallback_cfg = db_config.model_copy(deep=True)
        fallback_cfg.metadata_store = fallback_cfg.metadata_store.model_copy(
            update={
                "provider": "sqlite",
                "dsn": self._legacy_shared_dsn(),
            }
        )
        self._primary_store: SQLiteStore = build_sqlite_database(config=fallback_cfg, user_model=user_model)
        self._ensure_shared_schema()

    def _legacy_shared_dsn(self) -> str:
        configured_dsn = self.db_config.metadata_store.dsn
        if configured_dsn:
            return configured_dsn
        legacy_db = self.memory_root / "shared" / "memu.db"
        legacy_db.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{legacy_db}"

    def _agent_db_path(self, agent_id: str) -> Path:
        return (self.memory_root / agent_id / "memu.db").expanduser()

    def _shared_db_path(self) -> Path:
        return (self.memory_root / "shared" / "memu.db").expanduser()

    def _ensure_agent_schema(self, agent_id: str) -> None:
        db_path = self._agent_db_path(agent_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        create_memories = (
            "CREATE TABLE IF NOT EXISTS memories ("
            "id TEXT PRIMARY KEY, "
            "agent_id TEXT NOT NULL, "
            "user_id TEXT NOT NULL, "
            "content TEXT, "
            "embedding BLOB, "
            "created_at INTEGER"
            ")"
        )
        create_idx = "CREATE INDEX IF NOT EXISTS idx_agent_user ON memories(agent_id, user_id)"

        with self.connection_pool.checkout(f"agent:{agent_id}", str(db_path)) as db:
            execute_with_locked_retry(db, create_memories)
            execute_with_locked_retry(db, create_idx)

    def _ensure_shared_schema(self) -> None:
        db_path = self._shared_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        create_documents = (
            "CREATE TABLE IF NOT EXISTS documents ("
            "id TEXT PRIMARY KEY, "
            "filename TEXT NOT NULL, "
            "owner_agent_id TEXT NOT NULL, "
            "content_hash TEXT, "
            "mtime REAL, "
            "created_at INTEGER"
            ")"
        )
        create_chunks = (
            "CREATE TABLE IF NOT EXISTS document_chunks ("
            "id TEXT PRIMARY KEY, "
            "document_id TEXT NOT NULL, "
            "chunk_index INTEGER, "
            "start_token INTEGER DEFAULT 0, "
            "end_token INTEGER DEFAULT 0, "
            "content TEXT, "
            "embedding BLOB, "
            "filename TEXT, "
            "owner_agent_id TEXT, "
            "created_at INTEGER DEFAULT 0, "
            "FOREIGN KEY (document_id) REFERENCES documents(id)"
            ")"
        )
        create_owner_idx = "CREATE INDEX IF NOT EXISTS idx_owner_agent ON documents(owner_agent_id)"
        create_chunks_doc_idx = "CREATE INDEX IF NOT EXISTS idx_document_chunks_doc_id ON document_chunks(document_id)"
        create_chunks_owner_idx = (
            "CREATE INDEX IF NOT EXISTS idx_document_chunks_owner_agent ON document_chunks(owner_agent_id)"
        )

        with self.connection_pool.checkout("shared", str(db_path)) as db:
            execute_with_locked_retry(db, create_documents)
            execute_with_locked_retry(db, create_chunks)
            columns = fetch_with_locked_retry(db, "PRAGMA table_info(documents)", ())
            column_names = {str(col[1]) for col in columns if len(col) > 1}
            has_owner_agent_id = "owner_agent_id" in column_names
            if not has_owner_agent_id:
                execute_with_locked_retry(db, "ALTER TABLE documents ADD COLUMN owner_agent_id TEXT")
            if "content_hash" not in column_names:
                execute_with_locked_retry(db, "ALTER TABLE documents ADD COLUMN content_hash TEXT")
            if "mtime" not in column_names:
                execute_with_locked_retry(db, "ALTER TABLE documents ADD COLUMN mtime REAL")

            missing_metadata_rows = fetch_with_locked_retry(
                db,
                "SELECT id, created_at FROM documents WHERE content_hash IS NULL OR mtime IS NULL",
                (),
            )
            for document_id, created_at in missing_metadata_rows:
                chunk_rows = fetch_with_locked_retry(
                    db,
                    "SELECT content FROM document_chunks WHERE document_id = ? ORDER BY chunk_index ASC, created_at ASC",
                    (document_id,),
                )
                digest = hashlib.sha256()
                for chunk_row in chunk_rows:
                    content = ""
                    if chunk_row and len(chunk_row) > 0 and chunk_row[0] is not None:
                        content = str(chunk_row[0])
                    digest.update(content.encode("utf-8"))
                content_hash = digest.hexdigest()
                fallback_mtime = float(created_at) if created_at is not None else 0.0
                execute_with_locked_retry(
                    db,
                    (
                        "UPDATE documents "
                        "SET content_hash = CASE WHEN content_hash IS NULL OR content_hash = '' THEN ? ELSE content_hash END, "
                        "mtime = CASE WHEN mtime IS NULL THEN ? ELSE mtime END "
                        "WHERE id = ?"
                    ),
                    (content_hash, fallback_mtime, document_id),
                )

            chunk_columns = fetch_with_locked_retry(db, "PRAGMA table_info(document_chunks)", ())
            chunk_column_names = {str(col[1]) for col in chunk_columns if len(col) > 1}
            if "start_token" not in chunk_column_names:
                execute_with_locked_retry(db, "ALTER TABLE document_chunks ADD COLUMN start_token INTEGER DEFAULT 0")
            if "end_token" not in chunk_column_names:
                execute_with_locked_retry(db, "ALTER TABLE document_chunks ADD COLUMN end_token INTEGER DEFAULT 0")
            if "filename" not in chunk_column_names:
                execute_with_locked_retry(db, "ALTER TABLE document_chunks ADD COLUMN filename TEXT")
            if "owner_agent_id" not in chunk_column_names:
                execute_with_locked_retry(db, "ALTER TABLE document_chunks ADD COLUMN owner_agent_id TEXT")
            if "created_at" not in chunk_column_names:
                execute_with_locked_retry(db, "ALTER TABLE document_chunks ADD COLUMN created_at INTEGER DEFAULT 0")
            execute_with_locked_retry(db, create_owner_idx)
            execute_with_locked_retry(db, create_chunks_doc_idx)
            execute_with_locked_retry(db, create_chunks_owner_idx)

    def get_agent_db(self, agent_id: str):
        try:
            self._ensure_agent_schema(agent_id)
            db_path = self._agent_db_path(agent_id)
            return self.connection_pool.get_db(f"agent:{agent_id}", str(db_path))
        except Exception as exc:
            logger.exception(
                "agent database connection failed",
                extra={"agent": agent_id, "operation": "get_agent_db"},
            )
            raise RuntimeError(f"agent '{agent_id}' database connection failed: {exc}") from exc

    def get_shared_db(self):
        try:
            self._ensure_shared_schema()
            db_path = self._shared_db_path()
            return self.connection_pool.get_db("shared", str(db_path))
        except Exception as exc:
            logger.exception(
                "shared database connection failed",
                extra={"agent": "shared", "operation": "get_shared_db"},
            )
            raise RuntimeError(f"shared database connection failed: {exc}") from exc

    def search_agent_memories(self, *, agent_id: str, query: str, user_id: str | None = None) -> list[dict[str, Any]]:
        try:
            db_path = self._agent_db_path(agent_id)
            params: list[object] = [agent_id]
            sql = "SELECT id, agent_id, user_id, content, created_at FROM memories WHERE agent_id = ?"
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)

            with self.connection_pool.checkout(f"agent:{agent_id}", str(db_path)) as db:
                rows = fetch_with_locked_retry(db, sql, tuple(params))

            normalized = [
                {
                    "id": row[0],
                    "agent_id": row[1],
                    "user_id": row[2],
                    "content": row[3] or "",
                    "created_at": row[4],
                }
                for row in rows
            ]
            return rank_rows(normalized, query, text_key="content")
        except Exception as exc:
            logger.exception(
                "agent memory search failed",
                extra={"agent": agent_id, "operation": "search_agent_memories"},
            )
            return []

    def search_shared_documents(self, *, query: str, owner_filter: str | None) -> list[dict[str, Any]]:
        try:
            db_path = self._shared_db_path()
            sql = (
                "SELECT c.id, c.document_id, c.chunk_index, c.content, d.owner_agent_id, d.filename, d.created_at "
                "FROM document_chunks c "
                "JOIN documents d ON d.id = c.document_id"
            )
            params: tuple[object, ...] = ()
            if owner_filter:
                sql += " WHERE (d.owner_agent_id = ? OR d.owner_agent_id IS NULL)"
                params = (owner_filter,)

            with self.connection_pool.checkout("shared", str(db_path)) as db:
                rows = fetch_with_locked_retry(db, sql, params)

            normalized = [
                {
                    "id": row[0],
                    "document_id": row[1],
                    "chunk_index": row[2],
                    "content": row[3] or "",
                    "owner_agent_id": row[4],
                    "filename": row[5],
                    "created_at": row[6],
                }
                for row in rows
            ]
            return rank_rows(normalized, query, text_key="content")
        except Exception as exc:
            logger.exception(
                "shared document search failed",
                extra={"agent": "shared", "operation": "search_shared_documents"},
            )
            return []

    def search_documents(
        self,
        *,
        query: str,
        requesting_agent_id: str,
        allow_cross_agent: bool,
    ) -> list[dict[str, Any]]:
        if not requesting_agent_id.strip():
            msg = "requesting_agent_id must be a non-empty string"
            raise ValueError(msg)

        try:
            sql, params = build_document_search_query(
                requesting_agent_id=requesting_agent_id,
                allow_cross_agent=allow_cross_agent,
            )
            db_path = self._shared_db_path()
            with self.connection_pool.checkout("shared", str(db_path)) as db:
                rows = fetch_with_locked_retry(db, sql, params)

            normalized = [
                {
                    "id": row[0],
                    "document_id": row[1],
                    "chunk_index": row[2],
                    "content": row[3] or "",
                    "owner_agent_id": row[4],
                    "filename": row[5],
                    "created_at": row[6],
                }
                for row in rows
            ]
            return rank_rows(normalized, query, text_key="content")
        except Exception as exc:
            logger.exception(
                "document search failed",
                extra={"agent": requesting_agent_id, "operation": "search_documents"},
            )
            return []

    def hybrid_search(
        self,
        *,
        agent_id: str,
        query: str,
        allow_cross_agent: bool,
        user_id: str | None = None,
        top_k: int = 10,
    ) -> list[HybridSearchHit]:
        return hybrid_search(
            manager=self,
            agent_id=agent_id,
            query=query,
            allow_cross_agent=allow_cross_agent,
            user_id=user_id,
            top_k=top_k,
        )

    async def ingest_document(self, *, file_path: str, agent_id: str, user_id: str, embed_client: Any) -> IngestDocumentResult:
        return await ingest_document(
            file_path=file_path,
            agent_id=agent_id,
            user_id=user_id,
            config=self.config,
            db_manager=self,
            embed_client=embed_client,
        )

    @property
    def primary_store(self) -> SQLiteStore:
        return self._primary_store

    def close(self) -> None:
        try:
            self._primary_store.close()
        finally:
            self.connection_pool.close_all()

    def __enter__(self) -> HybridDatabaseManager:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.close()
        return False


class HybridDatabaseFacade:
    def __init__(self, manager: HybridDatabaseManager) -> None:
        self.manager = manager
        self._store = manager.primary_store

        self.resource_repo = self._store.resource_repo
        self.memory_category_repo = self._store.memory_category_repo
        self.memory_item_repo = self._store.memory_item_repo
        self.category_item_repo = self._store.category_item_repo

        self.resources = self._store.resources
        self.items = self._store.items
        self.categories = self._store.categories
        self.relations = self._store.relations

    def close(self) -> None:
        self.manager.close()

    def hybrid_search(
        self,
        *,
        agent_id: str,
        query: str,
        allow_cross_agent: bool,
        user_id: str | None = None,
        top_k: int = 10,
    ) -> list[HybridSearchHit]:
        return self.manager.hybrid_search(
            agent_id=agent_id,
            query=query,
            allow_cross_agent=allow_cross_agent,
            user_id=user_id,
            top_k=top_k,
        )

    def ensure_hybrid_storage(self, agent_id: str) -> None:
        self.manager.get_agent_db(agent_id)
        self.manager.get_shared_db()

    async def ingest_document(self, *, file_path: str, agent_id: str, user_id: str, embed_client: Any) -> IngestDocumentResult:
        self.ensure_hybrid_storage(agent_id)
        return await self.manager.ingest_document(
            file_path=file_path,
            agent_id=agent_id,
            user_id=user_id,
            embed_client=embed_client,
        )

    @property
    def open_connection_count(self) -> int:
        return self.manager.connection_pool.open_connection_count


def build_hybrid_database(
    *,
    db_config: DatabaseConfig,
    memu_config: MemUConfig,
    user_model: type[BaseModel],
    memory_root: str | Path | None = None,
) -> HybridDatabaseFacade:
    manager = HybridDatabaseManager(
        config=memu_config,
        db_config=db_config,
        user_model=user_model,
        memory_root=memory_root,
    )
    return HybridDatabaseFacade(manager)


__all__ = ["HybridDatabaseFacade", "HybridDatabaseManager", "build_hybrid_database"]
