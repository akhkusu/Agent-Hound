import json

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
    assert len(caps) == 1
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
    assert {cap.name for cap in result.capabilities} == {"docs", "filesystem"}
    assert len(result.edges) == 2


def test_codex_disabled_and_restricted_servers(tmp_path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.off]\nenabled = false\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-filesystem", "/demo"]\n[mcp_servers.limited]\ncommand = "npx"\nargs = ["-y", "@modelcontextprotocol/server-filesystem", "/demo"]\nenabled_tools = ["list_directory"]\n')
    _, caps = parse_config(config)
    assert [cap.name for cap in caps] == ["limited"]
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
