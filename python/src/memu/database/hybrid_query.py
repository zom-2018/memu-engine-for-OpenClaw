from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class HybridSearchHit:
    source: str
    id: str
    content: str
    score: float
    metadata: dict[str, Any]


def _score_text(content: str, query: str) -> float:
    c = (content or "").lower()
    q = (query or "").strip().lower()
    if not c or not q:
        return 0.0
    if q in c:
        return 1.0 + (len(q) / max(1, len(c)))
    tokens = [t for t in q.split() if t]
    if not tokens:
        return 0.0
    overlap = sum(1 for token in tokens if token in c)
    return overlap / len(tokens)


def merge_results(
    memory_results: list[dict[str, Any]],
    doc_results: list[dict[str, Any]],
) -> list[HybridSearchHit]:
    merged: list[HybridSearchHit] = []

    for row in memory_results:
        merged.append(
            HybridSearchHit(
                source="memory",
                id=str(row.get("id", "")),
                content=str(row.get("content") or ""),
                score=float(row.get("score", 0.0)),
                metadata={
                    "agent_id": row.get("agent_id"),
                    "user_id": row.get("user_id"),
                    "created_at": row.get("created_at"),
                },
            )
        )

    for row in doc_results:
        merged.append(
            HybridSearchHit(
                source="document",
                id=str(row.get("id", "")),
                content=str(row.get("content") or ""),
                score=float(row.get("score", 0.0)),
                metadata={
                    "document_id": row.get("document_id"),
                    "owner_agent_id": row.get("owner_agent_id"),
                    "chunk_index": row.get("chunk_index"),
                    "filename": row.get("filename"),
                    "created_at": row.get("created_at"),
                },
            )
        )

    merged.sort(key=lambda hit: hit.score, reverse=True)
    return merged


def rank_rows(rows: list[dict[str, Any]], query: str, *, text_key: str) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for row in rows:
        content = str(row.get(text_key) or "")
        score = _score_text(content, query)
        ranked.append({**row, "score": score})
    ranked.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
    return ranked


def hybrid_search(
    agent_id: str,
    query: str,
    allow_cross_agent: bool,
    *,
    manager: Any,
    user_id: str | None = None,
    top_k: int = 10,
) -> list[HybridSearchHit]:
    memory_rows = manager.search_agent_memories(agent_id=agent_id, query=query, user_id=user_id)
    document_rows = manager.search_documents(
        query=query,
        requesting_agent_id=agent_id,
        allow_cross_agent=allow_cross_agent,
    )
    merged = merge_results(memory_rows, document_rows)
    if top_k <= 0:
        return merged
    return merged[:top_k]


__all__ = ["HybridSearchHit", "hybrid_search", "merge_results", "rank_rows"]
