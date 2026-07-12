"""Parse agent configuration files from all supported platforms."""

from __future__ import annotations

import json
import os
import platform
import re
from pathlib import Path
from typing import Any

from agenthound.discovery.profiles import extract_allowed_paths, resolve_profile
from agenthound.models.nodes import Agent, Capability

# Direct facts: the configured binary itself is a shell.
_SHELL_BINARIES = {
    "bash", "sh", "zsh", "dash", "fish",
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh",
}

# Keyword fallback for servers not in the package registry. Tokens must match
# exactly (name split on non-alphanumerics), so "profile" never implies file
# access and "command-palette" never implies a shell. Deliberately excludes
# generic words (command, run, web, url, remote, ...) — those produced the
# false positives listed in issue #1. Keyword hits are only ever "suspected".
_SHELL_TOKENS = {"shell", "bash", "zsh", "terminal", "exec", "execute"}
_FILE_TOKENS = {"filesystem", "file", "files", "fs"}
_READONLY_LEAD_TOKENS = {"read", "list", "search", "get", "view"}
_NETWORK_TOKENS = {
    "fetch", "http", "https", "curl", "browser", "puppeteer", "playwright",
    "download", "upload",
}
_GIT_TOKENS = {"git"}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokenize(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        tokens.update(t for t in _TOKEN_SPLIT.split(part.lower()) if t)
    return tokens

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


def _build_mcp_capability(name: str, config: dict[str, Any]) -> Capability:
    """Derive capability flags in three layers, most reliable first.

    1. Known package profile (confirmed) — the package identity is a fact
       stated in the config.
    2. Direct config facts (confirmed) — the command binary is a shell.
    3. Name-token keywords (suspected) — only when no profile matched, so a
       benign known package is never re-guessed by its name.
    """
    url = config.get("url")
    command = config.get("command")
    args = [str(a) for a in config.get("args", []) if a is not None]
    transport = "sse" if url else "stdio"
    cmd_str: str | None = None
    if command:
        cmd_str = command + (" " + " ".join(args) if args else "")

    fields: dict[str, Any] = {}

    hit = resolve_profile(command, args)
    if hit is not None:
        profile, pkg_index = hit
        evidence = f"package:{profile.package}"
        if profile.note:
            evidence += f" ({profile.note})"
        if profile.file_access != "none":
            fields["file_access"] = profile.file_access
            fields["file_evidence"] = evidence
            fields["file_readonly"] = profile.file_readonly
            if profile.scoped_paths:
                fields["allowed_paths"] = extract_allowed_paths(args, pkg_index)
        if profile.shell_exec != "none":
            fields["shell_exec"] = profile.shell_exec
            fields["shell_evidence"] = evidence
        if profile.network_send != "none":
            fields["network_send"] = profile.network_send
            fields["network_evidence"] = evidence
        if profile.git_write != "none":
            fields["git_write"] = profile.git_write
            fields["git_evidence"] = evidence
    else:
        tokens = _tokenize(name, *args)
        if shell_hits := _SHELL_TOKENS & tokens:
            fields["shell_exec"] = "suspected"
            fields["shell_evidence"] = f"keyword:{sorted(shell_hits)[0]}"
        if file_hits := _FILE_TOKENS & tokens:
            fields["file_access"] = "suspected"
            fields["file_evidence"] = f"keyword:{sorted(file_hits)[0]}"
            name_tokens = [t for t in _TOKEN_SPLIT.split(name.lower()) if t]
            fields["file_readonly"] = bool(
                name_tokens and name_tokens[0] in _READONLY_LEAD_TOKENS
            )
        if network_hits := _NETWORK_TOKENS & tokens:
            fields["network_send"] = "suspected"
            fields["network_evidence"] = f"keyword:{sorted(network_hits)[0]}"
        if git_hits := _GIT_TOKENS & tokens:
            fields["git_write"] = "suspected"
            fields["git_evidence"] = f"keyword:{sorted(git_hits)[0]}"

    # A shell binary in the config is a fact regardless of profile/keywords.
    if command and os.path.basename(command) in _SHELL_BINARIES:
        fields["shell_exec"] = "confirmed"
        fields["shell_evidence"] = f"shell-binary:{os.path.basename(command)}"

    return Capability(
        name=name,
        cap_kind="MCPServer",
        command=cmd_str,
        transport=transport,
        **fields,
    )


def _parse_mcp_servers(servers_dict: dict[str, Any]) -> list[Capability]:
    return [_build_mcp_capability(name, config) for name, config in servers_dict.items()]


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
                        shell_evidence=f"hook:{event}",
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
