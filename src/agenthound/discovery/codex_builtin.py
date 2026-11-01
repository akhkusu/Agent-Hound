"""Conservative workspace-scoped Codex built-in capability candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agenthound.models.nodes import Agent, Asset, Capability


def codex_builtin_capabilities(agent: Agent, raw: dict[str, Any], workspace: Path) -> list[Capability]:
    # Configuration identifies a Codex environment, not a running/trusted session.
    # CLI overrides, managed policy and actual tool calls are unobserved.
    scope = (str(workspace.resolve()),)
    evidence = "builtin:codex:Read; platform file-read capability candidate; workspace assumed to be session cwd; runtime permissions, trust and overrides unverified"
    caps = [Capability(
        name="Read", cap_kind="BuiltInTool", agent_scope=agent.objectid,
        file_access="suspected", file_readonly=True, allowed_paths=scope,
        file_evidence=evidence, read_workspace=str(workspace.resolve()),
    )]
    features = raw.get("features", {})
    shell_disabled = isinstance(features, dict) and features.get("shell_tool") is False
    mode = raw.get("sandbox_mode")
    if mode in {"workspace-write", "danger-full-access"} and not shell_disabled:
        caps.append(Capability(
            name="Shell", cap_kind="BuiltInTool", agent_scope=agent.objectid,
            shell_exec="suspected", file_access="suspected", allowed_paths=scope,
            read_workspace=str(workspace.resolve()),
            shell_evidence=f"builtin:codex:Shell; configured sandbox_mode={mode}; command execution candidate within workspace; runtime tool/approval policy and overrides unverified",
            file_evidence="builtin:codex:Shell; workspace-scoped candidate; runtime access unverified",
        ))
    return caps


def codex_builtin_access(cap: Capability, asset: Asset, workspace: Path) -> dict[str, str] | None:
    root = workspace.resolve()
    if cap.read_workspace != str(root):
        return None
    try:
        requested = Path(asset.path).absolute()
        if not requested.is_relative_to(root) or not requested.resolve().is_relative_to(root):
            return None
    except (OSError, RuntimeError):
        return None
    return {"confidence": "suspected", "evidence": cap.file_evidence,
            "access_basis": "codex_workspace_capability_candidate"}
