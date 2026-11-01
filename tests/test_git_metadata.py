import subprocess

from agenthound.discovery.git_metadata import (
    inspect_repository,
    inspect_source,
    normalize_remote_url,
)


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _repository(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    source = tmp_path / "AGENTS.md"
    source.write_text("rules")
    _git(tmp_path, "add", "AGENTS.md")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "remote", "add", "origin", "git@github.com:Example/Project.git")
    _git(tmp_path, "config", "branch.main.remote", "origin")
    _git(tmp_path, "config", "branch.main.merge", "refs/heads/main")
    return source


def test_normalize_remote_url_formats_and_removes_credentials():
    assert normalize_remote_url("git@github.com:Org/Repo.git") == "github.com/org/repo"
    assert normalize_remote_url("https://token@github.com/Org/Repo.git?x=1") == "github.com/org/repo"
    assert normalize_remote_url("ssh://git@git.example.com/Org/Repo") == "git.example.com/org/repo"
    assert normalize_remote_url("/srv/repo") is None


def test_inspect_repository_and_clean_source(tmp_path):
    source = _repository(tmp_path)
    repository = inspect_repository(tmp_path)
    source_metadata = inspect_source(source)
    assert repository.remote_repository == "github.com/example/project"
    assert repository.upstream_branch == "main"
    assert len(repository.head_commit) == 40
    assert source_metadata.relative_path == "AGENTS.md"
    assert source_metadata.committed is True
    assert source_metadata.clean is True


def test_modified_source_is_not_clean(tmp_path):
    source = _repository(tmp_path)
    source.write_text("changed")
    metadata = inspect_source(source)
    assert metadata.committed is True
    assert metadata.clean is False


def test_origin_is_recorded_without_branch_upstream(tmp_path):
    _git(tmp_path, "init", "-b", "feature")
    _git(tmp_path, "remote", "add", "origin", "https://github.com/Example/Project.git")
    metadata = inspect_repository(tmp_path)
    assert metadata.remote_repository == "github.com/example/project"
    assert metadata.upstream_branch == ""
