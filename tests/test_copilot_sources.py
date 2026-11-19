import json
from pathlib import Path
import subprocess

import pytest

from agenthound.discovery.behavior_files import DiscoveryContext
from agenthound.discovery import copilot_sources


@pytest.fixture
def context(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "project"
    home.mkdir()
    workspace.mkdir()
    (workspace / ".git").mkdir()
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    return DiscoveryContext(workspace, home, "all")


def write(root: Path, relative: str, value: str = "PRIVATE-INSTRUCTION-BODY") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def found(context, agent="vscode-copilot"):
    return {item.path.resolve(): item for item in copilot_sources.discover(context, agent)}


def test_documented_project_categories_and_exact_locations(context):
    paths = {
        ".github/copilot-instructions.md": "persistent_instruction",
        ".github/instructions/python.instructions.md": "path_scoped_instruction",
        ".github/prompts/review.prompt.md": "reusable_prompt",
        ".github/agents/review.agent.md": "agent_definition",
        ".github/skills/review/SKILL.md": "skill",
        ".claude/skills/release/SKILL.md": "skill",
        ".agents/skills/test/SKILL.md": "skill",
        ".github/copilot/settings.json": "runtime_configuration",
        ".github/copilot/settings.local.json": "runtime_configuration",
        ".github/mcp.json": "runtime_configuration",
        ".vscode/mcp.json": "runtime_configuration",
    }
    for path in paths:
        write(context.workspace, path, "{}" if path.endswith(".json") else "PRIVATE-INSTRUCTION-BODY")
    for path in ("custom/review.agent.md", "instructions/wrong.instructions.md", ".github/prompts/wrong.md", ".github/agents/wrong.txt"):
        write(context.workspace, path)
    items = found(context)
    assert {str(path.relative_to(context.workspace)): item.rule.influence_channel for path, item in items.items()} == paths
    assert copilot_sources.discover(context, "codex") == []
    assert "PRIVATE-INSTRUCTION-BODY" not in repr(items)


def test_personal_instruction_and_config_scope_is_respected(context):
    user = context.home / ".copilot"
    personal = write(user, "copilot-instructions.md")
    modular = write(user, "instructions/nested/style.instructions.md", '---\napplyTo: "**/*.py"\n---\nPRIVATE-INSTRUCTION-BODY')
    skill = write(user, "skills/review/SKILL.md")
    shared = write(context.home, ".agents/skills/test/SKILL.md")
    agent = write(user, "agents/expert.agent.md")
    settings = write(user, "settings.json", '{ /* comment */ "token": "PRIVATE-TOKEN", }')
    provider = write(user, "providers.json", '{"apiKey":"PRIVATE-PROVIDER-TOKEN"}')
    lsp = write(user, "lsp-config.json", '{"args":["PRIVATE-ARGUMENT"]}')
    project = write(context.workspace, ".github/copilot-instructions.md")
    glob = DiscoveryContext(context.workspace, context.home, "global")
    items = found(glob, "copilot-cli")
    assert set(items) == {path.resolve() for path in (personal, modular, skill, shared, agent, settings, provider, lsp)}
    assert all(item.rule.scope == "user" for item in items.values())
    assert items[modular].target_pattern == "**/*.py"
    assert items[personal].rule.confidence == "confirmed"
    assert provider not in found(glob, "vscode-copilot")
    workspace = DiscoveryContext(context.workspace, context.home, "workspace")
    assert set(found(workspace)) == {project}
    for marker in ("PRIVATE-TOKEN", "PRIVATE-PROVIDER-TOKEN", "PRIVATE-ARGUMENT", "PRIVATE-INSTRUCTION-BODY"):
        assert marker not in repr(items)


def test_copilot_home_override_does_not_scan_default_or_sessions(context, monkeypatch):
    custom = context.home / "custom-copilot"
    selected = write(custom, "copilot-instructions.md")
    write(context.home, ".copilot/copilot-instructions.md")
    write(custom, "session-state/unrelated/AGENTS.md")
    monkeypatch.setenv("COPILOT_HOME", str(custom))
    assert set(found(context)) == {selected}


def test_active_instruction_hierarchy_combines_ancestors_without_siblings(context):
    root = write(context.workspace, "AGENTS.md")
    work = context.workspace / "work"
    work.mkdir()
    local = write(work, "AGENTS.md")
    child = write(work, "child/AGENTS.md")
    compatibility = write(context.workspace, ".claude/CLAUDE.md")
    write(context.workspace, "sibling/AGENTS.md")
    selected = DiscoveryContext(work, context.home, "workspace")
    items = found(selected)
    assert set(items) == {root, local, child, compatibility}
    assert items[root].confidence == items[local].confidence == "confirmed"
    assert items[compatibility].confidence == "confirmed"
    assert items[child].rule.activation == "conditional"
    assert items[child].rule.confidence == "suspected"


def test_relative_instruction_imports_are_bounded_cycle_safe_and_contained(context):
    instructions = write(context.workspace, "AGENTS.md", "@context/first.txt\n@/outside.txt\n@~/private.md\n```\n@ignored.txt\n```\n")
    first = write(context.workspace, "context/first.txt", "@second.txt\nPRIVATE-INSTRUCTION-BODY")
    second = write(context.workspace, "context/second.txt", "@first.txt\n")
    write(context.workspace, "ignored.txt")
    outside = write(context.home, "outside.txt")
    (context.workspace / "context" / "escape.txt").symlink_to(outside)
    first.write_text("@second.txt\n@escape.txt\nPRIVATE-INSTRUCTION-BODY")
    items = found(context)
    assert set(items) == {instructions, first, second}
    assert items[first].rule.influence_channel == "persistent_instruction"
    assert items[first].confidence == "confirmed"
    assert "PRIVATE-INSTRUCTION-BODY" not in repr(items)


def test_hook_definitions_and_scripts_are_discovered_without_execution(context, monkeypatch):
    script = write(context.workspace, "scripts/check.py", "raise RuntimeError('MUST-NOT-EXECUTE')")
    hooks = write(context.workspace, ".github/hooks/check.json", json.dumps({
        "version": 1, "hooks": {"preToolUse": [{"type": "command", "command": "python scripts/check.py PRIVATE-TOKEN"}]}
    }))
    inline = write(context.workspace, ".github/copilot/settings.json", json.dumps({
        "hooks": {"postToolUse": [{"type": "command", "cwd": "scripts", "command": "python check.py"}]},
        "env": {"TOKEN": "PRIVATE-TOKEN"},
    }))

    def forbidden(*args, **kwargs):
        pytest.fail("Discovery must not execute a hook")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    items = found(context)
    assert set(items) == {script, hooks, inline}
    assert items[script].rule.influence_channel == "hook_execution"
    assert items[script].rule.confidence == "suspected"
    assert "PRIVATE-TOKEN" not in repr(items)
    assert "MUST-NOT-EXECUTE" not in repr(items)


def test_disabled_hooks_do_not_collect_referenced_scripts(context):
    script = write(context.workspace, "scripts/check.sh")
    hooks = write(context.workspace, ".github/hooks/check.json", json.dumps({
        "disableAllHooks": True, "hooks": {"preToolUse": [{"bash": "scripts/check.sh"}]}
    }))
    items = found(context)
    assert set(items) == {hooks}
    assert script not in items
    hooks.write_text(json.dumps({"hooks": {"preToolUse": [{"bash": "scripts/check.sh"}]}}))
    settings = write(context.workspace, ".github/copilot/settings.local.json", '{"disableAllHooks":true}')
    assert set(found(context)) == {hooks, settings}


@pytest.mark.parametrize("value", ['{malformed "SECRET"', " " * (64 * 1024) + '{"hooks":{}}'])
def test_malformed_and_oversized_configs_do_not_expand_references(context, value):
    path = write(context.workspace, ".github/copilot/settings.json", value)
    assert set(found(context)) == {path}
    assert "SECRET" not in repr(found(context))


def test_symlink_escape_is_not_discovered(context):
    outside = write(context.home, "outside.md")
    github = context.workspace / ".github"
    github.mkdir()
    (github / "copilot-instructions.md").symlink_to(outside)
    skills = context.workspace / ".agents" / "skills"
    skills.parent.mkdir()
    skills.symlink_to(context.home, target_is_directory=True)
    assert found(context) == {}


def test_plugins_require_enablement_and_manifest_declarations(context):
    root = context.workspace / "plugins" / "enabled"
    manifest = write(root, "plugin.json", json.dumps({"name": "enabled", "agents": "roles"}))
    agent = write(root, "roles/review.agent.md")
    skill = write(root, "skills/review/SKILL.md")
    hooks = write(root, "hooks.json", json.dumps({"hooks": {"preToolUse": [{"command": 'python "${PLUGIN_ROOT}/scripts/check.py" PRIVATE-TOKEN'}]}}))
    script = write(root, "scripts/check.py", "PRIVATE-SCRIPT-BODY")
    write(context.workspace, "plugins/disabled/plugin.json", '{"name":"disabled"}')
    write(context.workspace, "plugins/disabled/skills/no/SKILL.md")
    write(root, "unrelated/secret.md")
    settings = write(context.workspace, ".github/copilot/settings.json", '{"enabledPlugins":{"./plugins/enabled":true,"./plugins/disabled":false}}')
    items = found(context)
    assert set(items) == {manifest, agent, skill, hooks, script, settings}
    assert items[manifest].rule.source_kind == "PluginManifest"
    for marker in ("PRIVATE-TOKEN", "PRIVATE-SCRIPT-BODY", "PRIVATE-INSTRUCTION-BODY"):
        assert marker not in repr(items)


def test_standard_plugins_use_namespaced_components_and_reject_unknown_schema(context):
    root = context.workspace / "plugin"
    manifest = write(root, "plugin.json", json.dumps({
        "name": "test", "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    }))
    agent = write(root, "com.github.copilot/agents/review.agent.md")
    skill = write(root, "skills/check/SKILL.md")
    mcp = write(root, "mcp.json", '{"token":"PRIVATE-TOKEN"}')
    write(root, "agents/ignored.agent.md")
    settings = write(context.workspace, ".github/copilot/settings.json", '{"enabledPlugins":{"./plugin":true}}')
    assert set(found(context)) == {manifest, agent, skill, mcp, settings}
    manifest.write_text('{"name":"test","$schema":"https://agent-plugins.org/schemas/999/plugin.schema.json"}')
    assert set(found(context)) == {settings}


def test_installed_plugin_files_require_opt_in(context):
    root = context.home / ".copilot" / "installed-plugins" / "market" / "review"
    manifest = write(root, "plugin.json", '{"name":"review"}')
    skill = write(root, "skills/review/SKILL.md")
    settings = write(context.home, ".copilot/settings.json", '{"enabledPlugins":{"review@market":true}}')
    assert set(found(context)) == {settings}
    enabled = DiscoveryContext(context.workspace, context.home, "all", include_plugin_cache=True)
    assert set(found(enabled)) == {settings, manifest, skill}


def test_local_plugin_override_disables_repository_default(context):
    write(context.workspace, "plugin/plugin.json", '{"name":"review"}')
    shared = write(context.workspace, ".github/copilot/settings.json", '{"enabledPlugins":{"./plugin":true}}')
    local = write(context.workspace, ".github/copilot/settings.local.json", '{"enabledPlugins":{"./plugin":false}}')
    assert set(found(context)) == {shared, local}


def test_vscode_registered_local_plugins_are_enabled_explicitly(context):
    manifest = write(context.workspace, "plugin/plugin.json", '{"name":"review"}')
    settings = write(context.workspace, ".vscode/settings.json", '{"chat.pluginLocations":{"./plugin":true}}')
    assert set(found(context)) == {settings, manifest}
    assert found(context, "copilot-cli") == {}


def test_malformed_frontmatter_does_not_export_body_as_metadata(context):
    path = write(context.workspace, ".github/instructions/style.instructions.md", '---\napplyTo: "**/*.py"\nPRIVATE-BODY-WITHOUT-CLOSING-HEADER')
    item = found(context)[path]
    assert item.target_pattern == ""
    assert item.discovery_status == "metadata_unavailable"
    assert "PRIVATE-BODY" not in repr(item)


def test_declared_vscode_customization_locations_are_scoped_and_targeted(context):
    instruction = write(context.workspace, "custom-instructions/style.instructions.md", '---\napplyTo: "**/*.py"\n---\nPRIVATE-BODY')
    agent = write(context.workspace, "custom-agents/review.agent.md")
    skill = write(context.workspace, "custom-skills/review/SKILL.md")
    write(context.workspace, "disabled/ignored.agent.md")
    external = context.home / "outside"
    write(external, "private.agent.md")
    settings = write(context.workspace, ".vscode/settings.json", json.dumps({
        "chat.instructionsFilesLocations": {"custom-instructions": True},
        "chat.agentFilesLocations": {"custom-agents": True, "disabled": False, str(external): True},
        "chat.agentSkillsLocations": {"custom-skills": True},
    }))
    items = found(context)
    assert set(items) == {instruction, agent, skill, settings}
    assert items[instruction].target_pattern == "**/*.py"
    assert found(context, "copilot-cli") == {}


def test_explicit_vscode_plugin_disable_prevents_components(context):
    write(context.workspace, "plugin/plugin.json", '{"name":"review"}')
    settings = write(context.workspace, ".vscode/settings.json", '{"chat.plugins.enabled":false,"chat.pluginLocations":{"./plugin":true}}')
    assert set(found(context)) == {settings}


def test_plugin_manifest_precedence_and_invalid_schema_types(context):
    root = context.workspace / "plugin"
    root_manifest = write(root, "plugin.json", '{"name":"root","agents":"ignored"}')
    preferred = write(root, ".plugin/plugin.json", '{"name":"preferred","agents":"selected"}')
    agent = write(root, "selected/review.agent.md")
    write(root, "ignored/wrong.agent.md")
    settings = write(context.workspace, ".github/copilot/settings.json", '{"enabledPlugins":{"./plugin":true}}')
    assert set(found(context)) == {preferred, agent, settings}
    root_manifest.write_text('{"name":"root","$schema":{}}')
    assert set(found(context)) == {settings}


def test_agent_scoped_literal_hook_references_only_parse_header(context):
    script = write(context.workspace, "scripts/format.sh", "PRIVATE-SCRIPT-BODY")
    ignored = write(context.workspace, "scripts/ignored.sh")
    agent = write(context.workspace, ".github/agents/review.agent.md", """---
name: reviewer
hooks:
  PostToolUse:
    - type: command
      command: ./scripts/format.sh
description: PRIVATE-DESCRIPTION
---
hooks:
  command: ./scripts/ignored.sh
PRIVATE-INSTRUCTION-BODY
""")
    items = found(context)
    assert set(items) == {agent, script}
    assert ignored not in items
    assert items[script].rule.confidence == "suspected"
    for marker in ("PRIVATE-SCRIPT-BODY", "PRIVATE-DESCRIPTION", "PRIVATE-INSTRUCTION-BODY"):
        assert marker not in repr(items)


def test_subdirectory_instruction_imports_can_reference_selected_repository(context):
    work = context.workspace / "sub"
    instruction = write(work, "AGENTS.md", "@../shared/context.txt")
    imported = write(context.workspace, "shared/context.txt")
    selected = DiscoveryContext(work, context.home, "workspace")
    assert set(found(selected)) == {instruction, imported}
