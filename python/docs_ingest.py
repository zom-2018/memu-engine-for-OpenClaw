import asyncio
import hashlib
import os
import json
import sqlite3
import tempfile
from datetime import datetime
from memu.app.service import MemoryService
from memu.app.settings import (
    CustomPrompt,
    DatabaseConfig,
    LLMConfig,
    MemUConfig,
    MemorizeConfig,
    MetadataStoreConfig,
    PromptBlock,
)
from memu.database.hybrid_factory import HybridDatabaseManager
from memu.database.lazy_db import execute_with_locked_retry, fetch_with_locked_retry
from memu.scope_model import AgentScopeModel
from memu.storage_layout import migrate_legacy_single_db_to_agent_db
from memu.storage_layout import agent_db_dsn


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is not None and str(v).strip():
        return v
    return default


def _log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        return
    try:
        state_dir = os.path.join(data_dir, "state")
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "sync.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _try_acquire_lock(lock_path: str):
    """Best-effort non-blocking lock using O_EXCL (PID-aware)."""

    def _pid_alive(pid: int) -> bool:
        if pid <= 1:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        return fd
    except FileExistsError:
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                pid_str = f.read().strip()
            pid = int(pid_str)
            if not _pid_alive(pid):
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                try:
                    fd = os.open(lock_path, flags)
                    os.write(fd, str(os.getpid()).encode("utf-8"))
                    return fd
                except FileExistsError:
                    return None
        except Exception:
            return None
        return None


def _release_lock(lock_path: str, fd) -> None:
    try:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    finally:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass


def _is_under_prefix(path: str, prefix: str) -> bool:
    try:
        path_abs = os.path.abspath(path)
        prefix_abs = os.path.abspath(prefix)
        # Ensure prefix ends with separator for strict containment check.
        if os.path.isdir(prefix_abs):
            prefix_abs = os.path.join(prefix_abs, "")
        return path_abs == prefix_abs.rstrip(os.sep) or path_abs.startswith(prefix_abs)
    except Exception:
        return False


def _collect_markdown_files(
    *, extra_paths: list[str], changed_path: str | None
) -> list[str]:
    """Collect markdown files to ingest.

    - If changed_path is provided, only ingest that file (or md files under that dir),
      and only if it is within extra_paths.
    - Otherwise, do a full scan of extra_paths.
    """
    files: set[str] = set()

    def _add_file(p: str) -> None:
        if p.endswith(".md") and os.path.isfile(p):
            files.add(os.path.abspath(p))

    def _scan_dir(d: str) -> None:
        for root, _, filenames in os.walk(d):
            for f in filenames:
                if f.endswith(".md"):
                    files.add(os.path.abspath(os.path.join(root, f)))

    if changed_path:
        cp = os.path.abspath(changed_path)
        # Only ingest changes that are within configured extra paths.
        allowed = any(_is_under_prefix(cp, p) for p in extra_paths)
        if not allowed:
            return []

        if os.path.isfile(cp):
            _add_file(cp)
        elif os.path.isdir(cp):
            _scan_dir(cp)
        return sorted(files)

    for path_item in extra_paths:
        if not os.path.exists(path_item):
            continue
        if os.path.isfile(path_item):
            _add_file(path_item)
        elif os.path.isdir(path_item):
            _scan_dir(path_item)

    return sorted(files)


LANGUAGE_PROMPTS = {
    "zh": """
## Language Override (CRITICAL - MUST FOLLOW)
- ALL output MUST be in Chinese (中文), regardless of example language.
- Use \"用户\" instead of \"the user\" or \"User\".
- The examples in this prompt are in English for reference only.
- You MUST write all memory content in Chinese.
""",
    "en": """
## Language Override
- ALL output MUST be in English.
- Use \"the user\" to refer to the user.
""",
    "ja": """
## Language Override (重要)
- ALL output MUST be in Japanese (日本語).
- Use \"ユーザー\" instead of \"the user\".
""",
}


def _build_language_aware_memorize_config(lang: str | None) -> MemorizeConfig:
    memory_types = ["profile", "event", "knowledge", "behavior", "skill", "tool"]
    base_config = {
        "memory_types": memory_types,
        "enable_item_references": True,
        "enable_item_reinforcement": True,
    }
    if not lang or lang not in LANGUAGE_PROMPTS:
        return MemorizeConfig(**base_config)

    lang_block = PromptBlock(ordinal=35, prompt=LANGUAGE_PROMPTS[lang])
    type_prompts: dict[str, str | CustomPrompt] = {}
    for mt in memory_types:
        type_prompts[mt] = CustomPrompt(root={"language": lang_block})

    return MemorizeConfig(
        **base_config,
        memory_type_prompts=type_prompts,
    )


def get_db_dsn() -> str:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        base = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base, "data")

    os.makedirs(data_dir, exist_ok=True)
    return f"sqlite:///{os.path.join(data_dir, 'memu.db')}"


def _is_same_mtime(stored_mtime: object, file_mtime: float, *, tolerance: float = 1e-6) -> bool:
    try:
        if stored_mtime is None:
            return False
        return abs(float(stored_mtime if isinstance(stored_mtime, (int, float, str)) else 0.0) - float(file_mtime)) <= tolerance
    except (TypeError, ValueError):
        return False


def _calculate_content_hash(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _get_shared_document_rows(manager: HybridDatabaseManager, *, filename: str, owner_agent_id: str) -> list[tuple[object, ...]]:
    db = manager.get_shared_db()
    rows = fetch_with_locked_retry(
        db,
        (
            "SELECT id, content_hash, mtime "
            "FROM documents "
            "WHERE filename = ? AND owner_agent_id = ? "
            "ORDER BY created_at DESC"
        ),
        (filename, owner_agent_id),
    )
    return rows


def _shared_document_exists(
    manager: HybridDatabaseManager,
    *,
    filename: str,
    owner_agent_id: str,
    content_hash: str,
    mtime: float,
) -> tuple[bool, list[str], list[str]]:
    rows = _get_shared_document_rows(manager, filename=filename, owner_agent_id=owner_agent_id)
    if not rows:
        return False, [], []

    doc_ids = [str(row[0]) for row in rows if row and row[0]]
    all_legacy = all((not row[1]) or (row[2] is None) for row in rows)
    if all_legacy:
        return True, doc_ids, []

    for row in rows:
        if len(row) < 3:
            continue
        row_hash = row[1]
        row_mtime = row[2]
        if row_hash == content_hash and _is_same_mtime(row_mtime, mtime):
            return True, [], []

    return False, [], doc_ids


def _backfill_document_metadata(
    manager: HybridDatabaseManager,
    *,
    document_ids: list[str],
    content_hash: str,
    mtime: float,
) -> None:
    if not document_ids:
        return
    db = manager.get_shared_db()
    for document_id in document_ids:
        execute_with_locked_retry(
            db,
            "UPDATE documents SET content_hash = ?, mtime = ? WHERE id = ?",
            (content_hash, mtime, document_id),
        )


def _delete_documents_and_chunks(manager: HybridDatabaseManager, *, document_ids: list[str]) -> None:
    if not document_ids:
        return
    db = manager.get_shared_db()
    for document_id in document_ids:
        execute_with_locked_retry(
            db,
            "DELETE FROM document_chunks WHERE document_id = ?",
            (document_id,),
        )
        execute_with_locked_retry(
            db,
            "DELETE FROM documents WHERE id = ?",
            (document_id,),
        )


def _persist_document_metadata(
    manager: HybridDatabaseManager,
    *,
    document_id: str,
    content_hash: str,
    mtime: float,
) -> None:
    db = manager.get_shared_db()
    execute_with_locked_retry(
        db,
        "UPDATE documents SET content_hash = ?, mtime = ? WHERE id = ?",
        (content_hash, mtime, document_id),
    )


def get_extra_paths() -> list[str]:
    raw = os.getenv("MEMU_EXTRA_PATHS", "[]")
    try:
        paths = json.loads(raw)
        if isinstance(paths, list):
            return [p for p in paths if isinstance(p, str)]
    except json.JSONDecodeError:
        pass
    return []


def _full_scan_marker_path() -> str | None:
    data_dir = os.getenv("MEMU_DATA_DIR")
    if not data_dir:
        return None
    return os.path.join(data_dir, "state", "docs_full_scan.marker")


async def main():
    lock_name = os.path.join(tempfile.gettempdir(), "memu_sync.lock_docs_ingest")
    lock_fd = _try_acquire_lock(lock_name)
    if lock_fd is None:
        _log("docs_ingest already running; skip")
        return

    try:
        migrate_legacy_single_db_to_agent_db(default_agent="main")
        user_id = _env("MEMU_USER_ID", "default") or "default"
        owner_agent = _env("MEMU_DOC_OWNER_AGENT", "main") or "main"

        chat_kwargs = {}
        if p := _env("MEMU_CHAT_PROVIDER"):
            chat_kwargs["provider"] = p
        if u := _env("MEMU_CHAT_BASE_URL"):
            chat_kwargs["base_url"] = u
        if k := _env("MEMU_CHAT_API_KEY"):
            chat_kwargs["api_key"] = k
        if m := _env("MEMU_CHAT_MODEL"):
            chat_kwargs["chat_model"] = m
        chat_config = LLMConfig(**chat_kwargs)

        embed_kwargs = {}
        if p := _env("MEMU_EMBED_PROVIDER"):
            embed_kwargs["provider"] = p
        if u := _env("MEMU_EMBED_BASE_URL"):
            embed_kwargs["base_url"] = u
        if k := _env("MEMU_EMBED_API_KEY"):
            embed_kwargs["api_key"] = k
        if m := _env("MEMU_EMBED_MODEL"):
            embed_kwargs["embed_model"] = m
        embed_config = LLMConfig(**embed_kwargs)

        db_config = DatabaseConfig(
            metadata_store=MetadataStoreConfig(
                provider="sqlite",
                dsn=agent_db_dsn(owner_agent),
            )
        )

        output_lang = _env("MEMU_OUTPUT_LANG", "")
        memorize_config = _build_language_aware_memorize_config(output_lang)

        service = MemoryService(
            llm_profiles={"default": chat_config, "embedding": embed_config},
            database_config=db_config,
            memorize_config=memorize_config,
        )
        embed_client = service._get_llm_client("embedding")
        manager = HybridDatabaseManager(
            config=MemUConfig(),
            db_config=db_config,
            user_model=AgentScopeModel,
        )

        extra_paths = get_extra_paths()
        changed_path = _env("MEMU_CHANGED_PATH", None)
        files_to_ingest = _collect_markdown_files(
            extra_paths=extra_paths, changed_path=changed_path
        )

        if not files_to_ingest:
            if changed_path:
                _log(
                    f"docs_ingest: no markdown files to ingest for change: {changed_path}"
                )
            else:
                _log("docs_ingest: no markdown files found in extraPaths")
            return

        mode = "incremental" if changed_path else "full-scan"
        _log(f"docs_ingest start. mode={mode} files={len(files_to_ingest)}")

        timeout_s = int(_env("MEMU_MEMORIZE_TIMEOUT_SECONDS", "600") or "600")

        ok = 0
        fail = 0
        skipped = 0

        for file_path in files_to_ingest:
            try:
                content_hash = _calculate_content_hash(file_path)
                mtime = float(os.path.getmtime(file_path))
                exists, legacy_doc_ids, stale_doc_ids = _shared_document_exists(
                    manager,
                    filename=os.path.basename(file_path),
                    owner_agent_id=owner_agent,
                    content_hash=content_hash,
                    mtime=mtime,
                )

                if legacy_doc_ids:
                    _backfill_document_metadata(
                        manager,
                        document_ids=legacy_doc_ids,
                        content_hash=content_hash,
                        mtime=mtime,
                    )
                    skipped += 1
                    continue

                if exists:
                    skipped += 1
                    continue

                if stale_doc_ids:
                    _delete_documents_and_chunks(manager, document_ids=stale_doc_ids)

                _log(f"docs_ingest ingest: {file_path}")
                result = await asyncio.wait_for(
                    manager.ingest_document(
                        file_path=file_path,
                        agent_id=owner_agent,
                        user_id=user_id,
                        embed_client=embed_client,
                    ),
                    timeout=timeout_s,
                )
                _persist_document_metadata(
                    manager,
                    document_id=result.document_id,
                    content_hash=content_hash,
                    mtime=mtime,
                )
                ok += 1
            except Exception as e:
                _log(f"docs_ingest failed: {file_path}: {type(e).__name__}: {e}")
                fail += 1

        manager.close()

        _log(
            f"docs_ingest complete. ok={ok} skipped={skipped} fail={fail} files={len(files_to_ingest)}"
        )

        # Persist marker so watcher can skip initial full-scans on restart.
        if mode == "full-scan":
            marker = _full_scan_marker_path()
            if marker:
                try:
                    with open(marker, "w", encoding="utf-8") as f:
                        f.write(datetime.now().isoformat())
                except Exception:
                    pass
    finally:
        _release_lock(lock_name, lock_fd)


if __name__ == "__main__":
    asyncio.run(main())
