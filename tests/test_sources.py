from pathlib import Path

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
    (tmp_path / "notes.md").write_text("# notes")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edges = [e for e in result.edges if e.kind == "Influences"]
    assert len(edges) == len(result.sources)
    for edge in edges:
        assert edge.end == agent.objectid


def test_readme_is_not_a_source(tmp_path):
    (tmp_path / "README.md").write_text("# readme")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    assert result.sources == []
    assert result.edges == []


def test_skill_only_influences_matching_agent(tmp_path):
    skill = tmp_path / ".agents" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: review\n---\n")

    codex = Agent(name="codex", platform="Codex", config_path=str(tmp_path))
    claude = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    codex_result = collect_sources(agent=codex, workspace=tmp_path, scope="workspace")
    claude_result = collect_sources(agent=claude, workspace=tmp_path, scope="workspace")

    assert codex_result.sources[0].source_kind == "Skill"
    assert [edge.kind for edge in codex_result.edges] == ["Influences"]
    assert claude_result.edges == []


def test_claude_rule_prompt_and_agent_definition_influence_claude(tmp_path):
    for relative in (
        ".claude/rules/python.md",
        ".claude/commands/release.md",
        ".claude/agents/reviewer.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("instructions")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    assert {source.source_kind for source in result.sources} == {
        "AgentInstruction", "PromptFile", "AgentDefinition"
    }
    assert len(result.edges) == 3
    assert {edge.kind for edge in result.edges} == {"Influences"}
    assert {source.name for source in result.sources} == {
        "Claude Rule: python",
        "Claude Command: release",
        "Claude Agent: reviewer",
    }


def test_structured_sources_have_descriptive_names(tmp_path):
    files = {
        ".agents/skills/security-review/SKILL.md": "Skill: security-review",
        ".github/skills/pr-review/SKILL.md": "Skill: pr-review",
        ".github/instructions/tests.instructions.md": "Copilot Instruction: tests",
        ".cursor/rules/security.mdc": "Cursor Rule: security",
        ".github/copilot-instructions.md": "Copilot Instructions",
        ".github/prompts/review.prompt.md": "Copilot Prompt: review",
        ".github/agents/reviewer.md": "Copilot Agent: reviewer",
    }
    for relative in files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("instructions")

    agent = Agent(name="vscode-copilot", platform="VS Code Copilot", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    names_by_path = {
        str(Path(source.path).relative_to(tmp_path)): source.name
        for source in result.sources
    }
    assert names_by_path == files


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
    (tmp_path / "notes.md").write_text("# notes")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "suspected"


def test_instruction_does_not_influence_unsupported_agent(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x")
    from agenthound.models.nodes import Agent
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    assert result.edges == []


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


def test_claude_desktop_does_not_use_claude_code_instruction(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("rules")
    agent = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/p")
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    assert result.edges == []
