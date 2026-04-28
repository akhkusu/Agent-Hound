import os
from pathlib import Path
import pytest
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
    (ssh_dir / "id_rsa").write_text("key")
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
    (ssh_dir / "id_ed25519").write_text("key")
    results = find_asset_files(scope="workspace", workspace=tmp_path)
    assert any(kind == "SSHKey" for _, kind in results)


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
