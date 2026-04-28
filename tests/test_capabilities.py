"""Tests for the Capabilities Collector."""

from pathlib import Path
import pytest
from agenthound.collectors.capabilities import collect_from_config, collect_from_configs
from agenthound.models.nodes import Capability

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
