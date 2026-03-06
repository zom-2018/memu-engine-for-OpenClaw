from __future__ import annotations


def build_document_search_query(*, requesting_agent_id: str, allow_cross_agent: bool) -> tuple[str, tuple[object, ...]]:
    """Build SQL query for shared document access control.

    Rules:
    - allow_cross_agent=True: return all shared documents.
    - allow_cross_agent=False: return only requesting agent documents and legacy public
      rows where owner_agent_id IS NULL.
    """
    if not requesting_agent_id.strip():
        msg = "requesting_agent_id must be a non-empty string"
        raise ValueError(msg)

    sql = (
        "SELECT c.id, c.document_id, c.chunk_index, c.content, d.owner_agent_id, d.filename, d.created_at "
        "FROM document_chunks c "
        "JOIN documents d ON d.id = c.document_id"
    )
    params: tuple[object, ...] = ()
    if not allow_cross_agent:
        sql += " WHERE (d.owner_agent_id = ? OR d.owner_agent_id IS NULL)"
        params = (requesting_agent_id,)
    return sql, params


__all__ = ["build_document_search_query"]
