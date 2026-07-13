"""Tests for the Capabilities Collector."""

from pathlib import Path
from agenthound.collectors.capabilities import collect_from_config, collect_from_configs

FIXTURES = Path(__file__).parent / "fixtures"


def test_collect_from_config_returns_result():
    result = collect_from_config(FIXTURES / "claude_desktop_config.json")
    assert len(result.agents) == 1
    assert len(result.capabilities) == 3


def test_has_capability_edges_created():
    result = collect_from_config(FIXTURES / "claude_desktop_config.json")
    agent = result.agents[0]
    cap_ids = {c.objectid for c in result.capabilities}
    edges = [e for e in result.edges if e.kind == "HasCapability"]
    assert len(edges) == 3
    for edge in edges:
        assert edge.start == agent.objectid
        assert edge.end in cap_ids


def test_collect_from_configs_deduplicates_agents():
    paths = [
        FIXTURES / "claude_desktop_config.json",
        FIXTURES / "vscode_mcp.json",
    ]
    result = collect_from_configs(paths)
    assert len(result.agents) == 2
    agent_names = {a.name for a in result.agents}
    assert "claude-desktop" in agent_names
    assert "vscode-copilot" in agent_names


def test_collect_from_configs_deduplicates_capabilities():
    # Same config file twice → only one set of capabilities
    path = FIXTURES / "claude_desktop_config.json"
    result = collect_from_configs([path, path])
    assert len(result.agents) == 1
    assert len(result.capabilities) == 3


def test_same_named_server_not_shared_across_agents(tmp_path):
    import json
    # Two different agents (detected by filename) each with a "filesystem"
    # server scoped to a different directory.
    desktop_dir = tmp_path / "desktop"
    cursor_dir = tmp_path / "cursor"
    desktop_dir.mkdir()
    cursor_dir.mkdir()
    desktop = desktop_dir / "claude_desktop_config.json"
    cursor = cursor_dir / "cursor_mcp.json"
    desktop.write_text(json.dumps({"mcpServers": {"filesystem": {
        "command": "npx", "args": ["@modelcontextprotocol/server-filesystem", "/project-a"]}}}))
    cursor.write_text(json.dumps({"mcpServers": {"filesystem": {
        "command": "npx", "args": ["@modelcontextprotocol/server-filesystem", "/project-b"]}}}))

    result = collect_from_configs([desktop, cursor])
    fs_caps = [c for c in result.capabilities if c.name == "filesystem"]
    # Two distinct capability nodes, each with its own scope (no union).
    assert len(fs_caps) == 2
    scopes = {c.allowed_paths for c in fs_caps}
    assert (("/project-a",)) in scopes
    assert (("/project-b",)) in scopes

    # Each agent's HasCapability points only to its own filesystem cap.
    agents = {a.objectid: a for a in result.agents}
    hascap = [e for e in result.edges if e.kind == "HasCapability"]
    for cap in fs_caps:
        owners = {e.start for e in hascap if e.end == cap.objectid}
        assert len(owners) == 1
        assert owners.pop() in agents
