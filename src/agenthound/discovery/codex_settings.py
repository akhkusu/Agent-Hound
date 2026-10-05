"""Parse Codex TOML configuration without exporting environment values."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from agenthound.discovery.config_parser import _build_mcp_capability
from agenthound.models.nodes import Agent, Capability


def codex_config_paths(workspace: Path) -> list[Path]:
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return [home / "config.toml", workspace / ".codex" / "config.toml"]


def read_codex_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"Cannot parse Codex config {path}: {exc}") from exc
    return raw


def collect_codex_settings(paths: list[Path], workspace: Path) -> tuple[Agent, list[Capability]]:
    workspace = workspace.resolve()
    agent = Agent(name="codex", platform="Codex", config_path=str(workspace / ".codex"))
    servers: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in paths:
        raw = read_codex_config(path)
        configured = raw.get("mcp_servers", {})
        if not isinstance(configured, dict):
            continue
        for name, server in configured.items():
            if isinstance(server, dict):
                servers[name] = (server, path)

    capabilities = []
    for name, (server, path) in servers.items():
        if server.get("enabled") is False:
            continue
        config = {key: server[key] for key in ("command", "args", "url") if key in server}
        if not isinstance(config.get("command", ""), str) or not isinstance(config.get("args", []), list):
            continue
        capability = _build_mcp_capability(name, config, agent.objectid)
        restricted = bool(server.get("enabled_tools") or server.get("disabled_tools"))
        if restricted:
            capability = capability.model_copy(update={
                "shell_exec": "suspected" if capability.shell_exec == "confirmed" else capability.shell_exec,
                "file_access": "suspected" if capability.file_access == "confirmed" else capability.file_access,
                "network_send": "suspected" if capability.network_send == "confirmed" else capability.network_send,
                "git_write": "suspected" if capability.git_write == "confirmed" else capability.git_write,
            })
        capability = capability.model_copy(update={
            "command": None,
            "allowed_paths": None,
            "repo_scope": None,
            "mcp_config_path": str(path),
            "mcp_scope": "project" if path.parent.resolve() == workspace / ".codex" else "user",
            "mcp_evidence": "Codex MCP configuration observed; trust, tool policy and runtime use unverified",
        })
        capabilities.append(capability)
    return agent, capabilities
