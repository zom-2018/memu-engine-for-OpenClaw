from __future__ import annotations

from memu.config_validator import validate_config


def test_valid_config() -> None:
    config = {
        "agentSettings": [
            {"name": "main", "memoryEnabled": True},
            {"name": "research", "memoryEnabled": False},
        ]
    }

    result = validate_config(config)

    assert result["valid"] is True
    assert result["errors"] == []


def test_invalid_config() -> None:
    config = {
        "agentSettings": [
            "not-an-object",
            {"memoryEnabled": True},
            {"name": 123, "memoryEnabled": "yes"},
            {"name": "   ", "memoryEnabled": True},
            {"name": "worker"},
        ]
    }

    result = validate_config(config)

    assert result["valid"] is False
    assert "agentSettings[0] must be an object." in result["errors"]
    assert "agentSettings[1] is missing required field 'name'." in result["errors"]
    assert "agentSettings[2].name must be a string." in result["errors"]
    assert "agentSettings[2].memoryEnabled must be a boolean." in result["errors"]
    assert "agentSettings[3].name must not be empty." in result["errors"]
    assert "agentSettings[4] is missing required field 'memoryEnabled'." in result["errors"]


def test_duplicate_agents() -> None:
    config = {
        "agentSettings": [
            {"name": "main", "memoryEnabled": True},
            {"name": "main", "memoryEnabled": False},
        ]
    }

    result = validate_config(config)

    assert result["valid"] is False
    assert "Duplicate agent name detected in agentSettings: 'main'." in result["errors"]


def test_agent_settings_must_be_list() -> None:
    config = {"agentSettings": {"name": "main", "memoryEnabled": True}}

    result = validate_config(config)

    assert result["valid"] is False
    assert result["errors"] == ["'agentSettings' must be a list of agent setting objects."]


def test_missing_agent_settings_is_valid() -> None:
    result = validate_config({})

    assert result["valid"] is True
    assert result["errors"] == []
