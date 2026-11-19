"""Parse agent configuration files from all supported platforms."""

from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agenthound.discovery.behavior_files import read_json, read_jsonc
from agenthound.discovery.builtin_read import read_capability
from agenthound.discovery.builtin_bash import bash_capability
from agenthound.discovery.claude_mcp import claude_mcp_paths, mcp_capabilities

from agenthound.discovery.profiles import (
    claims_for_profile,
    compatibility_identity,
    legacy_fields,
    sanitized_endpoint,
    extract_allowed_paths,
    extract_repo_scope,
    identity_tokens,
    resolve_profile,
)
from agenthound.models.nodes import Agent, Capability

# Keyword fallback for servers not in the package registry. Tokens must match
# exactly (name split on non-alphanumerics), so "profile" never implies file
# access and "command-palette" never implies a shell. Deliberately excludes
# generic words (command, run, web, url, remote, ...) — those produced the
# false positives listed in issue #1. Keyword hits are only ever "suspected".
_SHELL_TOKENS = {"shell", "bash", "zsh", "terminal", "exec", "execute"}
_FILE_TOKENS = {"filesystem", "file", "files", "fs"}
_READONLY_LEAD_TOKENS = {"read", "list", "search", "get", "view"}
_NETWORK_TOKENS = {
    "fetch",
    "http",
    "https",
    "curl",
    "browser",
    "puppeteer",
    "playwright",
    "download",
    "upload",
}
_GIT_TOKENS = {"git"}

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _tokenize(*parts: str) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        tokens.update(t for t in _TOKEN_SPLIT.split(part.lower()) if t)
    return tokens


_CLAUDE_DESKTOP_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Claude"
    / "claude_desktop_config.json"
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
        [
            (
                Path(os.environ.get("APPDATA", ""))
                / "Claude"
                / "claude_desktop_config.json",
                "claude-desktop",
            )
        ]
        if os.environ.get("APPDATA")
        else []
    ),
]


def claude_settings_paths(workspace: Path) -> list[Path]:
    """Return ordered user/shared/local candidates; never create settings."""
    user_dir = Path(
        os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    ).expanduser()
    return [
        user_dir / "settings.json",
        workspace / ".claude" / "settings.json",
        workspace / ".claude" / "settings.local.json",
    ]


def discover_config_files(workspace: Path | None = None) -> list[Path]:
    """Return paths of all agent config files found on this system."""
    found: list[Path] = []
    seen: set[Path] = set()
    workspace = (workspace or Path.cwd()).absolute()
    copilot_home = Path(
        os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot"))
    ).expanduser()
    candidates = [path for path, hint in _DISCOVERY_CANDIDATES if hint != "claude-code"]
    candidates.extend(claude_settings_paths(workspace or Path.cwd()))
    candidates.extend(claude_mcp_paths(workspace or Path.cwd()))
    from agenthound.discovery.codex_settings import codex_config_paths

    candidates.extend(codex_config_paths(workspace or Path.cwd()))
    candidates.extend(
        [
            workspace / ".cursor" / "mcp.json",
            workspace / ".vscode" / "mcp.json",
            workspace / ".github" / "mcp.json",
            workspace / ".github" / "copilot" / "settings.json",
            workspace / ".github" / "copilot" / "settings.local.json",
            copilot_home / "settings.json",
            copilot_home / "mcp-config.json",
        ]
    )
    from agenthound.discovery.behavior_files import safe_file

    for path in candidates:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            continue
        boundary = (
            workspace if path.absolute().is_relative_to(workspace) else path.parent
        )
        if resolved not in seen and safe_file(path, boundary):
            found.append(path)
            seen.add(resolved)
    return found


def _detect_agent(config_path: Path) -> tuple[str, str]:
    """Return (name, platform) for a config file path."""
    path_str = str(config_path)
    name = config_path.name
    copilot_home = Path(
        os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot"))
    ).expanduser()
    if (
        (
            (
                config_path.parent == copilot_home
                or config_path.parent.name == ".copilot"
            )
            and name in {"settings.json", "mcp-config.json"}
        )
        or (
            config_path.parent.name == "copilot"
            and config_path.parent.parent.name == ".github"
            and name in {"settings.json", "settings.local.json"}
        )
        or (config_path.parent.name == ".github" and name == "mcp.json")
    ):
        return "copilot-cli", "GitHub Copilot CLI"
    codex_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    if (
        name == "config.toml"
        and (
            config_path.parent.name == ".codex"
            or config_path.absolute().parent == codex_home.absolute()
        )
    ) or name == "codex_config.toml":
        return "codex", "Codex"
    if name in {".claude.json", ".mcp.json"}:
        return "claude-code", "Claude Code"
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
    claude_home = Path(
        os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    ).expanduser()
    if (
        config_path.parent.name == ".claude"
        or config_path.absolute().parent == claude_home.absolute()
    ) and name in {"settings.json", "settings.local.json"}:
        return "claude-code", "Claude Code"
    if ".vscode" in path_str:
        return "vscode-copilot", "VS Code Copilot"
    if ".cursor" in path_str:
        return "cursor", "Cursor"
    if "windsurf" in path_str or "codeium" in path_str:
        return "windsurf", "Windsurf"
    return "unknown-agent", "Unknown Agent"


def _build_mcp_capability(
    name: str, config: dict[str, Any], agent_scope: str
) -> Capability:
    """Validate static MCP metadata without exposing malformed secret values."""
    if not isinstance(config, dict):
        raise ValueError("MCP server definition must be an object")
    if not isinstance(config.get("command", ""), str) or not isinstance(
        config.get("args", []), list
    ):
        raise ValueError("MCP command must be a string and args must be a list")
    if config.get("url") is not None and not isinstance(config["url"], str):
        raise ValueError("MCP endpoint must be a string")
    try:
        # Validate URL structure before the identity/output sanitizer can omit a
        # malformed endpoint. Keep the upstream sanitized validation behavior.
        if isinstance(config.get("url"), str):
            urlsplit(config["url"]).port
        return _build_mcp_capability_data(name, config, agent_scope)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        raise ValueError("MCP server contains invalid configuration metadata") from None


def _build_mcp_capability_data(
    name: str, config: dict[str, Any], agent_scope: str
) -> Capability:
    """Identify implementations and project verified claims conservatively.

    Exact implementation claims are distinct from runtime access. Unknown
    implementations may receive suspected name heuristics; shell launchers
    alone do not expose an agent-controlled execution capability.
    """
    url = config.get("url")
    command = config.get("command")
    args = [str(a) for a in config.get("args", []) if a is not None]
    transport = str(
        config.get("type") or config.get("transport") or ("http" if url else "stdio")
    )
    cmd_str: str | None = None
    if command:
        # Arguments frequently contain credentials. Export only the launcher;
        # package identity and safe scope details have dedicated properties.
        base = os.path.basename(command.replace("\\", "/"))
        cmd_str = base if re.fullmatch(r"[A-Za-z0-9_.-]+", base) else None

    fields: dict[str, Any] = {}

    enabled = config.get("enabled_tools", config.get("tool_allowlist", []))
    disabled = config.get("disabled_tools", config.get("tool_denylist", []))
    allowlist = (
        tuple(x for x in enabled if isinstance(x, str))
        if isinstance(enabled, list)
        else ()
    )
    denylist = (
        tuple(x for x in disabled if isinstance(x, str))
        if isinstance(disabled, list)
        else ()
    )
    allowlist_configured = "enabled_tools" in config or "tool_allowlist" in config
    hit = resolve_profile(command, args, url if isinstance(url, str) else None)
    if hit is not None:
        profile, payload_index = hit
        claims = claims_for_profile(
            profile, allowlist, denylist, allowlist_configured=allowlist_configured
        )
        legacy_alias = compatibility_identity(command, args, profile)
        if legacy_alias:
            claims = tuple(
                c.model_copy(
                    update={
                        "confidence": "suspected",
                        "evidence": "Unverified historical compatibility alias; "
                        + c.evidence,
                    }
                )
                for c in claims
            )
        fields.update(legacy_fields(claims))
        fields.update(
            package_name=profile.package,
            profile_id=profile.canonical_id,
            profile_status=profile.status,
            identity_confidence="suspected" if legacy_alias else "confirmed",
            implementation_confidence=(
                "confirmed"
                if claims and profile.status == "verified" and not legacy_alias
                else "suspected"
                if claims
                else "none"
            ),
            claims=claims,
        )
        identity_evidence = (
            "historical compatibility alias; product identity unverified"
            if legacy_alias
            else "exact configured implementation identity"
        )
        evidence = (
            f"package:{profile.package}; {identity_evidence}; runtime access unverified"
        )
        for key in (
            "file_evidence",
            "shell_evidence",
            "network_evidence",
            "git_evidence",
        ):
            if fields[key]:
                fields[key] = evidence + "; " + fields[key]
        if profile.scoped_paths and fields["file_access"] != "none":
            fields["allowed_paths"] = extract_allowed_paths(args, payload_index)
        if profile.repo_scoped and fields["git_write"] != "none":
            fields["repo_scope"] = extract_repo_scope(args, payload_index)
    else:
        # Only the server name and what actually executes (command basename +
        # the launched package) seed keyword detection. Path/option-value args
        # are data — a "/home/git/cache" arg must not imply git_write.
        identities = identity_tokens(command, args)
        tokens = _tokenize(name, *identities)
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

    env_names = (
        set(config.get("env", {})) if isinstance(config.get("env"), dict) else set()
    )
    inherited_env = config.get("env_vars", [])
    if isinstance(inherited_env, list):
        env_names.update(str(item) for item in inherited_env)
    header_names: set[str] = set()
    for key in ("headers", "http_headers", "env_http_headers"):
        value = config.get(key)
        if isinstance(value, dict):
            header_names.update(str(item) for item in value)
    if config.get("bearer_token_env_var"):
        env_names.add(str(config["bearer_token_env_var"]))
        header_names.add("Authorization")
    auth_parts = []
    if any(
        re.search(r"TOKEN|KEY|AUTH|CREDENTIAL|SECRET", item, re.I) for item in env_names
    ):
        auth_parts.append("environment")
    if any(
        item.lower() in {"authorization", "x-api-key", "api-key"}
        for item in header_names
    ):
        auth_parts.append("header")
    fields.update(
        configured_env_vars=tuple(sorted(env_names)),
        configured_header_names=tuple(sorted(header_names)),
        auth_kind="+".join(auth_parts) or "none",
        tool_allowlist=allowlist,
        tool_denylist=denylist,
        tool_allowlist_configured=allowlist_configured,
        discovery_status="configured",
    )
    if isinstance(url, str):
        fields["endpoint"] = sanitized_endpoint(url)

    return Capability(
        name=name,
        cap_kind="MCPServer",
        command=cmd_str,
        transport=transport,
        agent_scope=agent_scope,
        **fields,
    )


def _parse_mcp_servers(
    servers_dict: dict[str, Any], agent_scope: str
) -> list[Capability]:
    if not isinstance(servers_dict, dict):
        raise ValueError("MCP servers must be an object")
    return [
        _build_mcp_capability(name, config, agent_scope)
        for name, config in servers_dict.items()
    ]


def _parse_hooks(hooks_dict: dict[str, Any], agent_scope: str) -> list[Capability]:
    if not isinstance(hooks_dict, dict):
        raise ValueError("Hook definitions must be an object")
    caps: list[Capability] = []
    for event, hook_groups in hooks_dict.items():
        if not isinstance(hook_groups, list):
            continue
        for group in hook_groups:
            if not isinstance(group, dict):
                raise ValueError("Hook matcher groups must be objects")
            matcher = group.get("matcher", event)
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                raise ValueError("Hook handlers must be a list")
            for hook in handlers:
                if not isinstance(hook, dict):
                    raise ValueError("Hook handlers must be objects")
                if hook.get("type") == "command":
                    if not isinstance(hook.get("command"), str):
                        raise ValueError("Hook command must be a string")
                    caps.append(
                        Capability(
                            name=f"hook:{event}:{matcher}",
                            cap_kind="ShellHook",
                            # Hook commands may contain credentials and inline script
                            # bodies. Presence is modeled without exporting any text.
                            command=None,
                            agent_scope=agent_scope,
                            shell_evidence=f"hook:{event}; fixed command handler, not an agent-controlled shell tool",
                        )
                    )
    return caps


def parse_config(config_path: Path) -> tuple[Agent, list[Capability]]:
    """Parse an agent config file. Returns (Agent, list[Capability])."""
    if config_path.suffix == ".toml" and _detect_agent(config_path)[0] == "codex":
        from agenthound.discovery.codex_settings import collect_codex_settings

        return collect_codex_settings([config_path], config_path.parent.parent)
    reader = (
        read_jsonc
        if _detect_agent(config_path)[0] in {"vscode-copilot", "copilot-cli"}
        else read_json
    )
    raw = reader(config_path, config_path.parent)
    if raw is None:
        raise ValueError(
            f"Cannot parse config {config_path}: expected a readable JSON object of at most 64 KiB"
        )

    if config_path.name in {".claude.json", ".mcp.json"}:
        agent = Agent(
            name="claude-code", platform="Claude Code", config_path=str(config_path)
        )
        # Explicit -c is single-file: nested project state is not guessed.
        servers = raw.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("MCP servers must be an object")
        scope = "user" if config_path.name == ".claude.json" else "project"
        caps = mcp_capabilities(
            agent,
            {name: (server, config_path, scope) for name, server in servers.items()},
        )
        builtin = read_capability(agent, {})
        if builtin is not None:
            caps.append(builtin)
        return agent, caps
    return parse_config_data(config_path, raw)


def parse_config_data(
    config_path: Path, raw: dict[str, Any], agent: Agent | None = None
) -> tuple[Agent, list[Capability]]:
    """Parse loaded settings, optionally for an already identified session."""

    agent_name, platform_name = _detect_agent(config_path)
    agent = agent or Agent(
        name=agent_name, platform=platform_name, config_path=str(config_path)
    )
    # Scope capabilities to their owning agent so identically-named servers in
    # different agents stay distinct nodes.
    agent_scope = agent.objectid

    caps: list[Capability] = []

    # MCP servers — all formats
    servers: dict[str, Any] = raw.get("mcpServers", {})
    if not servers:
        servers = raw.get("servers", {})
    if not servers and "mcp" in raw:
        if not isinstance(raw["mcp"], dict):
            raise ValueError("MCP configuration must be an object")
        servers = raw["mcp"].get("servers", {})
    mcp_caps = _parse_mcp_servers(servers, agent_scope)
    mcp_evidence = "MCP configuration observed; runtime connection and advertised features unverified"
    caps.extend(
        cap.model_copy(
            update={
                "mcp_config_path": str(config_path),
                "mcp_scope": "config",
                "mcp_evidence": mcp_evidence,
            }
        )
        for cap in mcp_caps
    )

    # Claude Code hooks
    if "hooks" in raw:
        caps.extend(_parse_hooks(raw["hooks"], agent_scope))

    if agent.name == "claude-code":
        builtin = read_capability(agent, raw)
        if builtin is not None:
            caps.append(builtin)
        builtin = bash_capability(agent, raw)
        if builtin is not None:
            caps.append(builtin)

    return agent, caps
