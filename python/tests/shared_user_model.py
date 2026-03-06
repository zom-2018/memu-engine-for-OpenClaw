from __future__ import annotations

from pydantic import BaseModel


class SharedUserModel(BaseModel):
    user_id: str | None = None
