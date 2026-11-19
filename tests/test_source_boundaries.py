"""Regressions for scope, plugin admission, and literal hook references."""

import json
from pathlib import Path

import pytest

from agenthound.collectors.sources import collect_sources
from agenthound.discovery.behavior_files import local_command_files
from agenthound.models.nodes import Agent
from agenthound.output.opengraph import build_opengraph


@pytest.fixture
def environment(tmp_path, monkeypatch):
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    (project / ".git").mkdir()
    monkeypatch.setattr("agenthound.discovery.skills._user_home", lambda: home)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setattr("agenthound.discovery.codex_sources._SYSTEM_ROOT", tmp_path / "system-codex")
    monkeypatch.setattr("agenthound.discovery.claude_sources._managed_root", lambda: tmp_path / "managed-claude")
    return project, home


def write(root: Path, relative: str, value: str = "PRIVATE-INSTRUCTION-BODY") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def collect(project: Path, name: str, scope: str = "workspace"):
    agent = Agent(name=name, platform=name, config_path=str(project / "agent-config"))
    return collect_sources(agent, project, scope)


def paths(result):
    return {Path(source.path).resolve(): source for source in result.sources}


def test_disabled_plugin_components_do_not_become_sources_through_static_patterns(environment):
    project, _ = environment
    write(project, ".github/copilot/settings.json", '{"enabledPlugins":{"./plugin":false}}')
    write(project, "plugin/plugin.json", '{"name":"disabled"}')
    write(project, "plugin/AGENTS.md")
    write(project, "plugin/.github/copilot-instructions.md")
    write(project, "plugin/.github/skills/private/SKILL.md", "---\nname: private\n---\nPRIVATE-SKILL-BODY")
    result = collect(project, "vscode-copilot")
    assert not any(Path(source.path).is_relative_to(project / "plugin") for source in result.sources)
    exported = json.dumps(build_opengraph(result))
    assert "PRIVATE-INSTRUCTION-BODY" not in exported
    assert "PRIVATE-SKILL-BODY" not in exported


def test_disabled_plugin_instructions_cannot_expand_imports_outside_plugin(environment):
    project, _ = environment
    write(project, ".github/copilot/settings.json", '{"enabledPlugins":{"./plugin":false}}')
    write(project, "plugin/plugin.json", '{"name":"disabled"}')
    write(project, "plugin/AGENTS.md", "@../private/shared-private.txt")
    # Ordinary root documents remain valid sources; place the target outside
    # general document scope so only a disabled instruction could discover it.
    imported = write(project, "private/shared-private.txt", "PRIVATE-IMPORTED-BODY")
    result = collect(project, "vscode-copilot")
    assert imported not in paths(result)


def test_explicit_import_can_use_a_file_inside_an_unselected_plugin(environment):
    project, _ = environment
    root = write(project, "AGENTS.md", "@plugin/shared/context.txt")
    write(project, "plugin/plugin.json", '{"name":"unselected"}')
    imported = write(project, "plugin/shared/context.txt", "PRIVATE-IMPORTED-BODY")
    result = collect(project, "vscode-copilot")
    discovered = paths(result)
    assert root in discovered
    assert imported in discovered
    assert any(edge.start == discovered[imported].objectid for edge in result.edges)
    assert "PRIVATE-IMPORTED-BODY" not in json.dumps(build_opengraph(result))


def test_project_skill_named_system_keeps_project_scope_on_node_and_edge(environment):
    project, _ = environment
    skill = write(project, ".agents/skills/.system/example/SKILL.md", "---\nname: example\ndescription: Safe metadata\n---\nPRIVATE-SKILL-BODY")
    result = collect(project, "codex")
    source = paths(result)[skill]
    assert source.source_scope == "project"
    assert source.is_external is False
    edge = next(edge for edge in result.edges if edge.start == source.objectid)
    assert edge.properties["source_scope"] == "project"
    assert "PRIVATE-SKILL-BODY" not in json.dumps(build_opengraph(result))


def test_actual_codex_builtin_skill_keeps_system_scope(environment):
    project, home = environment
    skill = write(home, ".codex/skills/.system/creator/SKILL.md", "---\nname: creator\n---\nPRIVATE-SKILL-BODY")
    result = collect(project, "codex", "global")
    source = paths(result)[skill]
    assert source.source_scope == "system"
    assert source.is_external is True
    edge = next(edge for edge in result.edges if edge.start == source.objectid)
    assert edge.properties["source_scope"] == "system"


def test_project_config_reference_to_user_file_does_not_enter_workspace_scope(environment):
    project, home = environment
    role = write(home, ".codex/agents/reviewer.toml", 'model = "PRIVATE-MODEL-VALUE"')
    config = write(project, ".codex/config.toml", f'[agents.reviewer]\nconfig_file = "{role}"\n')
    workspace = collect(project, "codex", "workspace")
    assert config in paths(workspace)
    assert role not in paths(workspace)
    combined = collect(project, "codex", "all")
    source = paths(combined)[role]
    assert source.source_scope == "user"
    assert source.is_external is True
    assert "PRIVATE-MODEL-VALUE" not in json.dumps(build_opengraph(combined))


def test_user_config_reference_to_project_file_does_not_enter_global_scope(environment):
    project, home = environment
    role = write(project, "roles/reviewer.toml", 'model = "PRIVATE-MODEL-VALUE"')
    config = write(home, ".codex/config.toml", f'[agents.reviewer]\nconfig_file = "{role}"\n')
    global_result = collect(project, "codex", "global")
    assert config in paths(global_result)
    assert role not in paths(global_result)
    combined = collect(project, "codex", "all")
    source = paths(combined)[role]
    assert source.source_scope == "project"
    assert source.is_external is False


def test_system_config_reference_to_project_file_does_not_enter_global_scope(environment):
    project, home = environment
    role = write(project, "roles/reviewer.toml", 'model = "PRIVATE-MODEL-VALUE"')
    config = write(home.parent, "system-codex/config.toml", f'[agents.reviewer]\nconfig_file = "{role}"\n')
    global_result = collect(project, "codex", "global")
    assert config in paths(global_result)
    assert role not in paths(global_result)
    combined = collect(project, "codex", "all")
    source = paths(combined)[role]
    assert source.source_scope == "project"
    assert source.is_external is False


@pytest.mark.parametrize("command", ["python -m module", "python -c module", "node --require module main.js", "python --unknown-option module"])
def test_interpreter_option_values_are_not_guessed_to_be_script_paths(environment, command):
    project, _ = environment
    write(project, "module", "PRIVATE-OPTION-OPERAND")
    write(project, "main.js", "PRIVATE-SCRIPT-BODY")
    assert local_command_files(command, project / "anchor.json", [project]) == []


def test_only_literal_script_operand_is_collected_not_secret_arguments(environment):
    project, _ = environment
    script = write(project, "scripts/hook.py", "PRIVATE-SCRIPT-BODY")
    write(project, "PRIVATE-ARGUMENT", "PRIVATE-ARGUMENT-BODY")
    assert local_command_files("python scripts/hook.py PRIVATE-ARGUMENT", project / "anchor.json", [project]) == [script]
