"""Parse agent configuration files from all supported platforms."""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path
from typing import Any

from agenthound.models.nodes import Capability, Agent

_SHELL_KEYWORDS = ("shell", "bash", "execute", "terminal", "command", "run", "exec")
_SHELL_PATTERN = re.compile(r'\b(' + '|'.join(_SHELL_KEYWORDS) + r')\b')

_CLAUDE_DESKTOP_PATH = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.system() == "Darwin"
    else Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
)

_DISCOVERY_CANDIDATES = [
    # (path, platform_hint)
    (_CLAUDE_DESKTOP_PATH, "claude-desktop"),
    (Path.home() / ".claude" / "settings.json", "claude-code"),
    (Path.home() / ".vscode" / "mcp.json", "vscode"),
    (Path.home() / ".cursor" / "mcp.json", "cursor"),
    (Path.home() / ".codeium" / "windsurf" / "mcp_config.json", "windsurf"),
    *(
        [(Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json", "claude-desktop")]
        if os.environ.get("APPDATA")
        else []
    ),
]


def discover_config_files() -> list[Path]:
    """Return paths of all agent config files found on this system."""
    found: list[Path] = []
    seen: set[Path] = set()
    for path, _ in _DISCOVERY_CANDIDATES:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved not in seen and path.exists():
            found.append(path)
            seen.add(resolved)
    return found


def _detect_agent(config_path: Path) -> tuple[str, str]:
    """Return (name, platform) for a config file path."""
    path_str = str(config_path)
    name = config_path.name
    # Match by filename first (covers fixtures and non-standard locations)
    if "claude_desktop_config" in name:
        return "claude-desktop", "Claude Desktop"
    if "claude_code_settings" in name:
        return "claude-code", "Claude Code"
    if "vscode_mcp" in name:
        return "vscode-copilot", "VS Code Copilot"
    if "cursor_mcp" in name:
        return "cursor", "Cursor"
    if "windsurf_mcp" in name:
        return "windsurf", "Windsurf"
    # Fall back to path heuristics for standard install locations
    if ".claude" in path_str and name == "settings.json":
        return "claude-code", "Claude Code"
    if ".vscode" in path_str:
        return "vscode-copilot", "VS Code Copilot"
    if ".cursor" in path_str:
        return "cursor", "Cursor"
    if "windsurf" in path_str or "codeium" in path_str:
        return "windsurf", "Windsurf"
    return "unknown-agent", "Unknown Agent"


def _is_shell_server(name: str, command: str | None, args: list[str]) -> bool:
    haystack = " ".join(filter(None, [name.lower(), command or "", *(str(a) for a in args if a is not None)])).lower()
    return bool(_SHELL_PATTERN.search(haystack))


def _parse_mcp_servers(servers_dict: dict[str, Any]) -> list[Capability]:
    caps: list[Capability] = []
    for name, config in servers_dict.items():
        url = config.get("url")
        command = config.get("command")
        args = config.get("args", [])
        transport = "sse" if url else "stdio"
        cmd_str: str | None = None
        if command:
            cmd_str = command + (" " + " ".join(args) if args else "")
        caps.append(Capability(
            name=name,
            cap_kind="MCPServer",
            command=cmd_str,
            transport=transport,
            has_shell=_is_shell_server(name, command, args),
        ))
    return caps


def _parse_hooks(hooks_dict: dict[str, Any]) -> list[Capability]:
    caps: list[Capability] = []
    for event, hook_groups in hooks_dict.items():
        if not isinstance(hook_groups, list):
            continue
        for group in hook_groups:
            matcher = group.get("matcher", event)
            for hook in group.get("hooks", []):
                if hook.get("type") == "command":
                    caps.append(Capability(
                        name=f"hook:{event}:{matcher}",
                        cap_kind="ShellHook",
                        command=hook.get("command"),
                        has_shell=True,
                    ))
    return caps


def parse_config(config_path: Path) -> tuple[Agent, list[Capability]]:
    """Parse an agent config file. Returns (Agent, list[Capability])."""
    try:
        with open(config_path) as f:
            raw: dict[str, Any] = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot parse config {config_path}: {exc}") from exc

    agent_name, platform_name = _detect_agent(config_path)
    agent = Agent(name=agent_name, platform=platform_name, config_path=str(config_path))

    caps: list[Capability] = []

    # MCP servers — all formats
    servers: dict[str, Any] = raw.get("mcpServers", {})
    if not servers:
        servers = raw.get("servers", {})
    if not servers and "mcp" in raw:
        servers = raw["mcp"].get("servers", {})
    caps.extend(_parse_mcp_servers(servers))

    # Claude Code hooks
    if "hooks" in raw:
        caps.extend(_parse_hooks(raw["hooks"]))

    return agent, caps
