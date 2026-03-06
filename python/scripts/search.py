import argparse
import asyncio
import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from memu.app.service import MemoryService
from memu.app.settings import (
    DatabaseConfig,
    LLMConfig,
    MemUConfig,
    MetadataStoreConfig,
    RetrieveCategoryConfig,
    RetrieveConfig,
    RetrieveItemConfig,
    RetrieveResourceConfig,
    UserConfig,
)
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.scope_model import AgentScopeModel
from memu.storage_layout import (
    agent_db_dsn,
    migrate_legacy_single_db_to_agent_db,
    parse_agent_settings_from_env,
    resolve_agent_policy,
)


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is not None and str(v).strip():
        return v
    return default


def _build_llm_configs() -> tuple[LLMConfig, LLMConfig]:
    chat_kwargs: dict[str, Any] = {}
    if p := _env("MEMU_CHAT_PROVIDER"):
        chat_kwargs["provider"] = p
    if u := _env("MEMU_CHAT_BASE_URL"):
        chat_kwargs["base_url"] = u
    if k := _env("MEMU_CHAT_API_KEY"):
        chat_kwargs["api_key"] = k
    if m := _env("MEMU_CHAT_MODEL"):
        chat_kwargs["chat_model"] = m
    chat_config = LLMConfig(**chat_kwargs)

    embed_kwargs: dict[str, Any] = {}
    if p := _env("MEMU_EMBED_PROVIDER"):
        embed_kwargs["provider"] = p
    if u := _env("MEMU_EMBED_BASE_URL"):
        embed_kwargs["base_url"] = u
    if k := _env("MEMU_EMBED_API_KEY"):
        embed_kwargs["api_key"] = k
    if m := _env("MEMU_EMBED_MODEL"):
        embed_kwargs["embed_model"] = m
    embed_config = LLMConfig(**embed_kwargs)
    return chat_config, embed_config


def _build_service(*, dsn: str, chat_config: LLMConfig, embed_config: LLMConfig, max_results: int, mode: str) -> MemoryService:
    retr_config = RetrieveConfig(
        route_intention=mode == "full",
        sufficiency_check=mode == "full",
        item=RetrieveItemConfig(enabled=True, top_k=max_results),
        category=RetrieveCategoryConfig(enabled=True, top_k=min(5, max_results)),
        resource=RetrieveResourceConfig(enabled=True, top_k=min(5, max_results)),
    )
    db_config = DatabaseConfig(metadata_store=MetadataStoreConfig(provider="sqlite", dsn=dsn))
    return MemoryService(
        llm_profiles={"default": chat_config, "embedding": embed_config},
        database_config=db_config,
        retrieve_config=retr_config,
        user_config=UserConfig(model=AgentScopeModel),
    )


@dataclass
class Candidate:
    uid: str
    store: str
    source: str
    path: str
    snippet: str
    raw_score: float
    agent_name: str


def _normalize_snippet(text: str) -> str:
    if not text:
        return ""
    s = text.strip().lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]", "", s)
    return s


def _shorten_path(abs_path: str, workspace_dir: str, extra_paths: list[str]) -> str:
    if not abs_path:
        return abs_path
    for i, ep in enumerate(extra_paths):
        if abs_path.startswith(ep + "/"):
            rel = abs_path[len(ep) + 1 :]
            return f"ext{i}:{rel}"
        if abs_path == ep:
            return f"ext{i}:"
    if workspace_dir and abs_path.startswith(workspace_dir + "/"):
        rel = abs_path[len(workspace_dir) + 1 :]
        return f"ws:{rel}"
    if workspace_dir and abs_path == workspace_dir:
        return "ws:"
    return abs_path


def _format_source(url: str | None, workspace_dir: str, extra_paths: list[str]) -> str | None:
    if not url:
        return None
    short = _shorten_path(url, workspace_dir, extra_paths)
    if short != url:
        return f"memu://{short}"
    if url.startswith("/"):
        return f"memu://{short}"
    return f"memu://{url}"


def _resolve_search_targets(*, requesting_agent: str, requested_stores: list[str]) -> list[str]:
    settings = parse_agent_settings_from_env()
    policy = resolve_agent_policy(requesting_agent, settings)
    if not policy["searchEnabled"]:
        return []

    raw_targets = requested_stores or list(policy["searchableStores"])
    targets: list[str] = []
    seen: set[str] = set()
    for target in raw_targets:
        resolved = requesting_agent if target == "self" else target
        if not isinstance(resolved, str) or not resolved.strip():
            continue
        resolved = resolved.strip()
        if resolved in seen:
            continue
        if resolved != "shared":
            target_policy = resolve_agent_policy(resolved, settings)
            if not target_policy["memoryEnabled"]:
                continue
        seen.add(resolved)
        targets.append(resolved)
    return targets


async def _search_agent_store(
    *,
    agent_name: str,
    query_text: str,
    user_id: str,
    mode: str,
    max_results: int,
    queries: list[dict[str, Any]],
    chat_config: LLMConfig,
    embed_config: LLMConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    service = _build_service(
        dsn=agent_db_dsn(agent_name),
        chat_config=chat_config,
        embed_config=embed_config,
        max_results=max_results,
        mode=mode,
    )
    result = await service.retrieve(queries=queries, where={"user_id": user_id})
    categories = result.get("categories", [])
    items = result.get("items", [])
    resources = result.get("resources", [])
    resource_url_map = {str(r.get("id")): str(r.get("url")) for r in resources if isinstance(r, dict) and r.get("id")}
    return categories, items, resource_url_map


def _rrf_fuse(candidates: list[Candidate], k: int = 60) -> list[tuple[Candidate, float]]:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        grouped[c.store].append(c)

    fused: dict[str, float] = defaultdict(float)
    by_uid: dict[str, Candidate] = {}
    for store, rows in grouped.items():
        ranked = sorted(rows, key=lambda r: r.raw_score, reverse=True)
        for rank, row in enumerate(ranked, start=1):
            fused[row.uid] += 1.0 / (k + rank)
            by_uid[row.uid] = row

    out = [(by_uid[uid], score) for uid, score in fused.items()]
    out.sort(key=lambda x: x[1], reverse=True)
    return out


async def search(
    *,
    query_text: str,
    requesting_agent: str,
    search_stores: list[str],
    max_results: int,
    min_score: float,
    user_id: str,
    mode: str,
    category_quota: int | None,
    item_quota: int | None,
    queries: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    migrate_legacy_single_db_to_agent_db(default_agent="main")

    t0 = time.perf_counter()
    effective_queries = queries or [{"role": "user", "content": query_text}]
    targets = _resolve_search_targets(requesting_agent=requesting_agent, requested_stores=search_stores)
    if not targets:
        return {"results": [], "provider": _env("MEMU_CHAT_PROVIDER", "openai") or "openai", "model": _env("MEMU_CHAT_MODEL", "unknown") or "unknown", "fallback": None, "citations": "off"}

    chat_config, embed_config = _build_llm_configs()
    workspace_dir = _env("MEMU_WORKSPACE_DIR", os.path.expanduser("~/.openclaw/workspace")) or ""
    try:
        extra_paths = json.loads(_env("MEMU_EXTRA_PATHS", "[]") or "[]")
    except Exception:
        extra_paths = []

    candidates: list[Candidate] = []

    for target in targets:
        if target == "shared":
            continue
        cats, items, resource_map = await _search_agent_store(
            agent_name=target,
            query_text=query_text,
            user_id=user_id,
            mode=mode,
            max_results=max_results,
            queries=effective_queries,
            chat_config=chat_config,
            embed_config=embed_config,
        )
        for c in cats:
            score = float(c.get("score", 0.0) or 0.0)
            if score < min_score:
                continue
            snippet = str(c.get("summary", "") or "")
            cat_id = str(c.get("id") or c.get("name") or "unknown")
            candidates.append(
                Candidate(
                    uid=f"{target}:category:{cat_id}",
                    store=target,
                    source="category",
                    path=f"memu://agent/{target}/category/{cat_id}",
                    snippet=snippet,
                    raw_score=score,
                    agent_name=target,
                )
            )
        for i in items:
            score = float(i.get("score", 0.0) or 0.0)
            if score < min_score:
                continue
            item_id = str(i.get("id") or "unknown")
            url = resource_map.get(str(i.get("resource_id") or ""))
            resolved_path = _format_source(url, workspace_dir, extra_paths) or f"memu://agent/{target}/item/{item_id}"
            candidates.append(
                Candidate(
                    uid=f"{target}:item:{item_id}",
                    store=target,
                    source="item",
                    path=resolved_path,
                    snippet=str(i.get("summary", "") or ""),
                    raw_score=score,
                    agent_name=target,
                )
            )

    if "shared" in targets:
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=DatabaseConfig(metadata_store=MetadataStoreConfig(provider="sqlite", dsn=agent_db_dsn(requesting_agent))),
            user_model=AgentScopeModel,
        )
        try:
            docs = manager.search_shared_documents(query=query_text, owner_filter=None)
        finally:
            manager.close()
        for d in docs:
            score = float(d.get("score", 0.0) or 0.0)
            if score < min_score:
                continue
            doc_id = str(d.get("document_id") or "unknown")
            chunk_id = str(d.get("chunk_index") or "0")
            candidates.append(
                Candidate(
                    uid=f"shared:doc:{d.get('id')}",
                    store="shared",
                    source="document",
                    path=f"memu://shared/document/{doc_id}#chunk-{chunk_id}",
                    snippet=str(d.get("content", "") or ""),
                    raw_score=score,
                    agent_name="shared",
                )
            )

    fused = _rrf_fuse(candidates)

    if category_quota is None and item_quota is None:
        if max_results >= 10:
            category_quota = 3 if max_results <= 10 else 4
        elif max_results >= 6:
            category_quota = 2
        else:
            category_quota = 1
        category_quota = min(category_quota, max_results)
        item_quota = max(0, max_results - category_quota)
    else:
        category_quota = 0 if category_quota is None else max(0, category_quota)
        item_quota = 0 if item_quota is None else max(0, item_quota)

    categories: list[tuple[Candidate, float]] = []
    non_categories: list[tuple[Candidate, float]] = []
    for row in fused:
        if row[0].source == "category":
            categories.append(row)
        else:
            non_categories.append(row)

    selected = [*categories[:category_quota], *non_categories[:item_quota]]
    if not selected:
        selected = fused[:max_results]

    seen_norm_snippets: set[str] = set()
    results: list[dict[str, Any]] = []
    snippet_budget = 4000
    for row, score in selected:
        if snippet_budget <= 0:
            break
        snippet = row.snippet[:700]
        norm = _normalize_snippet(snippet)
        if not norm or norm in seen_norm_snippets:
            continue
        seen_norm_snippets.add(norm)
        if len(snippet) > snippet_budget:
            snippet = snippet[:snippet_budget]
        snippet_budget -= len(snippet)
        results.append(
            {
                "path": row.path,
                "startLine": 1,
                "endLine": 1,
                "score": round(score, 6),
                "snippet": snippet,
                "source": "memory" if row.source in ("category", "item") else "document",
                "agentName": row.agent_name,
            }
        )
        if len(results) >= max_results:
            break

    payload: dict[str, Any] = {
        "results": results,
        "provider": _env("MEMU_CHAT_PROVIDER", "openai") or "openai",
        "model": _env("MEMU_CHAT_MODEL", "unknown") or "unknown",
        "fallback": None,
        "citations": "off",
    }
    if (_env("MEMU_DEBUG_TIMING", "false") or "").lower() == "true":
        payload["_timing"] = {
            "total_ms": round((time.perf_counter() - t0) * 1000, 2),
            "targets": targets,
            "candidate_count": len(candidates),
            "fused_count": len(fused),
        }
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Search query")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--category-quota", type=int, default=None)
    parser.add_argument("--item-quota", type=int, default=None)
    parser.add_argument("--mode", type=str, default="fast", choices=["fast", "full"])
    parser.add_argument("--queries-json", type=str, default="")
    parser.add_argument("--requesting-agent", type=str, default="main")
    parser.add_argument("--search-stores", type=str, default="self")
    args = parser.parse_args()

    try:
        query_messages: list[dict[str, Any]] | None = None
        if args.queries_json:
            parsed = json.loads(args.queries_json)
            if isinstance(parsed, list):
                query_messages = parsed

        stores = [s.strip() for s in (args.search_stores or "").split(",") if s.strip()]
        user_id = _env("MEMU_USER_ID", "default") or "default"
        res = asyncio.run(
            search(
                query_text=args.query,
                requesting_agent=args.requesting_agent,
                search_stores=stores,
                max_results=args.max_results,
                min_score=args.min_score,
                user_id=user_id,
                mode=args.mode,
                category_quota=args.category_quota,
                item_quota=args.item_quota,
                queries=query_messages,
            )
        )
        print(json.dumps(res, ensure_ascii=False))
    except Exception as e:
        print(
            json.dumps(
                {
                    "results": [],
                    "provider": _env("MEMU_CHAT_PROVIDER", "openai") or "openai",
                    "model": _env("MEMU_CHAT_MODEL", "unknown") or "unknown",
                    "fallback": None,
                    "citations": "off",
                    "error": str(e),
                },
                ensure_ascii=False,
            )
        )
