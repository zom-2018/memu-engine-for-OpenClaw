from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Index, MetaData
from sqlmodel import Field, SQLModel


def _agent_metadata() -> MetaData:
    return MetaData()


def _shared_metadata() -> MetaData:
    return MetaData()


AGENT_METADATA = _agent_metadata()
SHARED_METADATA = _shared_metadata()


class AgentMemory(SQLModel, table=True):
    __tablename__ = "memories"
    metadata = AGENT_METADATA
    __table_args__ = (Index("idx_agent_user", "agent_id", "user_id"),)

    id: str = Field(primary_key=True)
    agent_id: str = Field(index=True)
    user_id: str = Field(index=True)
    content: str | None = None
    embedding: bytes | None = None
    created_at: int = Field(default=0)


class SharedDocument(SQLModel, table=True):
    __tablename__ = "documents"
    metadata = SHARED_METADATA

    id: str = Field(primary_key=True)
    filename: str
    owner_agent_id: str = Field(index=True)
    created_at: int = Field(default=0)


class SharedDocumentChunk(SQLModel, table=True):
    __tablename__ = "document_chunks"
    metadata = SHARED_METADATA

    id: str = Field(primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    chunk_index: int = Field(default=0)
    start_token: int = Field(default=0)
    end_token: int = Field(default=0)
    content: str | None = None
    embedding: bytes | None = None
    filename: str | None = None
    owner_agent_id: str | None = Field(default=None, index=True)
    created_at: int = Field(default=0)


@dataclass
class HybridSchemaSet:
    agent_metadata: MetaData
    shared_metadata: MetaData
    agent_models: tuple[type[Any], ...]
    shared_models: tuple[type[Any], ...]


def get_hybrid_schema_set() -> HybridSchemaSet:
    return HybridSchemaSet(
        agent_metadata=AGENT_METADATA,
        shared_metadata=SHARED_METADATA,
        agent_models=(AgentMemory,),
        shared_models=(SharedDocument, SharedDocumentChunk),
    )


__all__ = [
    "AgentMemory",
    "HybridSchemaSet",
    "SharedDocument",
    "SharedDocumentChunk",
    "get_hybrid_schema_set",
]
