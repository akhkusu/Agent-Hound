import json
import subprocess

import pytest

from agenthound.discovery import cursor_sources as cursor
from agenthound.discovery.behavior_files import DiscoveryContext


def write(path, text="PRIVATE_BODY"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def files(context):
    return {item.path: item for item in cursor.discover(context, "cursor")}


@pytest.fixture
def layout(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / ".git").mkdir()
    return home, project


def test_rule_activation_and_nested_agent_scope(layout):
    home, project = layout
    always = write(project / ".cursor/rules/always.mdc", "---\nalwaysApply: true\n---\nPRIVATE_BODY")
    conditional = write(project / ".cursor/rules/web.mdc", "---\nglobs: src/**/*.ts\nalwaysApply: false\n---\nPRIVATE_BODY")
    intelligent = write(project / ".cursor/rules/smart.mdc", "---\ndescription: SECRET_DESCRIPTION\n---\nPRIVATE_BODY")
    manual = write(project / ".cursor/rules/manual.mdc", "---\nalwaysApply: false\n---\nPRIVATE_BODY")
    nested = write(project / "pkg/.cursor/rules/always.mdc", "---\nalwaysApply: true\n---\nPRIVATE_BODY")
    root_agents = write(project / "AGENTS.md")
    nested_agents = write(project / "pkg/AGENTS.md")
    wrong = write(project / ".cursor/rules/ignored.md")
    found = files(DiscoveryContext(project, home, "workspace"))
    assert found[always].activation == "automatic"
    assert found[always].confidence == "confirmed"
    assert found[conditional].activation == "conditional"
    assert found[conditional].target_pattern == "src/**/*.ts"
    assert found[intelligent].activation == "conditional"
    assert found[manual].activation == "on_demand"
    assert found[nested].activation == "conditional"
    assert found[root_agents].confidence == "confirmed"
    assert found[nested_agents].rule.activation == "conditional"
    assert wrong not in found
    assert "PRIVATE_BODY" not in repr(found)
    assert "SECRET_DESCRIPTION" not in repr(found)


def test_scopes_runtime_visibility_and_agent_target(layout):
    home, project = layout
    project_config = write(project / ".cursor/mcp.json", '{"mcpServers":{}}')
    user_config = write(home / ".cursor/mcp.json", '{"mcpServers":{}}')
    ignored = write(project / ".cursorignore", "SECRET_VISIBILITY_PATTERN")
    indexing = write(project / ".cursorindexingignore")
    legacy = write(project / ".cursorrules")
    global_files = files(DiscoveryContext(project, home, "global"))
    workspace_files = files(DiscoveryContext(project, home, "workspace"))
    assert user_config in global_files and project_config not in global_files
    assert {project_config, ignored, indexing, legacy} <= workspace_files.keys()
    assert workspace_files[ignored].rule.influence_channel == "context_visibility"
    assert workspace_files[project_config].rule.influence_channel == "runtime_configuration"
    assert "SECRET_VISIBILITY_PATTERN" not in repr(workspace_files)
    assert cursor.discover(DiscoveryContext(project, home, "all"), "codex") == []


def test_skills_and_subagent_precedence_without_body_export(layout):
    home, project = layout
    skills = [write(project / directory / "skills/one/SKILL.md") for directory in (".cursor", ".agents", ".claude", ".codex")]
    nested = write(project / "sub/.cursor/skills/nested/SKILL.md")
    user_skill = write(home / ".cursor/skills/user/SKILL.md")
    preferred = write(project / ".cursor/agents/reviewer.md")
    compatibility = write(project / ".claude/agents/reviewer.md")
    user_agent = write(home / ".cursor/agents/reviewer.md")
    independent = write(home / ".codex/agents/planner.md")
    found = files(DiscoveryContext(project, home, "all"))
    assert {*skills, nested, user_skill, preferred, independent} <= found.keys()
    assert compatibility not in found
    assert user_agent not in found


def test_hook_scripts_and_rule_references_are_static(layout, monkeypatch):
    home, project = layout
    script = write(project / ".cursor/hooks/check.py", "raise RuntimeError('MUST_NOT_EXECUTE')")
    user_script = write(home / ".cursor/hooks/check.py")
    write(project / ".cursor/hooks.json", json.dumps({"hooks": {"afterFileEdit": [
        {"command": "python .cursor/hooks/check.py --token SECRET_ARG"}
    ]}}))
    write(home / ".cursor/hooks.json", '{"hooks":{"stop":[{"command":"python ./hooks/check.py"}]}}')
    reference = write(project / "migration.sql", "SECRET_SQL_BODY")
    write(project / ".cursor/rules/migration.mdc", "---\nalwaysApply: true\n---\nRead @migration.sql on demand.")
    def forbidden(*args, **kwargs):
        pytest.fail("Discovery must not execute scripts")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    found = files(DiscoveryContext(project, home, "all"))
    assert {script, user_script, reference} <= found.keys()
    assert found[script].rule.influence_channel == "hook_execution"
    assert found[reference].rule.activation == "on_demand"
    for secret in ("MUST_NOT_EXECUTE", "SECRET_ARG", "SECRET_SQL_BODY"):
        assert secret not in repr(found)


def test_declared_native_plugin_components_and_explicit_paths_replace_defaults(layout):
    home, project = layout
    plugin = home / ".cursor/plugins/local/team"
    manifest = write(plugin / ".cursor-plugin/plugin.json", json.dumps({
        "name": "team", "rules": "custom-rules", "commands": ["actions/run.txt"],
        "hooks": "custom-hooks.json", "mcpServers": "servers.json",
    }))
    custom_rule = write(plugin / "custom-rules/team.mdc", "---\nalwaysApply: true\n---\nPRIVATE_BODY")
    ignored = write(plugin / "rules/ignored.mdc")
    command = write(plugin / "actions/run.txt")
    skill = write(plugin / "skills/help/SKILL.md")
    agent = write(plugin / "agents/worker.md")
    script = write(plugin / "scripts/check.py")
    write(plugin / "custom-hooks.json", '{"hooks":{"stop":[{"command":"python ${CURSOR_PLUGIN_ROOT}/scripts/check.py --token SECRET_ARG"}]}}')
    mcp = write(plugin / "servers.json", '{"mcpServers":{"remote":{"headers":{"Authorization":"SECRET_TOKEN"}}}}')
    unrelated = write(home / "other/.cursor-plugin/plugin.json", '{"name":"other"}')
    found = files(DiscoveryContext(project, home, "global"))
    assert {manifest, custom_rule, command, skill, agent, script, mcp} <= found.keys()
    assert found[custom_rule].confidence == "suspected"
    assert {ignored, unrelated}.isdisjoint(found)
    assert "SECRET_ARG" not in repr(found)
    assert "SECRET_TOKEN" not in repr(found)


def test_portable_plugin_only_loads_standard_components(layout):
    home, project = layout
    plugin = home / ".cursor/plugins/local/portable"
    manifest = write(plugin / "plugin.json", '{"$schema":"https://agent-plugins.org/schemas/1.0.0/plugin.schema.json","name":"portable"}')
    skill = write(plugin / "skills/help/SKILL.md")
    mcp = write(plugin / "mcp.json", '{}')
    unsupported = write(plugin / "agents/unsupported.md")
    found = files(DiscoveryContext(project, home, "global"))
    assert {manifest, skill, mcp} <= found.keys()
    assert unsupported not in found


def test_malformed_metadata_oversized_and_symlink_escapes(layout):
    home, project = layout
    malformed = write(project / ".cursor/rules/malformed.mdc", "---\nalwaysApply: true\nPRIVATE_BODY")
    oversized = write(project / ".cursor/rules/large.mdc", "SECRET_BODY" * 10000)
    outside = write(home / "outside.mdc")
    (project / ".cursor/rules/outside.mdc").symlink_to(outside)
    fake_user_rules = write(home / ".cursor/rules/user.mdc")
    fake_definition = write(project / "elsewhere/.cursor-plugin/plugin.json", '{"name":"fake"}')
    found = files(DiscoveryContext(project, home, "all"))
    assert found[malformed].activation == found[oversized].activation == "unknown"
    assert {project / ".cursor/rules/outside.mdc", fake_user_rules, fake_definition}.isdisjoint(found)
    assert "SECRET_BODY" not in repr(found)
