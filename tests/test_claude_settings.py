"""Discovery and effective Read policy tests with entirely isolated settings."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.collectors.assets import collect_assets
from agenthound.collectors.capabilities import collect_from_configs
from agenthound.discovery.config_parser import discover_config_files


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    project = tmp_path / 'project'
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False)
    monkeypatch.setattr('agenthound.discovery.config_parser._DISCOVERY_CANDIDATES', [])
    (project / '.env').write_text('DUMMY_VALUE=placeholder')
    (project / 'README.md').write_text('Dummy readme')
    (project / 'CLAUDE.md').write_text('Dummy instructions')
    return home, project


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    return path


def files(home, project, user, shared, local):
    paths = [home / '.claude/settings.json', project / '.claude/settings.json', project / '.claude/settings.local.json']
    for path, policy in zip(paths, [user, shared, local]):
        if policy is not None:
            write(path, {'permissions': policy})
    return paths


def test_discovery_missing_files_and_no_creation(layout):
    home, project = layout
    assert discover_config_files(project) == []
    assert not (project / '.claude').exists()
    paths = files(home, project, {}, None, {})
    found = discover_config_files(project)
    assert found == [paths[0], paths[2]]
    assert not paths[1].exists()


@pytest.mark.parametrize('user,shared,local,decision', [
    ({'deny': ['Read(.env)']}, {'allow': ['Read']}, {'allow': ['Read']}, None),
    ({'allow': ['Read']}, {'deny': ['Read(.env)']}, {'allow': ['Read']}, None),
    ({'ask': ['Read(.env)']}, {'allow': ['Read']}, {'allow': ['Read']}, 'ask:'),
    ({'allow': ['Read']}, {'ask': ['Read(.env)']}, {'allow': ['Read']}, 'ask:'),
    ({'ask': ['Read'], 'defaultMode': 'dontAsk'}, {}, {'defaultMode': 'default'}, 'ask:'),
    ({'ask': ['Read'], 'defaultMode': 'default'}, {}, {'defaultMode': 'dontAsk'}, None),
])
def test_policy_precedence(layout, user, shared, local, decision):
    home, project = layout
    paths = files(home, project, user, shared, local)
    caps = collect_from_configs(discover_config_files(project), workspace=project)
    assert len(caps.agents) == 1
    assert len(caps.capabilities) == 1
    assert len(caps.edges) == 1
    result = collect_assets(caps.capabilities, project, 'workspace')
    assert bool(result.edges) == (decision is not None)
    if decision:
        props = result.edges[0].properties
        assert props['confidence'] == 'suspected'
        assert decision in props['evidence']
        assert all(str(p) in props['evidence'] for p in paths)
        assert 'managed policy' in props['evidence']


def test_whole_tool_deny_survives_local_allow(layout):
    home, project = layout
    files(home, project, {'deny': ['Read']}, {}, {'allow': ['Read']})
    result = collect_from_configs(discover_config_files(project), workspace=project)
    assert len(result.agents) == 1
    assert not result.capabilities


def test_settings_relative_anchor_preserved(layout):
    home, project = layout
    files(home, project, {'deny': ['Read(/.env)']}, {}, {})
    caps = collect_from_configs(discover_config_files(project), workspace=project)
    assert collect_assets(caps.capabilities, project, 'workspace').edges
    write(project / '.claude/settings.local.json', {'permissions': {'deny': ['Read(/.env)']}})
    caps = collect_from_configs(discover_config_files(project), workspace=project)
    assert not collect_assets(caps.capabilities, project, 'workspace').edges


def test_other_workspace_not_inherits_project_policy(layout, tmp_path):
    home, project = layout
    files(home, project, {}, {'allow': ['Read']}, {})
    caps = collect_from_configs(discover_config_files(project), workspace=project)
    other = tmp_path / 'other'
    other.mkdir()
    (other / '.env').write_text('DUMMY=placeholder')
    assert not collect_assets(caps.capabilities, other, 'workspace').edges
    other_caps = collect_from_configs(discover_config_files(other), workspace=other)
    assert all(str(project) not in c.file_evidence for c in other_caps.capabilities)
    assert other_caps.agents[0].objectid != caps.agents[0].objectid


def test_discover_and_single_file_cli(layout, monkeypatch, tmp_path):
    home, project = layout
    paths = files(home, project, {'deny': ['Read(.env)']}, {}, {'allow': ['Read']})
    monkeypatch.setattr('agenthound.cli.check_internet', lambda: False)
    output = tmp_path / 'graph.json'
    runner = CliRunner()
    result = runner.invoke(cli, ['discover', '-w', str(project), '--scope', 'workspace', '-o', str(output)])
    assert result.exit_code == 0, result.output
    graph = json.loads(output.read_text())['graph']
    assert sum('Agent' in n['kinds'] for n in graph['nodes']) == 1
    assert sum(n['properties'].get('cap_kind') == 'BuiltInTool' for n in graph['nodes']) == 1
    assert not any(e['kind'] == 'CanAccess' for e in graph['edges'])
    assert len({n['id'] for n in graph['nodes']}) == len(graph['nodes'])
    result = runner.invoke(cli, ['-c', str(paths[2]), '-w', str(project), '--scope', 'workspace', '-o', str(output)])
    assert result.exit_code == 0, result.output
    graph = json.loads(output.read_text())['graph']
    assert sum(e['kind'] == 'CanAccess' for e in graph['edges']) == 1


def test_other_agents_mcp_hooks_and_duplicate_input(layout):
    home, project = layout
    paths = files(home, project, {}, {}, {})
    write(paths[0], {'mcpServers': {'filesystem': {'command': 'npx', 'args': ['@modelcontextprotocol/server-filesystem', str(project)]}}})
    write(paths[1], {'hooks': {'PreToolUse': [{'hooks': [{'type': 'command', 'command': 'echo dummy'}]}]}})
    cursor = write(home / 'cursor_mcp.json', {'mcpServers': {'memory': {'command': 'npx', 'args': ['@modelcontextprotocol/server-memory']}}})
    result = collect_from_configs([*paths, *paths, cursor], workspace=project)
    assert len(result.agents) == 2
    assert {c.cap_kind for c in result.capabilities} == {'BuiltInTool', 'MCPServer', 'ShellHook'}
    assert len(result.capabilities) == 4
    assert len(result.edges) == 4
    assert len({c.objectid for c in result.capabilities}) == 4


def test_config_dir_override(layout, monkeypatch, tmp_path):
    _, project = layout
    custom = tmp_path / 'custom-user'
    path = write(custom / 'settings.json', {'permissions': {'ask': ['Read']}})
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(custom))
    assert discover_config_files(project) == [path]
    caps = collect_from_configs([path], workspace=project)
    result = collect_assets(caps.capabilities, project, 'workspace')
    assert 'ask:Read' in result.edges[0].properties['evidence']
