from __future__ import annotations

from typing import Any


def validate_config(config_dict: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    if not isinstance(config_dict, dict):
        return {
            "valid": False,
            "errors": ["Config must be an object/dictionary."],
        }

    agent_settings = config_dict.get("agentSettings")

    if agent_settings is None:
        return {"valid": True, "errors": errors}

    if not isinstance(agent_settings, list):
        errors.append("'agentSettings' must be a list of agent setting objects.")
        return {"valid": False, "errors": errors}

    seen_names: set[str] = set()

    for index, agent in enumerate(agent_settings):
        prefix = f"agentSettings[{index}]"

        if not isinstance(agent, dict):
            errors.append(f"{prefix} must be an object.")
            continue

        name = agent.get("name")
        if "name" not in agent:
            errors.append(f"{prefix} is missing required field 'name'.")
        elif not isinstance(name, str):
            errors.append(f"{prefix}.name must be a string.")
        elif not name.strip():
            errors.append(f"{prefix}.name must not be empty.")
        else:
            if name in seen_names:
                errors.append(f"Duplicate agent name detected in agentSettings: '{name}'.")
            seen_names.add(name)

        memory_enabled = agent.get("memoryEnabled")
        if "memoryEnabled" not in agent:
            errors.append(f"{prefix} is missing required field 'memoryEnabled'.")
        elif not isinstance(memory_enabled, bool):
            errors.append(f"{prefix}.memoryEnabled must be a boolean.")

    return {"valid": len(errors) == 0, "errors": errors}
