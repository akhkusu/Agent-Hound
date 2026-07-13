from pathlib import Path
from agenthound.discovery.workspace import find_asset_files, find_source_files


def test_find_agent_instruction_files(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# instructions")
    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "main.py").write_text("code")
    results = find_source_files(tmp_path)
    names = {Path(p).name for p, _ in results}
    assert "CLAUDE.md" in names
    assert "AGENTS.md" in names
    assert "main.py" not in names


def test_find_doc_files(tmp_path):
    (tmp_path / "README.md").write_text("# readme")
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "page.html").write_text("<html/>")
    results = find_source_files(tmp_path)
    names = {Path(p).name for p, _ in results}
    assert "README.md" in names
    assert "notes.txt" in names
    assert "page.html" in names


def test_source_kind_agent_instruction(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("x")
    results = find_source_files(tmp_path)
    kinds = {kind for _, kind in results}
    assert "AgentInstruction" in kinds


def test_source_kind_doc_file(tmp_path):
    (tmp_path / "README.md").write_text("x")
    results = find_source_files(tmp_path)
    assert any(kind == "DocFile" for _, kind in results)


def test_find_ssh_keys(tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n")
    (ssh_dir / "known_hosts").write_text("hosts")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    paths = {Path(p).name for p, _ in results}
    assert "id_rsa" in paths
    assert "known_hosts" not in paths


def test_find_env_files(tmp_path):
    (tmp_path / ".env").write_text("SECRET=x")
    (tmp_path / ".env.local").write_text("LOCAL=y")
    (tmp_path / "config.py").write_text("x = 1")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    names = {Path(p).name for p, _ in results}
    assert ".env" in names
    assert ".env.local" in names
    assert "config.py" not in names


def test_asset_kind_ssh_key(tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_ed25519").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n-----END OPENSSH PRIVATE KEY-----\n"
    )
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert any(kind == "SSHKey" for _, kind in results)


def test_ssh_pubkey_and_config_not_flagged(tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa.pub").write_text("ssh-rsa AAAA...")
    (ssh_dir / "id_notakey").write_text("just some notes, no header")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert not any(kind == "SSHKey" for _, kind in results)


def test_env_template_not_flagged(tmp_path):
    (tmp_path / ".env.example").write_text("SECRET=changeme")
    (tmp_path / ".env.sample").write_text("SECRET=changeme")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert not any(kind == "EnvFile" for _, kind in results)


def test_vendored_dirs_excluded(tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / ".env").write_text("SECRET=x")
    (nm / "README.md").write_text("# vendored")
    assets = find_asset_files(scope="workspace", workspace=tmp_path)
    assert not any("node_modules" in p for p, _ in assets)


def test_asset_kind_env_file(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert any(kind == "EnvFile" for _, kind in results)


def test_git_repo_detected_as_source_code(tmp_path):
    (tmp_path / ".git").mkdir()
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert any(kind == "SourceCode" for _, kind in results)


def test_agent_instruction_in_hidden_dir(tmp_path):
    """AgentInstruction files inside hidden dirs must be found."""
    hidden = tmp_path / ".claude"
    hidden.mkdir()
    (hidden / "CLAUDE.md").write_text("instructions")
    results = find_source_files(tmp_path)
    paths = {Path(p).name for p, _ in results}
    assert "CLAUDE.md" in paths


def test_nested_git_repo_detected(tmp_path):
    """Nested git repos inside workspace must be detected as SourceCode."""
    nested = tmp_path / "submodule"
    nested.mkdir()
    (nested / ".git").mkdir()
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    source_paths = [p for p, k in results if k == "SourceCode"]
    assert any("submodule" in p for p in source_paths)


def test_deep_source_dir_file(tmp_path):
    """Doc files inside nested source dirs (docs/arch/x.md) must be found."""
    docs = tmp_path / "docs" / "architecture"
    docs.mkdir(parents=True)
    (docs / "overview.md").write_text("overview")
    results = find_source_files(tmp_path)
    paths = {Path(p).name for p, _ in results}
    assert "overview.md" in paths


def test_vscode_copilot_instruction_in_hidden_github_dir(tmp_path):
    github = tmp_path / ".github"
    github.mkdir()
    instruction = github / "copilot-instructions.md"
    instruction.write_text("rules")
    results = find_source_files(tmp_path)
    assert (str(instruction), "AgentInstruction") in results


def test_gcloud_adc_requires_gcloud_dir(tmp_path):
    # A stray ADC-named file outside a gcloud dir is not a credential.
    (tmp_path / "application_default_credentials.json").write_text("{}")
    stray = find_asset_files(scope="workspace", workspace=tmp_path)
    assert not any(k == "GCloudCredentials" for _, k in stray)
    gdir = tmp_path / "gcloud"
    gdir.mkdir()
    (gdir / "application_default_credentials.json").write_text("{}")
    real = find_asset_files(scope="workspace", workspace=tmp_path)
    assert any(k == "GCloudCredentials" for _, k in real)


def test_worktree_git_file_detected_as_source_code(tmp_path):
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere/.git/worktrees/x")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert any(k == "SourceCode" and "worktree" in p for p, k in results)


def test_bogus_git_file_not_source_code(tmp_path):
    # A .git file that is not a gitdir pointer must not count as a repo.
    d = tmp_path / "notarepo"
    d.mkdir()
    (d / ".git").write_text("just some text")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert not any(k == "SourceCode" and "notarepo" in p for p, k in results)
