"""Built-in Bash detection uses only explicit, broad Claude Code settings."""

import json

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.collectors.capabilities import collect_from_configs


def test_explicit_bash_allow(tmp_path):
    project = tmp_path / "project"
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"allow": ["Read", "Bash"]}}))
    result = collect_from_configs([settings], workspace=project)
    bash = next(cap for cap in result.capabilities if cap.name == "Bash")
    assert bash.cap_kind == "BuiltInTool"
    assert bash.shell_exec == "suspected"
    assert bash.file_access == "none"
    assert "explicit whole-tool allow" in bash.shell_evidence
    edge = next(edge for edge in result.edges if edge.end == bash.objectid)
    assert edge.properties["confidence"] == "suspected"
    assert "Bash" in edge.properties["evidence"]


@pytest.mark.parametrize("permissions", [
    {},
    {"allow": ["Read"]},
    {"allow": ["Bash(git status)"]},
    {"allow": ["Bash"], "deny": ["Bash"]},
    {"allow": ["Bash"], "ask": ["Bash"]},
    {"allow": ["Bash"], "deny": ["*"]},
    {"allow": ["Bash"], "deny": ["Bash(*)"]},
])
def test_no_broad_bash_claim(tmp_path, permissions):
    project = tmp_path / "project"
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": permissions}))
    result = collect_from_configs([settings], workspace=project)
    assert not any(cap.name == "Bash" for cap in result.capabilities)


def test_discover_merges_bash_allow_without_changing_read_only_demo(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    (project / ".claude").mkdir(parents=True)
    (project / "CLAUDE.md").write_text("Benign project instructions")
    (project / "README.md").write_text("Benign project description")
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    monkeypatch.setattr("agenthound.discovery.config_parser._DISCOVERY_CANDIDATES", [])
    monkeypatch.setattr("agenthound.cli.check_internet", lambda: False)
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
    shared = project / ".claude" / "settings.json"
    shared.write_text("{}")
    output = tmp_path / "graph.json"
    runner = CliRunner()
    result = runner.invoke(cli, ["discover", "-w", str(project), "--scope", "workspace", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert not any(node["properties"]["name"] == "BASH" for node in json.loads(output.read_text())["graph"]["nodes"])

    shared.write_text(json.dumps({"permissions": {"allow": ["Bash"]}}))
    result = runner.invoke(cli, ["discover", "-w", str(project), "--scope", "workspace", "-o", str(output)])
    assert result.exit_code == 0, result.output
    graph = json.loads(output.read_text())["graph"]
    assert {node["properties"]["name"] for node in graph["nodes"]} >= {"READ", "BASH"}
    assert any(node["properties"].get("impact_kind") == "SystemTakeover" for node in graph["nodes"])
    assert all(edge["properties"].get("confidence") == "suspected" for edge in graph["edges"] if edge["kind"] == "Triggers")

    readme = next(node for node in graph["nodes"] if node["properties"]["name"] == "README.MD")
    agent = next(node for node in graph["nodes"] if node["properties"]["name"] == "CLAUDE-CODE")
    bash = next(node for node in graph["nodes"] if node["properties"]["name"] == "BASH")
    assert any(edge["kind"] == "Influences" and edge["start"]["value"] == readme["id"] and edge["end"]["value"] == agent["id"] for edge in graph["edges"])
    assert any(edge["kind"] == "HasCapability" and edge["start"]["value"] == agent["id"] and edge["end"]["value"] == bash["id"] for edge in graph["edges"])
