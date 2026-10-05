"""Read reachability tests use only temporary dummy settings and assets."""
import json

import pytest
from click.testing import CliRunner

from agenthound.cli import cli
from agenthound.collectors.assets import collect_assets
from agenthound.collectors.capabilities import collect_from_config, collect_from_configs


def scan(tmp_path, permissions=None, filename='settings.json'):
    workspace = tmp_path / 'workspace'
    workspace.mkdir(exist_ok=True)
    folder = workspace / '.claude'
    folder.mkdir(exist_ok=True)
    config = folder / filename
    config.write_text(json.dumps({} if permissions is None else {'permissions': permissions}))
    (workspace / '.env').write_text('DUMMY=placeholder\n')
    caps = collect_from_config(config)
    assets = collect_assets(caps.capabilities, workspace, 'workspace')
    return workspace, config, caps, assets


@pytest.mark.parametrize('policy,edge,reason', [
    ({}, True, 'default-workspace-read'),
    ({'allow': ['Read']}, True, 'preapproval:Read'),
    ({'allow': ['Read(./.env)']}, True, 'preapproval:'),
    ({'allow': ['Read'], 'deny': ['Read(./.env)']}, False, ''),
    ({'allow': ['Read'], 'ask': ['Read(./.env)']}, True, 'ask:'),
    ({'allow': ['Read'], 'ask': ['Read'], 'defaultMode': 'dontAsk'}, False, ''),
    ({'deny': ['Read([unsupported])']}, False, ''),
    ({'deny': ['Read']}, False, ''),
    ({'deny': ['R*']}, False, ''),
])
def test_permissions(tmp_path, policy, edge, reason):
    _, _, caps, assets = scan(tmp_path, policy)
    assert bool(assets.edges) == edge
    if edge:
        assert assets.edges[0].properties['confidence'] == 'suspected'
        assert reason in assets.edges[0].properties['evidence']
        assert caps.edges[0].properties['confidence'] == 'suspected'


def test_no_permission_settings_still_has_builtin(tmp_path):
    _, _, caps, assets = scan(tmp_path)
    assert caps.capabilities[0].name == 'Read'
    assert caps.capabilities[0].file_readonly
    assert assets.edges


def test_path_scope_and_nested_deny(tmp_path):
    workspace, _, caps, _ = scan(tmp_path, {'deny': ['Read(secrets/**)']})
    nested = workspace / 'nested' / 'secrets'
    nested.mkdir(parents=True)
    (nested / '.env').write_text('DUMMY=placeholder')
    other = workspace / 'secretstuff'
    other.mkdir()
    (other / '.env').write_text('DUMMY=placeholder')
    result = collect_assets(caps.capabilities, workspace, 'workspace')
    reached = {e.end for e in result.edges}
    assert {a.path for a in result.assets if a.objectid in reached} == {str(workspace / '.env'), str(other / '.env')}


def test_other_project_not_reached(tmp_path):
    _, _, caps, _ = scan(tmp_path, {'allow': ['Read']})
    other = tmp_path / 'other'
    other.mkdir()
    (other / '.env').write_text('DUMMY=placeholder')
    assert not collect_assets(caps.capabilities, other, 'workspace').edges


def test_other_agent_not_given_read(tmp_path):
    _, _, caps, assets = scan(tmp_path, {'allow': ['Read']}, 'cursor_mcp.json')
    assert not caps.capabilities
    assert not assets.edges


def test_outside_and_symlink_deny(tmp_path):
    workspace, _, caps, _ = scan(tmp_path)
    outside = tmp_path / '.env'
    outside.write_text('DUMMY=placeholder')
    (workspace / 'linked.env').symlink_to(outside)
    result = collect_assets(caps.capabilities, workspace, 'workspace')
    reached = {e.end for e in result.edges}
    assert not any(a.objectid in reached for a in result.assets if a.name == 'linked.env')
    _, _, caps, _ = scan(tmp_path, {'allow': ['Read'], 'deny': [f'Read(/{outside})']})
    assert len(collect_assets(caps.capabilities, workspace, 'workspace').edges) == 1


def test_settings_anchor(tmp_path):
    _, _, caps, assets = scan(tmp_path, {'deny': ['Read(/.env)']})
    assert not assets.edges
    assert caps.capabilities


def test_mcp_hooks_coexist_and_deduplicate(tmp_path):
    workspace, config, _, _ = scan(tmp_path)
    config.write_text(json.dumps({'mcpServers': {'filesystem': {'command': 'npx', 'args': ['@modelcontextprotocol/server-filesystem', str(workspace)]}}, 'hooks': {'PreToolUse': [{'hooks': [{'type': 'command', 'command': 'echo dummy'}]}]}}))
    result = collect_from_configs([config, config])
    assert {c.cap_kind for c in result.capabilities} == {'BuiltInTool', 'MCPServer', 'ShellHook'}
    assert len(result.capabilities) == 3
    assets = collect_assets(result.capabilities, workspace, 'workspace')
    assert len(assets.edges) == 2
    assert not any(edge.start == next(cap.objectid for cap in result.capabilities if cap.cap_kind == 'ShellHook') for edge in assets.edges)


def test_cli_real_graph(tmp_path, monkeypatch):
    workspace, config, _, _ = scan(tmp_path, {'allow': ['Read']})
    (workspace / 'CLAUDE.md').write_text('Dummy instructions')
    (workspace / 'README.md').write_text('Dummy documentation')
    monkeypatch.setattr('agenthound.cli.check_internet', lambda: False)
    output = tmp_path / 'graph.json'
    result = CliRunner().invoke(cli, ['--config', str(config), '--workspace', str(workspace), '--scope', 'workspace', '-o', str(output)])
    assert result.exit_code == 0, result.output
    graph = json.loads(output.read_text())['graph']
    assert len(graph['nodes']) == 5
    assert [e['kind'] for e in graph['edges']].count('Influences') == 2
    edge = next(e for e in graph['edges'] if e['kind'] == 'CanAccess')
    assert edge['properties']['confidence'] == 'suspected'
    assert 'preapproval:Read' in edge['properties']['evidence']
    assert 'DUMMY=placeholder' not in output.read_text()


@pytest.mark.parametrize('rule', ['Read(./safe/**)', 'Read(/safe/**)'])
def test_allow_path_does_not_extend_to_outside(tmp_path, rule):
    workspace, _, caps, _ = scan(tmp_path, {'allow': [rule]})
    outside = tmp_path / '.env'
    outside.write_text('DUMMY=placeholder')
    (workspace / 'linked.env').symlink_to(outside)
    result = collect_assets(caps.capabilities, workspace, 'workspace')
    reached = {e.end for e in result.edges}
    assert not any(a.objectid in reached for a in result.assets if a.name == 'linked.env')


def test_explicit_absolute_allow_outside_is_still_suspected(tmp_path):
    outside = tmp_path / '.env'
    outside.write_text('DUMMY=placeholder')
    workspace, _, caps, _ = scan(tmp_path, {'allow': [f'Read(/{outside})']})
    (workspace / 'linked.env').symlink_to(outside)
    # The requested spelling must match as well; a target-only allow is insufficient.
    result = collect_assets(caps.capabilities, workspace, 'workspace')
    assert len(result.edges) == 1


def test_missing_config_does_not_create_capability(tmp_path):
    with pytest.raises(ValueError):
        collect_from_config(tmp_path / '.claude' / 'settings.json')


@pytest.mark.parametrize('pattern,blocked', [
    ('Read(.env)', True),
    ('Read(**/.env)', True),
    ('Read(./nested/*.env)', True),
    ('Read(/other/**)', False),
    ('Read(./nest/**)', False),
])
def test_path_patterns(tmp_path, pattern, blocked):
    workspace, _, caps, _ = scan(tmp_path, {'deny': [pattern]})
    folder = workspace / 'nested'
    folder.mkdir()
    target = folder / '.env'
    target.write_text('DUMMY=placeholder')
    result = collect_assets(caps.capabilities, workspace, 'workspace')
    asset = next(a for a in result.assets if a.path == str(target))
    assert any(e.end == asset.objectid for e in result.edges) is not blocked


def test_home_and_user_settings_anchor(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    workspace = tmp_path / 'project'
    workspace.mkdir()
    (workspace / '.env').write_text('DUMMY=placeholder')
    config_dir = tmp_path / '.claude'
    config_dir.mkdir()
    config = config_dir / 'settings.json'
    config.write_text(json.dumps({'permissions': {'deny': ['Read(/.env)']}}))
    caps = collect_from_config(config)
    assert collect_assets(caps.capabilities, workspace, 'workspace').edges
    config.write_text(json.dumps({'permissions': {'deny': ['Read(~/project/.env)']}}))
    caps = collect_from_config(config)
    assert not collect_assets(caps.capabilities, workspace, 'workspace').edges


def test_outside_absolute_allow_and_deny(tmp_path):
    from agenthound.discovery.builtin_read import read_access
    from agenthound.models.nodes import Asset
    outside = tmp_path / '.env'
    outside.write_text('DUMMY=placeholder')
    workspace, _, caps, _ = scan(tmp_path, {'allow': [f'Read(/{outside})']})
    asset = Asset(name='.env', asset_kind='EnvFile', path=str(outside))
    edge = read_access(caps.capabilities[0], asset, workspace)
    assert edge['confidence'] == 'suspected'
    _, _, caps, _ = scan(tmp_path, {'allow': ['Read'], 'deny': [f'Read(/{outside})']})
    assert read_access(caps.capabilities[0], asset, workspace) is None


def test_read_has_no_impact(tmp_path):
    from agenthound.collectors.impact import collect_impact
    _, _, caps, _ = scan(tmp_path, {'allow': ['Read']})
    assert not collect_impact(caps.capabilities, internet_reachable=True).impacts
