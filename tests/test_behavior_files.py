"""Boundaries shared by every platform's metadata discovery."""

import json
from pathlib import Path

import pytest

from agenthound.discovery.behavior_files import (
    MAX_READ_BYTES, DiscoveryContext, local_command_files, read_jsonc,
    read_text, resolve_reference, safe_glob,
)
from agenthound.discovery.config_parser import discover_config_files, parse_config
from agenthound.discovery.source_registry import RULES


def write(path, content="private instruction body"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_registry_has_complete_semantic_metadata():
    channels = {"prompt_context", "persistent_instruction", "path_scoped_instruction", "reusable_prompt",
                "agent_definition", "skill", "memory", "runtime_configuration", "hook_execution",
                "plugin", "context_visibility", "mcp_advertisement"}
    for rule in RULES:
        assert rule.platform and rule.pattern and rule.evidence
        assert rule.scope in {"project", "user", "system", "managed", "session"}
        assert rule.influence_channel in channels
        assert rule.activation in {"automatic", "conditional", "on_demand", "session_resume", "configured", "observed", "unknown"}
        assert rule.confidence in {"confirmed", "suspected"}


def test_jsonc_preserves_strings_and_handles_comments_after_trailing_comma(tmp_path):
    path = write(tmp_path / "settings.json", '''{
      // ignored comment
      "url": "https://example.invalid/a//b/*literal*/", // string kept
      "items": [1, /* note */ ],
    }''')
    assert read_jsonc(path, tmp_path) == {"url": "https://example.invalid/a//b/*literal*/", "items": [1]}


def test_safe_glob_prunes_private_state_dependencies_and_symlink_escapes(tmp_path):
    project = tmp_path / "project"
    good = write(project / "CLAUDE.md")
    for relative in ("node_modules/pkg/CLAUDE.md", ".claude/projects/other/memory/CLAUDE.md",
                     ".codex/sessions/CLAUDE.md", ".git/CLAUDE.md", "vendor/pkg/CLAUDE.md"):
        write(project / relative)
    outside = write(tmp_path / "outside/CLAUDE.md")
    (project / "linked").symlink_to(outside.parent, target_is_directory=True)
    (project / "linked-CLAUDE.md").symlink_to(outside)
    assert safe_glob(project, "**/CLAUDE.md") == [good]


def test_oversized_files_are_not_read_or_truncated_into_valid_metadata(tmp_path, monkeypatch):
    path = write(tmp_path / "SKILL.md", "x" * (MAX_READ_BYTES + 1))
    original = Path.open

    def forbidden(self, *args, **kwargs):
        if self == path:
            pytest.fail("Oversized file must not be opened")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbidden)
    assert read_text(path, tmp_path) is None


@pytest.mark.parametrize("value", [None, {}, [], 42, "https://example.invalid/prompt.md", "${MISSING}/secret.md"])
def test_reference_parser_rejects_nonliteral_or_remote_references(tmp_path, value):
    assert resolve_reference(value, tmp_path / "CLAUDE.md", (tmp_path,)) is None


def test_command_references_never_export_module_modes_or_argument_files(tmp_path):
    script = write(tmp_path / "hook.py")
    write(tmp_path / "module")
    write(tmp_path / "secret-value")
    declaring = tmp_path / "_cwd"
    assert local_command_files("python hook.py --token secret-value", declaring, (tmp_path,)) == [script]
    assert local_command_files("python -m module", declaring, (tmp_path,)) == []
    assert local_command_files("python -c 'hook.py'", declaring, (tmp_path,)) == []
    assert local_command_files("python --arbitrary secret-value hook.py", declaring, (tmp_path,)) == []


def test_discovery_includes_documented_project_mcp_and_copilot_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    expected = {
        write(project / ".cursor/mcp.json", "{}"),
        write(project / ".vscode/mcp.json", "{}"),
        write(project / ".github/mcp.json", "{}"),
        write(project / ".github/copilot/settings.json", "{}"),
        write(home / ".copilot/settings.json", "{}"),
        write(home / ".copilot/mcp-config.json", "{}"),
    }
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    monkeypatch.setattr("agenthound.discovery.config_parser._DISCOVERY_CANDIDATES", [])
    assert set(discover_config_files(project)) == expected
    names = {path: parse_config(path)[0].name for path in expected}
    assert names[project / ".cursor/mcp.json"] == "cursor"
    assert names[project / ".vscode/mcp.json"] == "vscode-copilot"
    assert names[project / ".github/copilot/settings.json"] == "copilot-cli"
    assert names[home / ".copilot/mcp-config.json"] == "copilot-cli"


def test_discovery_does_not_follow_config_symlink_outside_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outside = write(tmp_path / "outside.json", json.dumps({"private": "secret"}))
    linked = project / ".cursor/mcp.json"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside)
    monkeypatch.setattr("agenthound.discovery.config_parser._DISCOVERY_CANDIDATES", [])
    assert linked not in discover_config_files(project)


def test_explicit_configs_honor_documented_custom_user_roots(tmp_path, monkeypatch):
    claude = write(tmp_path / "custom-claude/settings.json", "{}")
    codex = write(tmp_path / "custom-codex/config.toml", "")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude.parent))
    monkeypatch.setenv("CODEX_HOME", str(codex.parent))
    assert parse_config(claude)[0].name == "claude-code"
    assert parse_config(codex)[0].name == "codex"


def test_copilot_configuration_accepts_documented_jsonc_format(tmp_path):
    path = write(tmp_path / ".github/copilot/settings.json", '{\n// private comment\n"enabledPlugins": {}, // note\n}')
    assert parse_config(path)[0].name == "copilot-cli"


def test_project_hierarchy_stops_at_repository_root(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    workspace = repo / "src/package"
    workspace.mkdir(parents=True)
    context = DiscoveryContext(workspace, tmp_path / "home", "workspace")
    assert context.project_root == repo
    assert context.ancestors == (repo, repo / "src", workspace)
