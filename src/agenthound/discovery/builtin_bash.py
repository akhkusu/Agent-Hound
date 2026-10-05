"""Conservative static model of Claude Code's built-in Bash tool."""

from __future__ import annotations

import fnmatch
from typing import Any

from agenthound.models.nodes import Agent, Capability


def bash_capability(agent: Agent, raw: dict[str, Any]) -> Capability | None:
    permissions = raw.get("permissions", {})
    if not isinstance(permissions, dict):
        return None
    allow = permissions.get("allow", [])
    if not isinstance(allow, list) or "Bash" not in allow:
        return None
    for kind in ("deny", "ask"):
        rules = permissions.get(kind, [])
        if not isinstance(rules, list) or any(
            not isinstance(rule, str) or (
                "(" not in rule and fnmatch.fnmatchcase("Bash", rule)
            ) or rule == "Bash(*)" for rule in rules
        ):
            return None
    return Capability(
        name="Bash",
        cap_kind="BuiltInTool",
        agent_scope=agent.objectid,
        shell_exec="suspected",
        shell_evidence=(
            "builtin:claude-code:Bash; explicit whole-tool allow observed; "
            "command-specific deny/ask rules, managed policy, runtime approval, "
            "session identity and OS permissions unverified"
        ),
    )
