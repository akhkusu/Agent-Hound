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


def test_general_documents_are_excluded_by_default(tmp_path):
    (tmp_path / "notes.md").write_text("# notes")
    agent = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    assert result.sources == []
    assert result.edges == []


def test_readme_is_not_a_source_without_agent_configuration(tmp_path):
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


def test_project_skill_exports_frontmatter_metadata_without_body(tmp_path):
    skill = tmp_path / ".agents" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: secure-review\ndescription: Review code for security issues.\n---\n"
        "SECRET-INSTRUCTION-BODY\n"
    )

    agent = Agent(name="codex", platform="Codex", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")

    source = next(source for source in result.sources if source.source_kind == "Skill")
    exported = source.to_opengraph()["properties"]
    assert source.name == "Skill: secure-review"
    assert exported["description"] == "Review code for security issues."
    assert exported["source_scope"] == "project"
    assert "SECRET-INSTRUCTION-BODY" not in str(exported)
    edge = next(edge for edge in result.edges if edge.start == source.objectid)
    assert edge.properties["confidence"] == "suspected"
    assert "invocation unverified" in edge.properties["evidence"]


def test_codex_all_scope_collects_user_and_system_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    user_skill = home / ".agents" / "skills" / "personal" / "SKILL.md"
    system_skill = codex_home / "skills" / ".system" / "creator" / "SKILL.md"
    for path, name in ((user_skill, "personal"), (system_skill, "creator")):
        path.parent.mkdir(parents=True)
        path.write_text(f"---\nname: {name}\ndescription: {name} skill\n---\n")
    monkeypatch.setattr("agenthound.discovery.skills._user_home", lambda: home)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    agent = Agent(name="codex", platform="Codex", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="all")

    skills = {source.name: source for source in result.sources if source.source_kind == "Skill"}
    assert skills["Skill: personal"].source_scope == "user"
    assert skills["Skill: creator"].source_scope == "system"
    assert all(source.is_external for source in skills.values())
    assert len(result.edges) == 2


def test_claude_user_skills_only_influence_claude(tmp_path, monkeypatch):
    home = tmp_path / "home"
    claude_home = home / "custom-claude"
    skill = claude_home / "skills" / "release" / "skill.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: release\ndescription: Prepare releases\n---\n")
    monkeypatch.setattr("agenthound.discovery.skills._user_home", lambda: home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))

    claude = Agent(name="claude-code", platform="Claude Code", config_path=str(tmp_path))
    codex = Agent(name="codex", platform="Codex", config_path=str(tmp_path))
    claude_result = collect_sources(claude, tmp_path, "global")
    codex_result = collect_sources(codex, tmp_path, "global")

    assert [source.name for source in claude_result.sources] == ["Skill: release"]
    assert len(claude_result.edges) == 1
    assert codex_result.sources == []


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
    expected = {
        path: name for path, name in files.items()
        if not path.startswith(".cursor/")
    }
    assert names_by_path == expected


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
    assert result.sources == []
    assert [e for e in result.edges if e.kind == "Influences"] == []


def test_cursorrules_influences_cursor_confirmed(tmp_path):
    (tmp_path / ".cursorrules").write_text("rules")
    agent = Agent(name="cursor", platform="Cursor", config_path=str(tmp_path))
    result = collect_sources(agent=agent, workspace=tmp_path, scope="workspace")
    edge = next(e for e in result.edges if e.kind == "Influences")
    assert edge.properties["confidence"] == "confirmed"


def test_instruction_does_not_influence_unsupported_agent(tmp_path):
    (tmp_path / "AGENTS.md").write_text("x")
    from agenthound.models.nodes import Agent
    agent = Agent(name="claude-desktop", platform="Claude Desktop", config_path=str(tmp_path))
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
