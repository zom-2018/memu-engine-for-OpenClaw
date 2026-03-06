from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memu.app.settings import MemUConfig
from memu.chunking import TextChunk, chunk_text
from memu.database.lazy_db import execute_with_locked_retry
from memu.parsers import parse_file

logger = logging.getLogger(__name__)


@dataclass
class IngestDocumentResult:
    document_id: str
    file_path: str
    filename: str
    chunk_count: int
    elapsed_seconds: float


def _embedding_to_blob(embedding: Sequence[float]) -> bytes:
    return json.dumps([float(v) for v in embedding]).encode("utf-8")


def _extract_embeddings(value: Any) -> list[list[float]]:
    if isinstance(value, tuple):
        if not value:
            return []
        first = value[0]
        if isinstance(first, list):
            return first
        msg = f"Unexpected embed() tuple payload type: {type(first).__name__}"
        raise TypeError(msg)
    if isinstance(value, list):
        return value
    msg = f"Unexpected embed() payload type: {type(value).__name__}"
    raise TypeError(msg)


async def ingest_document(
    *,
    file_path: str,
    agent_id: str,
    user_id: str,
    config: MemUConfig,
    db_manager: Any,
    embed_client: Any,
) -> IngestDocumentResult:
    started = time.perf_counter()
    normalized_path = str(Path(file_path).expanduser().resolve())
    filename = os.path.basename(normalized_path)

    logger.info("Starting document ingestion for %s (agent=%s user=%s)", normalized_path, agent_id, user_id)

    try:
        text = parse_file(normalized_path)
    except FileNotFoundError:
        raise
    except Exception as exc:
        msg = f"Failed to parse document {normalized_path}: {exc}"
        raise RuntimeError(msg) from exc

    now_ts = int(time.time())
    document_id = str(uuid.uuid4())
    chunks: list[TextChunk] = chunk_text(text, config.chunkSize, config.chunkOverlap)
    chunk_count = len(chunks)

    logger.info("Parsed %s and produced %d chunks", normalized_path, chunk_count)
    if chunk_count > 1000:
        logger.warning(
            "Document %s produced %d chunks; consider increasing chunkSize (current=%d)",
            normalized_path,
            chunk_count,
            config.chunkSize,
        )

    shared_db = db_manager.get_shared_db()

    insert_doc_sql = "INSERT INTO documents (id, filename, owner_agent_id, created_at) VALUES (?, ?, ?, ?)"
    execute_with_locked_retry(shared_db, insert_doc_sql, (document_id, filename, agent_id, now_ts))

    if chunks:
        chunk_texts = [c.text for c in chunks]
        embeddings = _extract_embeddings(await embed_client.embed(chunk_texts))
        if len(embeddings) != len(chunks):
            msg = (
                f"Embedding count mismatch for {normalized_path}: "
                f"got {len(embeddings)} embeddings for {len(chunks)} chunks"
            )
            raise RuntimeError(msg)

        insert_chunk_sql = (
            "INSERT INTO document_chunks "
            "(id, document_id, chunk_index, start_token, end_token, content, embedding, filename, owner_agent_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            execute_with_locked_retry(
                shared_db,
                insert_chunk_sql,
                (
                    str(uuid.uuid4()),
                    document_id,
                    chunk.index,
                    chunk.start_token,
                    chunk.end_token,
                    chunk.text,
                    _embedding_to_blob(embedding),
                    filename,
                    agent_id,
                    now_ts,
                ),
            )

    elapsed_seconds = time.perf_counter() - started
    logger.info("Finished document ingestion for %s in %.3fs", normalized_path, elapsed_seconds)
    if filename.lower().endswith(".pdf") and elapsed_seconds > 10:
        logger.warning("PDF ingestion exceeded 10s for %s (%.3fs)", normalized_path, elapsed_seconds)

    return IngestDocumentResult(
        document_id=document_id,
        file_path=normalized_path,
        filename=filename,
        chunk_count=chunk_count,
        elapsed_seconds=elapsed_seconds,
    )


async def ingestDocument(file_path: str, agent_id: str, user_id: str, config: MemUConfig, db_manager: Any, embed_client: Any) -> dict[str, Any]:
    result = await ingest_document(
        file_path=file_path,
        agent_id=agent_id,
        user_id=user_id,
        config=config,
        db_manager=db_manager,
        embed_client=embed_client,
    )
    return {
        "document_id": result.document_id,
        "chunk_count": result.chunk_count,
        "duration": result.elapsed_seconds,
    }
