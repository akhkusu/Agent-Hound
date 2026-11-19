import json

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.collectors.capabilities import collect_from_configs
from agenthound.collectors.sources import collect_sources
from agenthound.discovery.config_parser import discover_config_files, parse_config
from agenthound.models.nodes import Agent


def test_codex_explicit_toml_config(tmp_path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.filesystem]\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-filesystem", "/demo"]\n[mcp_servers.filesystem.env]\nSECRET = "never-export"\n')
    agent, caps = parse_config(config)
    assert agent.name == "codex"
    assert len(caps) == 2
    assert caps[0].name == "filesystem"
    assert caps[0].file_access == "confirmed"
    assert caps[0].allowed_paths is None
    assert "never-export" not in json.dumps(caps[0].to_opengraph())


def test_codex_discovery_merges_user_and_project(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    workspace = tmp_path / "project"
    home.mkdir()
    (workspace / ".codex").mkdir(parents=True)
    user_config = home / "config.toml"
    project_config = workspace / ".codex" / "config.toml"
    user_config.write_text('[mcp_servers.docs]\nurl = "https://example.com/mcp"\n')
    project_config.write_text('[mcp_servers.filesystem]\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-filesystem", "/demo"]\n')
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr("agenthound.discovery.config_parser._DISCOVERY_CANDIDATES", [])
    found = discover_config_files(workspace)
    assert user_config in found and project_config in found
    result = collect_from_configs([path for path in found if path in {user_config, project_config}], workspace=workspace)
    codex_agents = [agent for agent in result.agents if agent.name == "codex"]
    assert len(codex_agents) == 1
    assert {cap.name for cap in result.capabilities} == {"docs", "filesystem", "Read"}
    assert len(result.edges) == 3


def test_codex_disabled_and_restricted_servers(tmp_path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.off]\nenabled = false\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-filesystem", "/demo"]\n[mcp_servers.limited]\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-filesystem", "/demo"]\nenabled_tools = ["list_directory"]\n')
    _, caps = parse_config(config)
    assert [cap.name for cap in caps] == ["limited", "Read"]
    assert caps[0].file_access == "suspected"


def test_codex_agents_instruction_override(tmp_path):
    (tmp_path / "AGENTS.md").write_text("base")
    (tmp_path / "AGENTS.override.md").write_text("override")
    agent = Agent(name="codex", platform="Codex", config_path=str(tmp_path / ".codex"))
    result = collect_sources(agent, tmp_path, "workspace")
    influenced = {edge.start for edge in result.edges}
    override = next(source for source in result.sources if source.name == "AGENTS.override.md")
    base = next(source for source in result.sources if source.name == "AGENTS.md")
    assert override.objectid in influenced
    assert base.objectid not in influenced


def test_codex_cli_export_omits_env_values(tmp_path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.docs]\nurl = "https://example.com/mcp"\n[mcp_servers.docs.env]\nAPI_KEY = "never-export"\n[mcp_servers.filesystem]\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-filesystem", "/demo", "--api-key", "never-export-arg"]\n')
    output = tmp_path / "output.json"
    result = CliRunner().invoke(cli, ["-c", str(config), "-w", str(tmp_path), "--scope", "workspace", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert "never-export" not in output.read_text()
    assert "never-export-arg" not in output.read_text()
    graph = json.loads(output.read_text())["graph"]
    assert any(node["properties"]["name"] == "CODEX" for node in graph["nodes"])


def test_codex_builtin_read_links_source_to_workspace_asset(tmp_path):
    from agenthound.collectors.assets import collect_assets

    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("synthetic input", encoding="utf-8")
    (tmp_path / ".env").write_text("SYNTHETIC=placeholder", encoding="utf-8")
    result = collect_from_configs([config], workspace=tmp_path)
    read = next(c for c in result.capabilities if c.name == "Read")
    assert read.cap_kind == "BuiltInTool"
    assert read.file_access == "suspected"
    assert read.shell_exec == "none"
    sources = collect_sources(result.agents[0], tmp_path, "workspace")
    assets = collect_assets(result.capabilities, tmp_path, "workspace")
    assert any(e.kind == "Influences" for e in sources.edges)
    assert any(e.kind == "HasCapability" and e.end == read.objectid for e in result.edges)
    assert any(e.kind == "CanAccess" and e.start == read.objectid for e in assets.edges)
    assert all(e.properties["confidence"] == "suspected" for e in assets.edges)


@pytest.mark.parametrize("mode,disabled,has_shell", [
    ("workspace-write", False, True), ("danger-full-access", False, True),
    ("read-only", False, False), ("workspace-write", True, False),
])
def test_codex_shell_requires_explicit_configuration(tmp_path, mode, disabled, has_shell):
    from agenthound.discovery.codex_builtin import codex_builtin_access
    from agenthound.discovery.codex_settings import collect_codex_settings
    from agenthound.models.nodes import Asset

    config = tmp_path / "config.toml"
    config.write_text(f'sandbox_mode = "{mode}"\n[features]\nshell_tool = {str(not disabled).lower()}\n', encoding="utf-8")
    _, caps = collect_codex_settings([config], tmp_path)
    assert any(c.name == "Shell" for c in caps) is has_shell
    if has_shell:
        shell = next(c for c in caps if c.name == "Shell")
        assert shell.shell_exec == "suspected"
        outside = Asset(name="outside", asset_kind="EnvFile", path=str(tmp_path.parent / ".env"))
        assert codex_builtin_access(shell, outside, tmp_path) is None
