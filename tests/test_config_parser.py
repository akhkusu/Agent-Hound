import json
from pathlib import Path


from agenthound.discovery.config_parser import discover_config_files, parse_config

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_claude_desktop_config():
    agent, caps = parse_config(FIXTURES / "claude_desktop_config.json")
    assert agent.platform == "Claude Desktop"
    assert agent.name == "claude-desktop"
    assert len(caps) == 3
    names = {c.name for c in caps}
    assert "filesystem" in names
    assert "github" in names
    assert "remote" in names


def test_claude_desktop_stdio_server():
    _, caps = parse_config(FIXTURES / "claude_desktop_config.json")
    fs = next(c for c in caps if c.name == "filesystem")
    assert fs.cap_kind == "MCPServer"
    assert fs.transport == "stdio"
    assert "npx" in fs.command


def test_claude_desktop_sse_server():
    _, caps = parse_config(FIXTURES / "claude_desktop_config.json")
    remote = next(c for c in caps if c.name == "remote")
    assert remote.transport == "sse"


def test_parse_claude_code_settings():
    agent, caps = parse_config(FIXTURES / "claude_code_settings.json")
    assert agent.platform == "Claude Code"
    cap_kinds = {c.cap_kind for c in caps}
    assert "MCPServer" in cap_kinds
    assert "ShellHook" in cap_kinds


def test_claude_code_hooks_have_shell():
    _, caps = parse_config(FIXTURES / "claude_code_settings.json")
    hooks = [c for c in caps if c.cap_kind == "ShellHook"]
    assert len(hooks) == 2
    assert all(h.has_shell for h in hooks)


def test_parse_vscode_config():
    agent, caps = parse_config(FIXTURES / "vscode_mcp.json")
    assert agent.platform == "VS Code Copilot"
    assert len(caps) == 1
    assert caps[0].name == "my-tool"


def test_parse_cursor_config():
    agent, caps = parse_config(FIXTURES / "cursor_mcp.json")
    assert agent.platform == "Cursor"
    assert len(caps) == 1


def test_parse_windsurf_config():
    agent, caps = parse_config(FIXTURES / "windsurf_mcp.json")
    assert agent.platform == "Windsurf"
    assert len(caps) == 1


def test_shell_server_detected(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "bash-exec": {
                "command": "npx",
                "args": ["-y", "mcp-server-shell-execute"]
            }
        }
    }))
    _, caps = parse_config(cfg)
    assert caps[0].has_shell is True


def test_non_shell_server_not_flagged(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "memory": {"command": "npx", "args": ["-y", "server-memory"]}
        }
    }))
    _, caps = parse_config(cfg)
    assert caps[0].has_shell is False


def test_objectid_deterministic_across_parses():
    _, caps1 = parse_config(FIXTURES / "claude_desktop_config.json")
    _, caps2 = parse_config(FIXTURES / "claude_desktop_config.json")
    assert caps1[0].objectid == caps2[0].objectid


def test_discover_config_files_returns_list():
    # discover_config_files scans known system paths — on the test runner
    # those paths may not exist, so we just verify it returns a list of Paths.
    paths = discover_config_files()
    assert isinstance(paths, list)
    for p in paths:
        assert isinstance(p, Path)
        assert p.exists()


def test_shell_binary_launcher_detected_case_insensitive(tmp_path):
    # A shell binary as command is a launcher signal (suspected), and must be
    # recognized regardless of case / path separator.
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "legacy-tool": {"command": "C:\\Windows\\System32\\PowerShell.EXE", "args": []}
        }
    }))
    _, caps = parse_config(cfg)
    assert caps[0].shell_exec == "suspected"
    assert caps[0].shell_evidence.startswith("shell-binary-launcher:")


def test_hooks_remain_confirmed_shell(tmp_path):
    # Hooks execute configured commands on events — a real, confirmed capability.
    _, caps = parse_config(FIXTURES / "claude_code_settings.json")
    hooks = [c for c in caps if c.cap_kind == "ShellHook"]
    assert hooks and all(h.shell_exec == "confirmed" for h in hooks)


def test_data_arg_does_not_seed_keyword(tmp_path):
    # A path argument containing "git" must not imply git_write.
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {
            "notes": {"command": "npx", "args": ["-y", "notes-mcp", "/home/user/git/cache"]}
        }
    }))
    _, caps = parse_config(cfg)
    assert caps[0].git_write == "none"


def test_same_named_server_distinct_across_agents():
    # "filesystem" in two different agent configs must be two nodes.
    from agenthound.collectors.capabilities import collect_from_configs
    result = collect_from_configs([
        FIXTURES / "claude_desktop_config.json",
        FIXTURES / "vscode_mcp.json",
    ])
    fs_caps = [c for c in result.capabilities if c.name == "filesystem"]
    # Only claude_desktop has "filesystem"; assert agent_scope is populated so
    # a same-named server elsewhere would not collide.
    assert all(c.agent_scope for c in fs_caps)
