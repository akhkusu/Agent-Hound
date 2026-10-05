"""Merge observed user/shared/local Claude settings for one workspace.

Lists accumulate; higher-scope scalar/dictionary keys override lower scopes.
Read retains each source's rules so settings-relative anchors are not lost.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agenthound.discovery.config_parser import claude_settings_paths, parse_config_data
from agenthound.discovery.claude_mcp import collect_mcp
from agenthound.models.nodes import Agent, Capability


def _merge(lower: Any, higher: Any) -> Any:
    if isinstance(lower, dict) and isinstance(higher, dict):
        merged = dict(lower)
        for key, value in higher.items():
            merged[key] = _merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(lower, list) and isinstance(higher, list):
        return lower + [item for item in higher if item not in lower]
    return higher


def collect_claude_settings(paths: list[Path], workspace: Path) -> tuple[Agent, list[Capability]]:
    """Only merge applicable discovered paths, in precedence order."""
    workspace = workspace.resolve()
    supplied = {p.resolve() for p in paths}
    merged: dict[str, Any] = {}
    sources = []
    seen = set()
    for index, path in enumerate(claude_settings_paths(workspace)):
        if path.resolve() not in supplied or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot parse config {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"Config must be an object: {path}")
        merged = _merge(merged, raw)
        policy = raw.get("permissions", {})
        if not isinstance(policy, dict):
            policy = {"deny": ["Read(unsupported:policy)"]}
        sources.append({"path": str(path), "anchor": str(path.parent if index == 0 else workspace),
                        "scope": ("user", "shared", "local")[index], "permissions": policy})
    # This is the session identity, not a claim that a settings file exists.
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(workspace / ".claude"))
    _, caps = parse_config_data(Path(agent.config_path), merged, agent)
    mcps, canonical_names = collect_mcp(paths, workspace, agent, merged)
    caps = [cap for cap in caps if not (cap.cap_kind == "MCPServer" and cap.name in canonical_names)]
    caps.extend(mcps)
    updated = []
    for cap in caps:
        if cap.cap_kind == "BuiltInTool" and cap.name == "Read":
            cap = cap.model_copy(update={
                "read_policy_sources": sources or [{"path": "no permission settings observed", "anchor": str(workspace), "permissions": {}}], "read_workspace": str(workspace),
                "file_evidence": cap.file_evidence + "; observed settings:" + ",".join(s["path"] for s in sources),
            })
        elif cap.cap_kind == "BuiltInTool" and cap.name == "Bash":
            cap = cap.model_copy(update={
                "shell_evidence": cap.shell_evidence + "; observed settings:" + ",".join(s["path"] for s in sources),
            })
        updated.append(cap)
    return agent, updated
