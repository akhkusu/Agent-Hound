from agenthound.collectors.sources import collect_sources
from agenthound.models.nodes import Agent


def test_collect_sources_finds_agent_instructions(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# instructions")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    assert len(result.sources) >= 1
    kinds = {s.source_kind for s in result.sources}
    assert "AgentInstruction" in kinds


def test_collect_sources_creates_influences_edges(tmp_path):
    (tmp_path / "README.md").write_text("# readme")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edges = [e for e in result.edges if e.kind == "Influences"]
    assert len(edges) == len(result.sources)
    for edge in edges:
        assert edge.end == agent.objectid


def test_collect_sources_edge_starts_from_source(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x")
    agent = Agent(name="x", platform="X", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    source_ids = {s.objectid for s in result.sources}
    for edge in result.edges:
        assert edge.start in source_ids


def test_collect_sources_empty_workspace(tmp_path):
    agent = Agent(name="x", platform="X", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    assert result.sources == []
    assert result.edges == []


def test_cursorrules_does_not_influence_claude(tmp_path):
    (tmp_path / ".cursorrules").write_text("rules")
    agent = Agent(name="claude-desktop", platform="Claude Desktop", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    # The source node exists, but there is no Influences edge to Claude.
    assert any(s.name == ".cursorrules" for s in result.sources)
    assert [e for e in result.edges if e.kind == "Influences"] == []


def test_cursorrules_influences_cursor_confirmed(tmp_path):
    (tmp_path / ".cursorrules").write_text("rules")
    agent = Agent(name="cursor", platform="Cursor", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "confirmed"


def test_docfile_influence_is_suspected(tmp_path):
    (tmp_path / "README.md").write_text("# readme")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "suspected"


def test_unmapped_instruction_is_suspected(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x")
    from agenthound.models.nodes import Agent
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "suspected"


def test_vscode_standard_copilot_instruction_is_confirmed(tmp_path):
    github = tmp_path / ".github"
    github.mkdir()
    (github / "copilot-instructions.md").write_text("rules")
    agent = Agent(name="vscode-copilot", platform="VS Code Copilot", config_path="/p")
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "confirmed"
    assert ".github/copilot-instructions.md" in edge.properties["evidence"]


def test_nested_claude_instruction_is_only_suspected(tmp_path):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "CLAUDE.md").write_text("rules")
    agent = Agent(name="claude-code", platform="Claude Code", config_path="/p")
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "suspected"


def test_root_claude_instruction_is_confirmed_for_claude_code(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("rules")
    agent = Agent(name="claude-code", platform="Claude Code", config_path="/p")
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "confirmed"


def test_claude_desktop_does_not_confirm_claude_code_instruction(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("rules")
    agent = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/p")
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "suspected"
