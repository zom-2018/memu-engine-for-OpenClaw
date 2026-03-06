from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from memu.app.settings import DatabaseConfig, MemUConfig
from memu.database.inmemory import build_inmemory_database
from memu.database.interfaces import Database

if TYPE_CHECKING:
    pass


def build_database(
    *,
    config: DatabaseConfig,
    memu_config: MemUConfig | None = None,
    user_model: type[BaseModel],
) -> Database:
    """
    Initialize a database backend for the configured provider.

    Supported providers:
        - "inmemory": In-memory storage (default, no persistence)
        - "postgres": PostgreSQL with optional pgvector support
        - "sqlite": SQLite file-based storage (lightweight, portable)

    """
    provider = config.metadata_store.provider

    if provider == "sqlite":
        from memu.database.hybrid_factory import build_hybrid_database

        cfg = memu_config if memu_config is not None else MemUConfig()
        return build_hybrid_database(db_config=config, memu_config=cfg, user_model=user_model)

    if provider == "inmemory":
        return build_inmemory_database(config=config, user_model=user_model)
    elif provider == "postgres":
        # Lazy import to avoid requiring pgvector when not using postgres
        from memu.database.postgres import build_postgres_database

        return build_postgres_database(config=config, user_model=user_model)
    else:
        msg = f"Unsupported metadata_store provider: {provider}"
        raise ValueError(msg)
