"""Capability parsing must not publish command arguments or parser inputs."""

import json
from pathlib import Path
import subprocess

import pytest

from agenthound.collectors.capabilities import collect_from_config
from agenthound.discovery.claude_mcp import load_object
from agenthound.discovery.claude_settings import collect_claude_settings
from agenthound.discovery.codex_settings import read_codex_config
from agenthound.discovery.config_parser import parse_config
from agenthound.discovery.profiles import extract_allowed_paths, extract_repo_scope
from agenthound.output.opengraph import build_opengraph


def test_full_graph_omits_hook_bodies_and_mcp_secret_arguments(tmp_path, monkeypatch):
    config = tmp_path / "cursor_mcp.json"
    config.write_text(json.dumps({
        "mcpServers": {
            "filesystem": {"command": "npx", "args": [
                "@modelcontextprotocol/server-filesystem", "/demo", "--token", "SECRET_MCP_ARGUMENT"
            ]},
            "inline": {"command": "python -c SECRET_INLINE_COMMAND"},
        },
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{
            "type": "command", "command": 'python -c "SECRET_HOOK_SCRIPT_BODY" --password SECRET_HOOK_ARGUMENT'
        }]}]},
    }))

    def forbidden(*args, **kwargs):
        pytest.fail("Parsing must not execute configured commands")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = collect_from_config(config)
    hook = next(cap for cap in result.capabilities if cap.cap_kind == "ShellHook")
    filesystem = next(cap for cap in result.capabilities if cap.name == "filesystem")
    assert hook.command is None
    assert hook.shell_exec == "none"
    assert filesystem.command == "npx"
    assert filesystem.allowed_paths == ("/demo",)
    assert any(edge.end == hook.objectid for edge in result.edges)
    graph = json.dumps(build_opengraph(result))
    for marker in ("SECRET_MCP_ARGUMENT", "SECRET_INLINE_COMMAND", "SECRET_HOOK_SCRIPT_BODY", "SECRET_HOOK_ARGUMENT"):
        assert marker not in graph


def test_scope_parsers_do_not_export_unknown_option_values():
    assert extract_allowed_paths(["--api-key", "SECRET_VALUE"], 0) == ()
    assert extract_allowed_paths(["/allowed", "--api-key=SECRET_VALUE"], 0) == ("/allowed",)
    assert extract_repo_scope(["--token", "SECRET_VALUE"], 0) is None
    assert extract_repo_scope(["--token", "SECRET_VALUE", "--repository", "/repo"], 0) == ("/repo",)


def _read(kind, path):
    if kind == "config":
        return parse_config(path)
    if kind == "claude-state":
        return load_object(path)
    if kind == "claude-settings":
        return collect_claude_settings([path], path.parent.parent)
    return read_codex_config(path)


@pytest.mark.parametrize("kind", ["config", "claude-state", "claude-settings", "codex"])
def test_oversized_configs_fail_before_opening_file(tmp_path, monkeypatch, kind):
    path = tmp_path / ".claude/settings.json" if kind == "claude-settings" else tmp_path / "config.toml" if kind == "codex" else tmp_path / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SECRET_OVERSIZED_VALUE" * 4000)
    opened = []
    original = Path.open

    def tracked(self, *args, **kwargs):
        if self == path:
            opened.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked)
    with pytest.raises(ValueError) as error:
        _read(kind, path)
    assert "64 KiB" in str(error.value)
    assert "SECRET_OVERSIZED_VALUE" not in str(error.value)
    assert opened == []


@pytest.mark.parametrize("kind", ["config", "claude-state", "claude-settings", "codex"])
def test_malformed_config_errors_do_not_include_parser_input(tmp_path, kind):
    path = tmp_path / ".claude/settings.json" if kind == "claude-settings" else tmp_path / "config.toml" if kind == "codex" else tmp_path / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('key = SECRET_MALFORMED_VALUE' if kind == "codex" else '{"token": SECRET_MALFORMED_VALUE}')
    with pytest.raises(ValueError) as error:
        _read(kind, path)
    assert "SECRET_MALFORMED_VALUE" not in str(error.value)
    assert "Cannot parse" in str(error.value)


@pytest.mark.parametrize("definition", [
    {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": {"token": "SECRET_INVALID_VALUE"}}]}]}},
    {"mcpServers": {"remote": {"url": "https://example.com:SECRET_INVALID_VALUE/mcp"}}},
    {"mcpServers": {"remote": {"command": {"token": "SECRET_INVALID_VALUE"}}}},
])
def test_invalid_values_raise_sanitized_validation_errors(tmp_path, definition):
    path = tmp_path / "claude_code_settings.json"
    path.write_text(json.dumps(definition))
    with pytest.raises(ValueError) as error:
        parse_config(path)
    assert "SECRET_INVALID_VALUE" not in str(error.value)
    assert error.value.__cause__ is None


def test_config_symlink_cannot_escape_its_parent_root(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text('{"mcpServers": {}}')
    root = tmp_path / "root"
    root.mkdir()
    link = root / "config.json"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        parse_config(link)
