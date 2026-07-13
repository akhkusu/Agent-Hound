from agenthound.collectors.assets import collect_assets
from agenthound.models.nodes import Capability


def test_collect_assets_finds_env_file(tmp_path):
    (tmp_path / ".env").write_text("SECRET=x")
    result = collect_assets(capabilities=[], workspace=tmp_path, scope="workspace")
    kinds = {a.asset_kind for a in result.assets}
    assert "EnvFile" in kinds


def test_collect_assets_finds_ssh_key(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n")
    result = collect_assets(capabilities=[], workspace=tmp_path, scope="workspace")
    kinds = {a.asset_kind for a in result.assets}
    assert "SSHKey" in kinds


def test_privileged_cap_gets_can_access_edge(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    cap = Capability(name="bash-hook", cap_kind="ShellHook", has_shell=True)
    result = collect_assets(capabilities=[cap], workspace=tmp_path, scope="workspace")
    edges = [e for e in result.edges if e.kind == "CanAccess"]
    assert len(edges) == len(result.assets)
    for edge in edges:
        assert edge.start == cap.objectid


def test_non_privileged_cap_gets_no_can_access_edge(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    cap = Capability(name="memory-server", cap_kind="MCPServer", has_shell=False)
    result = collect_assets(capabilities=[cap], workspace=tmp_path, scope="workspace")
    # Non-shell, non-filesystem MCPServer -> no CanAccess edges
    edges = [e for e in result.edges if e.kind == "CanAccess"]
    assert len(edges) == 0


def test_asset_path_stored(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    result = collect_assets(capabilities=[], workspace=tmp_path, scope="workspace")
    env_assets = [a for a in result.assets if a.asset_kind == "EnvFile"]
    assert any(".env" in a.path for a in env_assets)


def test_empty_workspace_no_assets(tmp_path):
    result = collect_assets(capabilities=[], workspace=tmp_path, scope="workspace")
    assert result.assets == []
