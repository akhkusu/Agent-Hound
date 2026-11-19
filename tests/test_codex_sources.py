import json
import subprocess

import pytest

from agenthound.discovery import codex_sources as codex
from agenthound.discovery.behavior_files import DiscoveryContext


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(codex, "_SYSTEM_ROOT", tmp_path / "system")
    return home, repo


def write(path, content="PRIVATE_INSTRUCTION_BODY"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def files(context):
    return {item.path: item for item in codex.discover(context, "codex")}


def test_instruction_precedence_nested_scope_and_siblings(layout):
    home, repo = layout
    selected = repo / "services/payments"
    selected.mkdir(parents=True)
    global_regular = write(home / ".codex/AGENTS.md")
    global_override = write(home / ".codex/AGENTS.override.md")
    root = write(repo / "AGENTS.md")
    regular = write(selected / "AGENTS.md")
    override = write(selected / "AGENTS.override.md")
    nested = write(selected / "internal/AGENTS.md")
    sibling = write(repo / "services/search/AGENTS.md")
    found = files(DiscoveryContext(selected, home, "all"))
    assert {global_override, root, override, nested} <= found.keys()
    assert {global_regular, regular, sibling}.isdisjoint(found)
    assert found[root].confidence == found[override].confidence == "confirmed"
    assert found[nested].activation == "conditional"
    assert found[nested].confidence == "suspected"
    assert found[root].precedence < found[override].precedence
    assert "PRIVATE_INSTRUCTION_BODY" not in repr(found)


def test_empty_override_fallback_order_and_safe_filenames(layout):
    home, repo = layout
    write(home / ".codex/config.toml", 'project_doc_fallback_filenames = ["TEAM.md", "SECOND.md", "../outside.md"]')
    write(repo / "AGENTS.override.md", " \n")
    team = write(repo / "TEAM.md")
    second = write(repo / "SECOND.md")
    outside = write(repo.parent / "outside.md")
    nested = write(repo / "sub/AGENTS.md")
    nested_fallback = write(repo / "sub/TEAM.md")
    found = files(DiscoveryContext(repo, home, "workspace"))
    assert {team, nested} <= found.keys()
    assert {second, outside, nested_fallback}.isdisjoint(found)
    assert found[team].rule is codex.FALLBACK


def test_scopes_skills_configs_and_custom_codex_home(layout, monkeypatch):
    home, repo = layout
    custom = home / "profile"
    monkeypatch.setenv("CODEX_HOME", str(custom))
    project = write(repo / ".agents/skills/team/SKILL.md", "---\nname: team\ndescription: Help a team\n---\nBODY")
    user = write(home / ".agents/skills/user/SKILL.md")
    bundled = write(custom / "skills/.system/system/SKILL.md")
    old = write(home / ".codex/skills/old/SKILL.md")
    config = write(custom / "config.toml", "sandbox_mode = 'read-only'")
    active = write(custom / "AGENTS.md")
    project_config = write(repo / ".codex/config.toml", "sandbox_mode = 'workspace-write'")
    global_files = files(DiscoveryContext(repo, home, "global"))
    workspace = files(DiscoveryContext(repo, home, "workspace"))
    assert {user, bundled, config, active} <= global_files.keys()
    assert global_files[bundled].scope == "system"
    assert {project, project_config} <= workspace.keys()
    assert old not in global_files
    assert all(item.scope == "project" for item in workspace.values())
    assert codex.discover(DiscoveryContext(repo, home, "all"), "cursor") == []


def test_config_references_cycles_and_hooks_never_execute(layout, monkeypatch):
    home, repo = layout
    instructions = write(repo / ".codex/guidance.txt")
    compact = write(repo / ".codex/compact.md")
    role = write(repo / ".codex/agents/reviewer.toml", '[agents.self]\nconfig_file = "reviewer.toml"\n')
    script = write(repo / ".codex/hooks/check.py", "raise RuntimeError('MUST_NOT_EXECUTE')")
    config = write(repo / ".codex/config.toml", '''model_instructions_file = "guidance.txt"
experimental_compact_prompt_file = "compact.md"
[agents.reviewer]
config_file = "agents/reviewer.toml"
[[hooks.SessionStart]]
[[hooks.SessionStart.hooks]]
type = "command"
command = "python .codex/hooks/check.py --token SECRET_COMMAND_ARG"
''')
    def forbidden(*args, **kwargs):
        pytest.fail("Source discovery executed a command")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    found = files(DiscoveryContext(repo, home, "workspace"))
    assert {instructions, compact, role, script, config} <= found.keys()
    assert found[config].rule.influence_channel == "runtime_configuration"
    assert found[role].rule.activation == "on_demand"
    assert found[script].rule.source_kind == "HookScript"
    assert "SECRET_COMMAND_ARG" not in repr(found)
    assert "MUST_NOT_EXECUTE" not in repr(found)


def test_disabled_hooks_and_skills_do_not_create_invocation_sources(layout):
    home, repo = layout
    script = write(repo / ".codex/check.sh")
    skill = write(repo / ".agents/skills/off/SKILL.md")
    write(repo / ".codex/config.toml", f'''[features]
hooks = false
[[skills.config]]
path = "{skill}"
enabled = false
''')
    write(repo / ".codex/hooks.json", json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command", "command": ".codex/check.sh"}
    ]}]}}))
    found = files(DiscoveryContext(repo, home, "workspace"))
    assert script not in found
    assert skill not in found


def test_enabled_local_plugins_only_and_overlay_precedence(layout):
    home, repo = layout
    write(repo / ".codex/config.toml", '[plugins."on@repo"]\nenabled = true\n[plugins."off@repo"]\nenabled = false\n')
    write(repo / ".agents/plugins/marketplace.json", json.dumps({"name": "repo", "plugins": [
        {"name": name, "source": {"source": "local", "path": f"./plugins/{name}"}} for name in ("on", "off")
    ]}))
    on = repo / "plugins/on"
    manifest = write(on / "plugin.json", json.dumps({"name": "on", "extensions": {"com.openai": {"hooks": "./custom.json"}}}))
    write(on / ".codex-plugin/plugin.json", json.dumps({"hooks": "./ignored.json"}))
    skill = write(on / "skills/helper/SKILL.md")
    mcp = write(on / "mcp.json", '{"mcpServers":{"remote":{"headers":{"Authorization":"SECRET_TOKEN"}}}}')
    hooks = write(on / "custom.json", '{"hooks":{}}')
    ignored = write(on / "ignored.json", '{}')
    off = write(repo / "plugins/off/.codex-plugin/plugin.json", '{"name":"off"}')
    unrelated = write(repo / "other/plugin.json", '{"name":"unrelated"}')
    found = files(DiscoveryContext(repo, home, "workspace"))
    assert {manifest, skill, mcp, hooks} <= found.keys()
    assert {ignored, off, unrelated}.isdisjoint(found)
    assert "SECRET_TOKEN" not in repr(found)


def test_cache_is_opt_in_and_not_guessed_from_unrelated_plugins(layout):
    home, repo = layout
    write(repo / ".codex/config.toml", '[plugins."on@repo"]\nenabled = true\n')
    write(repo / ".agents/plugins/marketplace.json", '{"name":"repo","plugins":[{"name":"on","source":{"source":"url","url":"https://example.invalid/plugin"}}]}')
    cached = write(home / ".codex/plugins/cache/repo/on/1.0/.codex-plugin/plugin.json", '{"name":"on"}')
    unrelated = write(home / ".codex/plugins/cache/other/secret/1.0/.codex-plugin/plugin.json", '{"name":"secret"}')
    assert cached not in files(DiscoveryContext(repo, home, "all"))
    found = files(DiscoveryContext(repo, home, "all", include_plugin_cache=True))
    assert cached in found
    assert unrelated not in found


def test_symlink_escapes_oversized_and_malformed_files_are_safe(layout):
    home, repo = layout
    outside = write(repo.parent / "outside/AGENTS.md")
    (repo / "AGENTS.md").symlink_to(outside)
    write(repo / ".codex/config.toml", 'model_instructions_file = { malformed = true }')
    oversized = write(repo / "nested/AGENTS.md", "SECRET_BODY" * 10000)
    (repo / ".agents").mkdir()
    (repo / ".agents/skills").symlink_to(outside.parent, target_is_directory=True)
    found = files(DiscoveryContext(repo, home, "all"))
    assert repo / "AGENTS.md" not in found
    assert oversized in found
    assert "SECRET_BODY" not in repr(found)


def test_project_instruction_byte_budget_and_disabled_skill_names(layout):
    home, repo = layout
    selected = repo / "sub"
    selected.mkdir()
    write(home / ".codex/config.toml", 'project_doc_max_bytes = 3\n[[skills.config]]\nname = "off"\nenabled = false\n')
    root = write(repo / "AGENTS.md", "ROOT_BODY")
    child = write(selected / "AGENTS.md")
    off = write(repo / ".agents/skills/custom/SKILL.md", "---\nname: off\ndescription: Never selected\n---\nBODY")
    found = files(DiscoveryContext(selected, home, "all"))
    assert root in found
    assert found[root].discovery_status == "partially_available"
    assert child not in found
    assert off not in found
