"""Canonical MCP sources, scopes, secret exclusion and discovery isolation."""
import json
from pathlib import Path

import pytest

from agenthound.collectors.capabilities import collect_from_config, collect_from_configs
from agenthound.discovery.config_parser import discover_config_files
from agenthound.output.opengraph import build_opengraph


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home, project = tmp_path / 'home', tmp_path / 'project'
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False)
    monkeypatch.delenv('CODEX_HOME', raising=False)
    monkeypatch.setattr('agenthound.discovery.config_parser._DISCOVERY_CANDIDATES', [])
    return home, project


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    return path


def server(package):
    return {'command': 'uvx', 'args': [package]}


def test_mcp_only_discovery_one_agent_and_read(layout):
    home, project = layout
    write(home / '.claude.json', {'mcpServers': {'fetch': server('mcp-server-fetch')}})
    write(project / '.mcp.json', {'mcpServers': {'git': server('mcp-server-git')}})
    paths = discover_config_files(project)
    assert len(paths) == 2
    result = collect_from_configs(paths, workspace=project)
    assert len(result.agents) == 1
    assert {c.name for c in result.capabilities} == {'fetch', 'git', 'Read'}
    assert all(e.properties['confidence'] == 'suspected' for e in result.edges)


def test_local_project_user_whole_definition_precedence(layout):
    home, project = layout
    write(home / '.claude.json', {'mcpServers': {'same': server('mcp-server-fetch')}, 'projects': {
        str(project): {'mcpServers': {'same': server('mcp-server-git')}},
        str(project.parent / 'other'): {'mcpServers': {'foreign': server('mcp-server-commands')}}}})
    write(project / '.mcp.json', {'mcpServers': {'same': server('mcp-server-commands')}})
    result = collect_from_configs(discover_config_files(project), workspace=project)
    cap = next(c for c in result.capabilities if c.name == 'same')
    assert cap.mcp_scope == 'local'
    assert cap.git_write == 'suspected'
    assert cap.shell_exec == 'none' and cap.network_send == 'none'
    assert len(result.capabilities) == 2


def test_project_over_user_no_field_merge(layout):
    home, project = layout
    write(home / '.claude.json', {'mcpServers': {'same': server('mcp-server-commands')}})
    write(project / '.mcp.json', {'mcpServers': {'same': {'type': 'http', 'url': 'https://dummy.invalid'}}})
    result = collect_from_configs(discover_config_files(project), workspace=project)
    cap = next(c for c in result.capabilities if c.name == 'same')
    assert cap.mcp_scope == 'project' and cap.transport == 'http'
    assert cap.shell_exec == 'none' and cap.command is None


def test_disabled_and_rejected_servers(layout):
    home, project = layout
    write(home / '.claude.json', {'mcpServers': {'off': server('mcp-server-fetch')}, 'projects': {str(project): {'disabledMcpServers': ['off']}}})
    write(project / '.mcp.json', {'mcpServers': {'rejected': server('mcp-server-git')}})
    write(project / '.claude/settings.local.json', {'disabledMcpjsonServers': ['rejected']})
    result = collect_from_configs(discover_config_files(project), workspace=project)
    assert {c.name for c in result.capabilities} == {'Read'}


def test_auth_and_unrelated_state_not_exported(layout):
    home, project = layout
    definition = server('mcp-server-fetch')
    definition.update(env={'TOKEN': 'dummy-env-secret'}, headers={'Authorization': 'dummy-header-secret'})
    definition['args'] += ['--token', 'dummy-argument-secret']
    write(home / '.claude.json', {'oauthAccount': {'accessToken': 'dummy-auth-secret'}, 'mcpServers': {'fetch': definition}, 'projects': {'/foreign': {'private': 'dummy-other-state'}}})
    result = collect_from_configs(discover_config_files(project), workspace=project)
    output = json.dumps(build_opengraph(result))
    for secret in ['dummy-env-secret', 'dummy-header-secret', 'dummy-argument-secret', 'dummy-auth-secret', 'dummy-other-state']:
        assert secret not in output
    assert 'package:mcp-server-fetch' in output


def test_explicit_config_does_not_merge_nested_or_project(layout):
    home, project = layout
    path = write(home / '.claude.json', {'mcpServers': {'user': server('mcp-server-fetch')}, 'projects': {str(project): {'mcpServers': {'local': server('mcp-server-git')}}}})
    write(project / '.mcp.json', {'mcpServers': {'project': server('mcp-server-commands')}})
    assert {c.name for c in collect_from_config(path).capabilities} == {'Read', 'user'}
    assert {c.name for c in collect_from_config(project / '.mcp.json').capabilities} == {'Read', 'project'}


def test_placeholder_not_expanded(layout):
    home, project = layout
    write(home / '.claude.json', {'mcpServers': {'filesystem': {'command': 'npx', 'args': ['@modelcontextprotocol/server-filesystem', '${PRIVATE_ROOT}']}}})
    result = collect_from_configs(discover_config_files(project), workspace=project)
    cap = next(c for c in result.capabilities if c.name == 'filesystem')
    assert cap.file_access == 'none'
    assert 'placeholders unresolved' in cap.mcp_evidence


def test_config_dir_override_and_missing_files(layout, tmp_path, monkeypatch):
    _, project = layout
    custom = tmp_path / 'custom'
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(custom))
    assert discover_config_files(project) == []
    assert not (project / '.mcp.json').exists()
    path = write(custom / '.claude.json', {'mcpServers': {'fetch': server('mcp-server-fetch')}})
    assert discover_config_files(project) == [path]


def test_disabled_canonical_does_not_fall_back_to_settings(layout):
    home, project = layout
    write(home / '.claude.json', {'mcpServers': {'off': server('mcp-server-fetch')}, 'projects': {str(project): {'disabledMcpServers': ['off']}}})
    write(project / '.claude/settings.json', {'mcpServers': {'off': server('mcp-server-commands')}})
    result = collect_from_configs(discover_config_files(project), workspace=project)
    assert {c.name for c in result.capabilities} == {'Read'}
