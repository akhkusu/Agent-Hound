"""Real CLI/OpenGraph checks with isolated, synthetic agent environments."""

import json
from pathlib import Path
import re

from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.collectors.sources import collect_sources_for_agents
from agenthound.models.nodes import Agent


def populate_workspace(root: Path) -> tuple[Path, Path]:
    project, home = root / "project", root / "home"

    def write(relative: str, text: str, base: Path = project) -> Path:
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    (project / ".git").mkdir(parents=True)
    body = "NEVER_EXPORT_INSTRUCTION_BODY"
    write("README.md", body)
    write("CLAUDE.md", body)
    write("CLAUDE.local.md", body)
    write("AGENTS.md", body)
    write("src/AGENTS.override.md", body)
    write("lib/TEAM.md", body)
    write(".claude/rules/tests.md", "---\npaths:\n  - src/**\n---\n" + body)
    write(".claude/commands/release.md", body)
    write(".claude/agents/reviewer.md", body)
    skill = "---\nname: safe-review\ndescription: Review a change.\n---\nNEVER_EXPORT_SKILL_BODY"
    write(".claude/skills/review/SKILL.md", skill)
    write(".agents/skills/common/SKILL.md", skill)
    write("scripts/check.py", "raise RuntimeError('NEVER_EXPORT_SCRIPT_BODY')")
    hooks = {"Stop": [{"hooks": [{"type": "command", "command": "python scripts/check.py --token NEVER_EXPORT_HOOK_ARGUMENT"}]}]}
    write(".claude/settings.json", json.dumps({"hooks": hooks, "env": {"TOKEN": "NEVER_EXPORT_ENV"}}))
    write(".codex/config.toml", 'project_doc_fallback_filenames = ["TEAM.md"]\n[agents.review]\nconfig_file = "roles/review.toml"\n')
    write(".codex/roles/review.toml", 'developer_instructions = "NEVER_EXPORT_ROLE_INSTRUCTIONS"\n')
    write(".cursor/mcp.json", '{"mcpServers":{"remote":{"url":"https://example.invalid/mcp?token=NEVER_EXPORT_TOKEN","headers":{"Authorization":"NEVER_EXPORT_HEADER"}}}}')
    write(".cursor/rules/security.mdc", "---\nalwaysApply: true\n---\n" + body)
    write(".cursorignore", "NEVER_EXPORT_IGNORE_BODY")
    write(".vscode/mcp.json", "{}")
    write(".github/copilot/settings.json", "{}")
    write(".github/copilot-instructions.md", body)
    write(".github/instructions/python.instructions.md", "---\napplyTo: '**/*.py'\n---\n" + body)
    write(".github/prompts/review.prompt.md", body)
    write(".github/agents/review.agent.md", body)
    write(".github/hooks/check.json", json.dumps({"hooks": {"sessionStart": [{"type": "command", "command": "python scripts/check.py --token NEVER_EXPORT_HOOK_ARGUMENT"}]}}))
    write(".copilot/settings.json", "{}", home)
    write(".copilot/copilot-instructions.md", body, home)
    key = re.sub(r"[^a-zA-Z0-9]", "-", str(project))
    write(f".claude/projects/{key}/memory/MEMORY.md", "NEVER_EXPORT_MEMORY_BODY", home)
    write(f".claude/projects/{key}/memory/topic.md", "NEVER_EXPORT_MEMORY_TOPIC", home)
    write(".claude/projects/unrelated/memory/MEMORY.md", "NEVER_EXPORT_UNRELATED_MEMORY", home)
    write(".claude/skills/toolkit/.claude-plugin/plugin.json", '{"name":"toolkit"}')
    write(".claude/skills/toolkit/commands/deploy.md", "NEVER_EXPORT_PLUGIN_BODY")
    return project, home


def isolate_environment(home: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("agenthound.discovery.skills._user_home", lambda: home)
    monkeypatch.setattr("agenthound.discovery.config_parser._DISCOVERY_CANDIDATES", [])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setattr("agenthound.cli.check_internet", lambda: False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_PROJECT_DIR_NAME", raising=False)


def test_real_scan_outputs_safe_semantic_graph_and_verbose_dimensions(tmp_path, monkeypatch):
    project, home = populate_workspace(tmp_path)
    isolate_environment(home, monkeypatch)
    output = tmp_path / "graph.json"
    result = CliRunner().invoke(cli, ["scan", "-w", str(project), "--scope", "all", "-o", str(output), "-v"])
    assert result.exit_code == 0, result.output
    text = output.read_text(encoding="utf-8")
    assert "NEVER_EXPORT" not in text
    assert "NEVER_EXPORT" not in result.output
    graph = json.loads(text)["graph"]
    ids = {node["id"] for node in graph["nodes"]}
    assert len(ids) == len(graph["nodes"])
    assert all(edge["start"]["value"] in ids and edge["end"]["value"] in ids for edge in graph["edges"])
    sources = {node["id"]: node["properties"] for node in graph["nodes"] if "Source" in node["kinds"]}
    kinds = {node["source_kind"] for node in sources.values()}
    assert {"DocFile", "AgentInstruction", "Skill", "Memory", "RuntimeConfiguration", "HookScript", "PluginManifest", "ContextVisibility"} <= kinds
    assert all(source["content_exported"] is False for source in sources.values())
    role = next(source for source in sources.values() if source["path"].endswith(".codex/roles/review.toml"))
    assert role["displayname"] == "Codex Agent: review"
    assert not any("unrelated" in source["path"] for source in sources.values())
    influences = [edge for edge in graph["edges"] if edge["kind"] == "Influences"]
    for edge in influences:
        properties = edge["properties"]
        assert properties["confidence"] in {"confirmed", "suspected"}
        assert properties["activation"]
        assert properties["influence_channel"]
        assert properties["evidence"]
        if properties["activation"] in {"on_demand", "conditional"}:
            assert properties["confidence"] == "suspected"
    for dimension in ("Platform", "Source kind", "Scope", "Activation"):
        assert dimension in result.output


def test_shared_source_is_one_node_with_individual_agent_edges(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("private instruction body")
    (tmp_path / "README.md").write_text("private document body")
    monkeypatch.setattr("agenthound.discovery.skills._user_home", lambda: tmp_path / "home")
    agents = [Agent(name=name, platform=name, config_path=str(tmp_path)) for name in ("codex", "vscode-copilot", "cursor")]
    result = collect_sources_for_agents(agents, tmp_path, "workspace")
    instructions = [source for source in result.sources if Path(source.path).name == "AGENTS.md"]
    assert len(instructions) == 1
    edges = [edge for edge in result.edges if edge.start == instructions[0].objectid]
    assert {edge.end for edge in edges} == {agent.objectid for agent in agents}
    assert len(edges) == 3
