"""Scope model for multi-agent memory support."""

from pydantic import BaseModel, Field


class AgentScopeModel(BaseModel):
    user_id: str = Field(
        default="default",
        min_length=1,
        max_length=100,
    )
    agent_id: str = Field(
        default="main",
        min_length=1,
        max_length=100,
    )
    agentName: str = Field(
        default="main",
        min_length=1,
        max_length=50,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
