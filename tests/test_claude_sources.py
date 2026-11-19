from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from agenthound.discovery import claude_sources
from agenthound.discovery.behavior_files import DiscoveryContext, MAX_READ_BYTES


def write(path: Path, text: str = "PRIVATE-BODY-MARKER") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path: Path, data: dict) -> Path:
    return write(path, json.dumps(data))


@pytest.fixture
def context(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    home.mkdir()
    workspace.mkdir()
    (workspace / ".git").mkdir()
    monkeypatch.setattr(claude_sources, "_managed_root", lambda: tmp_path / "managed")
    for name in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_PROJECT_DIR_NAME", "CLAUDE_CODE_DISABLE_AUTO_MEMORY"):
        monkeypatch.delenv(name, raising=False)
    return DiscoveryContext(workspace, home, "all")


def discover(context):
    return {item.path: item for item in claude_sources.discover(context, "claude-code")}


def test_project_local_nested_and_targeting(context):
    root = context.workspace
    automatic = [write(root / path) for path in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")]
    nested = [write(root / path) for path in ("src/CLAUDE.md", "src/CLAUDE.local.md", "src/.claude/CLAUDE.md")]
    found = discover(replace(context, scope="workspace"))
    assert set(found) == set(automatic + nested)
    assert all(found[path].rule.confidence == "confirmed" for path in automatic)
    assert all(found[path].rule.activation == "conditional" for path in nested)
    assert all(found[path].rule.confidence == "suspected" for path in nested)
    assert claude_sources.discover(context, "claude-desktop") == []
    assert claude_sources.discover(context, "codex") == []


def test_subdirectory_loads_ancestor_instructions_but_not_sibling(context):
    root = context.workspace
    ancestor = write(root / "CLAUDE.md")
    sub = root / "src"
    current = write(sub / "CLAUDE.local.md")
    write(root / "unrelated/CLAUDE.md")
    found = discover(replace(context, workspace=sub, scope="workspace"))
    assert set(found) == {ancestor, current}
    assert all(item.rule.activation == "automatic" for item in found.values())


@pytest.mark.parametrize("scope", ["workspace", "global", "all"])
def test_scope_and_custom_config_directory(context, monkeypatch, scope):
    user = context.home / "custom-claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user))
    project = write(context.workspace / "CLAUDE.local.md")
    user_instruction = write(user / "CLAUDE.md")
    user_settings = write_json(user / "settings.json", {})
    user_state = write_json(user / ".claude.json", {"token": "STATE-SECRET"})
    found = discover(replace(context, scope=scope))
    expected = ({project} if scope in {"workspace", "all"} else set()) | (
        {user_instruction, user_settings, user_state} if scope in {"global", "all"} else set()
    )
    assert set(found) == expected
    assert "STATE-SECRET" not in repr(found)


def test_rules_commands_skills_agents_and_styles(context):
    root = context.workspace
    rule = write(root / ".claude/rules/base.md")
    scoped = write(root / ".claude/rules/python.md", "---\npaths:\n  - src/**/*.py\n---\nPRIVATE-RULE")
    command = write(root / ".claude/commands/deploy.md")
    agent = write(root / ".claude/agents/reviewer.md")
    skill = write(root / ".claude/skills/review/SKILL.md", "---\nname: review\ndescription: Review changes\n---\nPRIVATE-SKILL")
    style = write(root / ".claude/output-styles/concise.md", "---\nname: concise\n---\nPRIVATE-STYLE")
    write_json(root / ".claude/settings.local.json", {"outputStyle": "concise"})
    found = discover(context)
    assert found[rule].rule.confidence == "confirmed"
    assert found[scoped].confidence == "suspected"
    assert found[scoped].activation == "conditional"
    for path, kind in ((command, "PromptFile"), (agent, "AgentDefinition"), (skill, "Skill"), (style, "OutputStyle")):
        assert found[path].rule.source_kind == kind
    assert found[style].activation == "configured"
    assert all("PRIVATE-" not in repr(item) for item in found.values())


def test_memory_only_selected_repository_index_and_topics(context):
    key = claude_sources.re.sub(r"[^a-zA-Z0-9]", "-", str(context.workspace))
    selected = context.home / ".claude/projects" / key / "memory"
    index = write(selected / "MEMORY.md", "PRIVATE-MEMORY")
    topic = write(selected / "testing.md", "PRIVATE-TOPIC")
    write(context.home / ".claude/projects/other-project/memory/MEMORY.md", "OTHER-PROJECT-SECRET")
    write(selected.parent / "session-id.jsonl", "PRIVATE-TRANSCRIPT")
    found = discover(replace(context, scope="global"))
    assert set(found) == {index, topic}
    assert found[index].rule.activation == "automatic"
    assert found[index].confidence == "confirmed"
    assert found[topic].rule.activation == "on_demand"
    assert found[topic].confidence == "suspected"
    assert "PRIVATE-" not in repr(found)
    assert "OTHER-PROJECT-SECRET" not in repr(found)
    assert not any(item.rule.source_kind == "Memory" for item in discover(replace(context, scope="workspace")).values())


def test_custom_memory_and_explicit_disable(context, monkeypatch):
    custom = context.home / "my-memory"
    index = write(custom / "MEMORY.md")
    config = context.workspace / ".claude/settings.json"
    write_json(config, {"autoMemoryDirectory": "~/my-memory"})
    found = discover(context)
    assert found[index].confidence == "suspected"
    write_json(config, {"autoMemoryDirectory": "~/my-memory", "autoMemoryEnabled": False})
    assert index not in discover(context)
    write_json(config, {"autoMemoryDirectory": "~/my-memory"})
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "1")
    assert index not in discover(context)


def test_explicit_project_directory_name_without_scanning_other_projects(context, monkeypatch):
    claude = context.home / "configured"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CLAUDE_CODE_PROJECT_DIR_NAME", "selected")
    selected = write(claude / "projects/selected/memory/MEMORY.md")
    write(claude / "projects/unrelated/memory/MEMORY.md")
    assert set(discover(context)) == {selected}


def test_worktree_uses_main_repository_memory(context):
    main = context.workspace
    worktree = main.parent / "worktree"
    worktree.mkdir()
    gitdir = main / ".git/worktrees/worktree"
    write(gitdir / "commondir", "../..\n")
    write(worktree / ".git", "gitdir: " + str(gitdir))
    key = claude_sources.re.sub(r"[^a-zA-Z0-9]", "-", str(main))
    selected = write(context.home / ".claude/projects" / key / "memory/MEMORY.md")
    assert selected in discover(replace(context, workspace=worktree))


def test_imports_relative_escaped_spaces_cycles_code_and_depth(context):
    root = context.workspace
    instruction = write(root / "CLAUDE.md", "@docs/one.md @Design\\ Docs/guide.md\n`@ignored.md`\n```md\n@ignored.md\n```\n@\"ignored.md\"\n")
    one = write(root / "docs/one.md", "@../CLAUDE.md @two.md")
    two = write(root / "docs/two.md", "@three.md")
    three = write(root / "docs/three.md", "@four.md")
    four = write(root / "docs/four.md", "@five.md")
    write(root / "docs/five.md")
    spaced = write(root / "Design Docs/guide.md")
    ignored = write(root / "ignored.md")
    found = discover(replace(context, scope="workspace"))
    assert set(found) == {instruction, one, two, three, four, spaced}
    assert ignored not in found
    assert len(found) == len(set(found))


def test_imports_cannot_escape_root_or_follow_external_links(context, tmp_path):
    root = context.workspace
    outside = write(tmp_path / "outside.md")
    write(root / "CLAUDE.md", "@../outside.md @linked.md @node_modules/reference.md")
    (root / "linked.md").symlink_to(outside)
    write(root / "node_modules/reference.md")
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/linked.md").symlink_to(outside)
    assert set(discover(replace(context, scope="workspace"))) == {root / "CLAUDE.md"}


def test_oversized_and_malformed_metadata_do_not_expand_references(context):
    root = context.workspace
    imported = write(root / "reference.md")
    oversized = write(root / "CLAUDE.md", "x" * MAX_READ_BYTES + "\n@reference.md")
    malformed = write(root / ".claude/rules/broken.md", "---\npaths: [\nPRIVATE-BODY")
    config = write(root / ".claude/settings.json", '{"hooks": SECRET-JSON-VALUE}')
    found = discover(context)
    assert imported not in found
    assert found[oversized].discovery_status == "metadata_unavailable"
    assert found[malformed].discovery_status == "metadata_invalid"
    assert found[config].discovery_status == "metadata_unavailable"
    assert "SECRET-JSON-VALUE" not in repr(found)


def test_hook_settings_and_referenced_scripts_never_execute_or_export_arguments(context, monkeypatch):
    root = context.workspace
    script = write(root / "hooks/check.py", "raise RuntimeError('MUST-NOT-EXECUTE')")
    extra_argument = write(root / "SECRET-ARGUMENT.txt")
    config = write_json(root / ".claude/settings.json", {"env": {"API_KEY": "SECRET-ENV"}, "hooks": {
        "PreToolUse": [{"hooks": [{"type": "command", "command": "python3 hooks/check.py --token SECRET-ARGUMENT.txt"}]}]
    }})

    def forbidden(*args, **kwargs):
        pytest.fail("Discovery attempted to execute a hook")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    found = discover(context)
    assert found[config].rule.influence_channel == "runtime_configuration"
    assert found[script].rule.influence_channel == "hook_execution"
    assert found[script].rule.confidence == "suspected"
    assert extra_argument not in found
    assert "SECRET-ENV" not in repr(found)
    assert "SECRET-ARGUMENT" not in repr(found)
    assert "MUST-NOT-EXECUTE" not in repr(found)
    write_json(config, {"disableAllHooks": True, "hooks": {"PreToolUse": [{"hooks": [
        {"type": "command", "command": "python3 hooks/check.py"}
    ]}]}})
    assert script not in discover(context)


def test_exec_form_and_frontmatter_hook_references(context):
    root = context.workspace
    script = write(root / "hooks/check.py")
    write_json(root / ".claude/settings.json", {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python3", "args": ["${CLAUDE_PROJECT_DIR}/hooks/check.py", "SECRET-ARG"]}
    ]}]}})
    skill_script = write(root / ".claude/hooks/skill.sh")
    write(root / ".claude/skills/review/SKILL.md", "---\nname: review\nhooks:\n  Stop:\n    - hooks:\n        - type: command\n          command: '\"${CLAUDE_PROJECT_DIR}\"/.claude/hooks/skill.sh'\n---\nPRIVATE-SKILL-BODY")
    found = discover(context)
    assert script in found
    assert skill_script in found
    assert "SECRET-ARG" not in repr(found)


def test_declared_skills_directory_plugins_respect_enabled_state_and_manifest_paths(context):
    base = context.workspace / ".claude/skills"
    enabled = base / "enabled"
    manifest = write_json(enabled / ".claude-plugin/plugin.json", {
        "name": "enabled", "commands": ["./custom/deploy.md"], "agents": "./agents/reviewer.md",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}"/scripts/check.sh'}]}]},
    })
    command = write(enabled / "custom/deploy.md")
    ignored_default = write(enabled / "commands/ignored.md")
    agent = write(enabled / "agents/reviewer.md")
    skill = write(enabled / "skills/review/SKILL.md")
    script = write(enabled / "scripts/check.sh")
    mcp = write_json(enabled / ".mcp.json", {"mcpServers": {"server": {"env": {"KEY": "SECRET-PLUGIN"}}}})
    disabled = base / "disabled"
    write_json(disabled / ".claude-plugin/plugin.json", {"name": "disabled", "defaultEnabled": False})
    disabled_skill = write(disabled / "skills/hidden/SKILL.md")
    escaped = write(base / "outside.md")
    write_json(enabled / ".claude-plugin/plugin.json", {
        "name": "enabled", "commands": ["./custom/deploy.md", "./../outside.md"],
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}"/scripts/check.sh'}]}]},
    })
    found = discover(context)
    assert {manifest, command, agent, skill, script, mcp} <= set(found)
    assert ignored_default not in found
    assert disabled_skill not in found
    assert escaped not in found
    assert "SECRET-PLUGIN" not in repr(found)
    write_json(context.workspace / ".claude/settings.local.json", {"enabledPlugins": {"enabled@skills-dir": False}})
    assert not any(path.is_relative_to(enabled) for path in discover(context))


def test_plugin_cache_requires_opt_in_enabled_id_and_matching_project(context):
    claude = context.home / ".claude"
    plugin = claude / "plugins/cache/market/enabled/1.0"
    manifest = write_json(plugin / ".claude-plugin/plugin.json", {"name": "enabled"})
    command = write(plugin / "commands/review.md")
    other = claude / "plugins/cache/market/other/1.0"
    write_json(other / ".claude-plugin/plugin.json", {"name": "other"})
    write(other / "commands/hidden.md")
    write_json(claude / "settings.json", {"enabledPlugins": {"enabled@market": True, "other@market": False}})
    write_json(claude / "plugins/installed_plugins.json", {"plugins": {
        "enabled@market": [{"scope": "project", "projectPath": str(context.workspace), "installPath": str(plugin)}],
        "other@market": [{"scope": "user", "installPath": str(other)}],
    }})
    assert manifest not in discover(context)
    enabled = discover(replace(context, include_plugin_cache=True))
    assert manifest in enabled and command in enabled
    assert not any(path.is_relative_to(other) for path in enabled)
    assert manifest not in discover(replace(context, scope="global", include_plugin_cache=True))
    write_json(claude / "plugins/installed_plugins.json", {"plugins": {"enabled@market": [
        {"scope": "project", "projectPath": str(context.workspace.parent / "other-project"), "installPath": str(plugin)}
    ]}})
    assert manifest not in discover(replace(context, include_plugin_cache=True))


def test_existing_managed_paths_only_and_lookalikes_ignored(context, monkeypatch):
    managed = context.workspace.parent / "managed"
    monkeypatch.setattr(claude_sources, "_managed_root", lambda: managed)
    policy = write(managed / "CLAUDE.md")
    settings = write_json(managed / "managed-settings.json", {})
    dropin = write_json(managed / "managed-settings.d/10-policy.json", {})
    mcp = write_json(managed / "managed-mcp.json", {})
    write(context.workspace / "custom/rules.md")
    write(context.workspace / ".claude/rules-not-really/file.md")
    write(context.workspace / ".claude/agents/not-an-agent.txt")
    found = discover(context)
    assert set(found) == {policy, settings, dropin, mcp}
    assert all(item.rule.scope == "managed" for item in found.values())
    assert discover(replace(context, scope="workspace")) == {}


def test_agents_fallback_respects_claude_presence_and_user_mode(context):
    root = context.workspace
    agents = write(root / "AGENTS.md")
    nested = write(root / "src/AGENTS.md")
    write(root / ".agents/skills/ignored/AGENTS.md")
    found = discover(context)
    assert set(found) == {agents, nested}
    assert found[agents].activation == "automatic"
    assert found[agents].confidence == "suspected"
    assert found[nested].activation == "conditional"
    claude = write(root / "CLAUDE.local.md")
    assert agents not in discover(context)
    settings = write_json(context.home / ".claude/settings.json", {"pluginConfigs": {
        "cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "claude-md-and-agents-md"}}
    }})
    assert {agents, nested, claude, settings} <= set(discover(context))
    write_json(settings, {"enabledPlugins": {"cc-plugin-agents-md@builtin": False}})
    assert agents not in discover(context)


def test_root_local_settings_apply_from_subdirectory_and_override_old_local(context):
    root = context.workspace
    root_settings = write_json(root / ".claude/settings.local.json", {"autoMemoryEnabled": False})
    sub = root / "src"
    local_settings = write_json(sub / ".claude/settings.local.json", {"autoMemoryEnabled": True})
    found = discover(replace(context, workspace=sub))
    assert {root_settings, local_settings} <= set(found)
    key = claude_sources.re.sub(r"[^a-zA-Z0-9]", "-", str(root))
    index = write(context.home / ".claude/projects" / key / "memory/MEMORY.md")
    assert index not in discover(replace(context, workspace=sub))


def test_memory_directory_symlink_and_restricted_project_custom_directory_are_skipped(context):
    claude = context.home / ".claude"
    key = claude_sources.re.sub(r"[^a-zA-Z0-9]", "-", str(context.workspace))
    other = claude / "projects/unrelated/memory"
    write(other / "MEMORY.md")
    selected = claude / "projects" / key
    selected.mkdir()
    (selected / "memory").symlink_to(other, target_is_directory=True)
    assert not any(item.rule.source_kind == "Memory" for item in discover(context).values())
    custom = write(context.home / "custom-memory/MEMORY.md")
    write_json(context.workspace / ".claude/settings.json", {
        "autoMemoryDirectory": str(custom.parent), "permissions": {"blockReadsOutsideWorkingDirectories": True}
    })
    assert custom not in discover(context)


def test_import_budget_and_excluded_imports_do_not_expand(context):
    root = context.workspace
    write(root / "CLAUDE.md", " ".join(f"@refs/{index}.txt" for index in range(140)) + " @excluded.txt")
    for index in range(140):
        write(root / f"refs/{index}.txt")
    write(root / "excluded.txt", "@private.txt")
    private = write(root / "private.txt")
    write_json(root / ".claude/settings.json", {"claudeMdExcludes": ["excluded.txt"]})
    found = discover(context)
    imports = [item for item in found.values() if item.origin == "claude-import"]
    assert len(imports) <= claude_sources.MAX_REFERENCES
    assert private not in found
    # Exercise exclusion before traversal without the import budget masking it.
    write(root / "CLAUDE.md", "@excluded.txt")
    assert private not in discover(context)


def test_nested_skills_plugin_is_not_enabled_by_presence_of_its_components(context):
    root = context.workspace / "src/.claude/skills/plugin"
    write_json(root / ".claude-plugin/plugin.json", {"name": "nested", "defaultEnabled": False})
    nested = write(root / "skills/hidden/SKILL.md")
    assert nested not in discover(context)


def test_subagent_memory_requires_declared_scope_and_remains_conditional(context):
    root = context.workspace
    write(root / ".claude/agents/reviewer.md", "---\nname: reviewer\nmemory: project\n---\nPRIVATE-AGENT")
    index = write(root / ".claude/agent-memory/reviewer/MEMORY.md", "PRIVATE-AGENT-MEMORY")
    topic = write(root / ".claude/agent-memory/reviewer/testing.md", "PRIVATE-AGENT-TOPIC")
    write(root / ".claude/agent-memory/unconfigured/MEMORY.md")
    found = discover(context)
    assert found[index].activation == "conditional"
    assert found[index].confidence == "suspected"
    assert found[topic].activation == "on_demand"
    assert all("unconfigured" not in str(path) for path in found)
    assert "PRIVATE-AGENT" not in repr(found)
    write_json(root / ".claude/settings.json", {"autoMemoryEnabled": False})
    assert index not in discover(context)


def test_managed_hooks_are_not_disabled_by_user_setting(context, monkeypatch):
    managed = context.workspace.parent / "managed"
    monkeypatch.setattr(claude_sources, "_managed_root", lambda: managed)
    script = write(managed / "check.sh")
    write_json(managed / "managed-settings.json", {"allowManagedHooksOnly": True, "hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": str(script)}
    ]}]}})
    user_script = write(context.home / ".claude/user.sh")
    write_json(context.home / ".claude/settings.json", {"disableAllHooks": True, "hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": str(user_script)}
    ]}]}})
    found = discover(context)
    assert script in found
    assert user_script not in found


def test_deeply_nested_plugin_paths_are_bounded(context):
    plugin = context.workspace / ".claude/skills/plugin"
    deeply_nested = "[" * 600 + '"./custom/"' + "]" * 600
    write(plugin / ".claude-plugin/plugin.json", '{"name":"plugin", "skills":' + deeply_nested + "}")
    custom = write(plugin / "custom/review/SKILL.md")
    assert custom not in discover(context)


def test_imported_home_file_and_literal_other_project_memory_are_user_sources(context):
    root = context.workspace
    personal = write(context.home / "standards.md", "PRIVATE-HOME-IMPORT")
    referenced_memory = write(context.home / ".claude/projects/other/memory/MEMORY.md")
    write(root / "CLAUDE.md", "@~/standards.md @~/.claude/projects/other/memory/MEMORY.md")
    found = discover(context)
    assert personal in found
    assert found[personal].confidence == "suspected"
    assert referenced_memory in found
    assert found[referenced_memory].rule.source_kind == "Memory"
    assert found[referenced_memory].rule.influence_channel == "memory"
    assert found[referenced_memory].scope == "user"
    assert found[referenced_memory].confidence == "suspected"
    assert personal not in discover(replace(context, scope="workspace"))
    assert referenced_memory not in discover(replace(context, scope="workspace"))


@pytest.mark.parametrize("scope", ["all", "global", "workspace"])
def test_explicit_other_project_memory_is_exact_metadata_only_and_scoped(context, monkeypatch, scope):
    claude = context.home / ".claude"
    memory = claude / "projects/other/memory"
    referenced = write(memory / "topic.md", "@must-not-read.md\nPRIVATE-OTHER-MEMORY")
    sibling = write(memory / "must-not-read.md", "PRIVATE-SIBLING-MEMORY")
    index = write(memory / "MEMORY.md", "PRIVATE-NONREFERENCED-INDEX")
    transcript = write(memory.parent / "session.jsonl", "PRIVATE-SESSION")
    declaring = claude / "CLAUDE.md" if scope == "global" else context.workspace / "CLAUDE.local.md"
    write(declaring, f"@{referenced} @{transcript}")
    original_read_text = claude_sources.read_text

    def metadata_reads_only(path, root):
        assert path not in {referenced, sibling, index, transcript}, "Explicit memory content must not be read"
        return original_read_text(path, root)

    monkeypatch.setattr(claude_sources, "read_text", metadata_reads_only)
    found = discover(replace(context, scope=scope))
    assert (referenced in found) == (scope != "workspace")
    assert sibling not in found and index not in found and transcript not in found
    if referenced in found:
        assert found[referenced].activation == "automatic"
        assert "Explicit literal @path memory reference" in found[referenced].evidence
        assert found[referenced].rule.metadata_parser == ""
    assert "PRIVATE-" not in repr(found)


def test_explicit_memory_import_rejects_file_and_directory_symlink_escapes(context):
    claude = context.home / ".claude"
    memory = claude / "projects/other/memory"
    outside = write(context.home / "outside.md", "PRIVATE-EXTERNAL")
    memory.mkdir(parents=True)
    linked = memory / "linked.md"
    linked.symlink_to(outside)
    external_root = claude / "projects/directory-link"
    external_root.mkdir()
    (external_root / "memory").symlink_to(outside.parent, target_is_directory=True)
    write(context.workspace / "CLAUDE.md", f"@{linked} @{external_root / 'memory/outside.md'}")
    found = discover(context)
    assert linked not in found
    assert not any(item.rule.source_kind == "Memory" for item in found.values())


def test_explicit_memory_exports_memory_edge_without_reading_body(context, monkeypatch):
    from agenthound.collectors import sources
    from agenthound.discovery.git_metadata import SourceMetadata
    from agenthound.models.nodes import Agent
    from agenthound.output.opengraph import build_opengraph

    memory = write(context.home / ".claude/projects/other/memory/topic.md", "PRIVATE-MEMORY-CONTENT\n@hidden.md")
    write(memory.parent / "hidden.md", "PRIVATE-SIBLING-CONTENT")
    write(context.workspace / "CLAUDE.md", f"@{memory}")
    monkeypatch.setattr("agenthound.discovery.skills._user_home", lambda: context.home)
    monkeypatch.setattr(sources, "inspect_source", lambda path: SourceMetadata())
    original_read_text = sources.read_text

    def metadata_only(path, root):
        assert path != memory, "Memory must not be read by central metadata checks"
        return original_read_text(path, root)

    monkeypatch.setattr(sources, "read_text", metadata_only)
    result = sources.collect_sources(
        Agent(name="claude-code", platform="Claude Code", config_path=str(context.workspace)),
        context.workspace, "all",
    )
    source = next(item for item in result.sources if item.path == str(memory))
    assert source.source_kind == "Memory"
    assert source.influence_channel == "memory"
    edge = next(item for item in result.edges if item.start == source.objectid)
    assert edge.properties["confidence"] == "suspected"
    assert edge.properties["content_exported"] is False
    exported = json.dumps(build_opengraph(result))
    assert "PRIVATE-MEMORY-CONTENT" not in exported
    assert "PRIVATE-SIBLING-CONTENT" not in exported
    assert "hidden.md" not in exported


def test_plugin_cache_root_cannot_reapprove_an_external_symlink_target(context):
    claude = context.home / ".claude"
    outside = context.workspace.parent / "external-cache"
    plugin = outside / "market/enabled/1.0"
    write_json(plugin / ".claude-plugin/plugin.json", {"name": "enabled"})
    command = write(plugin / "commands/review.md")
    write_json(claude / "settings.json", {"enabledPlugins": {"enabled@market": True}})
    write_json(claude / "plugins/installed_plugins.json", {"plugins": {"enabled@market": [{
        "scope": "user", "installPath": str(claude / "plugins/cache/market/enabled/1.0")
    }]}})
    (claude / "plugins/cache").symlink_to(outside, target_is_directory=True)
    found = discover(replace(context, include_plugin_cache=True))
    assert not any(path.resolve() == command for path in found)


@pytest.mark.parametrize("component", ["projects", "project", "memory"])
def test_default_memory_directory_ancestors_cannot_reapprove_external_targets(context, component):
    claude = context.home / ".claude"
    key = claude_sources.re.sub(r"[^a-zA-Z0-9]", "-", str(context.workspace))
    selected = claude / "projects" / key / "memory"
    outside = context.workspace.parent / "external-memory"
    boundary = {"projects": claude / "projects", "project": selected.parent, "memory": selected}[component]
    boundary.parent.mkdir(parents=True, exist_ok=True)
    outside.mkdir()
    boundary.symlink_to(outside, target_is_directory=True)
    write(selected / "MEMORY.md")
    assert not any(item.rule.source_kind == "Memory" for item in discover(context).values())


def test_literal_custom_memory_directory_symlink_is_not_approved(context):
    outside = context.workspace.parent / "external-custom-memory"
    write(outside / "MEMORY.md")
    configured = context.home / "memory-alias"
    configured.symlink_to(outside, target_is_directory=True)
    write_json(context.workspace / ".claude/settings.json", {"autoMemoryDirectory": str(configured)})
    assert not any(item.rule.source_kind == "Memory" for item in discover(context).values())


@pytest.mark.parametrize("scope", ["project", "user"])
def test_enabled_single_root_skill_plugin_without_skills_directory(context, scope):
    base = context.workspace / ".claude" if scope == "project" else context.home / ".claude"
    plugin = base / "skills/single"
    write_json(plugin / ".claude-plugin/plugin.json", {"name": "single"})
    skill = write(plugin / "SKILL.md", "---\nname: single\ndescription: Single plugin skill\n---\nPRIVATE-SINGLE-SKILL-BODY")
    found = discover(context)
    assert found[skill].rule.source_kind == "Skill"
    assert found[skill].rule.influence_channel == "skill"
    assert found[skill].scope == scope
    assert found[skill].origin == "plugin"
    assert "PRIVATE-SINGLE-SKILL-BODY" not in repr(found)
    write_json(plugin / ".claude-plugin/plugin.json", {"name": "single", "skills": []})
    assert skill not in discover(context)
    write_json(plugin / ".claude-plugin/plugin.json", {"name": "single"})
    (plugin / "skills").mkdir()
    assert skill not in discover(context)


def test_primary_directory_plugins_do_not_load_from_ancestor(context):
    plugin = context.workspace / ".claude/skills/plugin"
    write_json(plugin / ".claude-plugin/plugin.json", {"name": "plugin"})
    command = write(plugin / "commands/review.md")
    skill = write(plugin / "skills/review/SKILL.md")
    sub = context.workspace / "src"
    sub.mkdir()
    found = discover(replace(context, workspace=sub))
    assert command not in found and skill not in found
