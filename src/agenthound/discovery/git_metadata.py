"""Collect sanitized Git metadata for cross-graph repository matching."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


_SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")


@dataclass(frozen=True)
class RepositoryMetadata:
    remote_repository: str = ""
    upstream_branch: str = ""
    head_commit: str = ""


@dataclass(frozen=True)
class SourceMetadata:
    repository_path: str = ""
    relative_path: str = ""
    committed: bool = False
    clean: bool = False


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _output(path: Path, *args: str) -> str:
    result = _git(path, *args)
    if result is None or result.returncode != 0:
        return ""
    return result.stdout.strip()


def normalize_remote_url(remote: str) -> str | None:
    """Return a credential-free ``host/owner/repository`` identifier."""
    value = remote.strip()
    if not value:
        return None

    host = ""
    path = ""
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https", "ssh", "git"} or not parsed.hostname:
            return None
        host = parsed.hostname
        try:
            port = parsed.port
        except ValueError:
            return None
        if port is not None:
            host = f"{host}:{port}"
        path = parsed.path
    else:
        match = _SCP_REMOTE.fullmatch(value)
        if match is None:
            return None
        host, path = match.groups()

    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None
    owner, repository = parts
    if repository.lower().endswith(".git"):
        repository = repository[:-4]
    if not owner or not repository:
        return None
    return f"{host.casefold()}/{owner.casefold()}/{repository.casefold()}"


def find_repository(path: Path) -> Path | None:
    directory = path if path.is_dir() else path.parent
    root = _output(directory, "rev-parse", "--show-toplevel")
    if not root:
        return None
    return Path(root).resolve()


def inspect_repository(path: Path) -> RepositoryMetadata:
    root = find_repository(path)
    if root is None:
        return RepositoryMetadata()

    head_commit = _output(root, "rev-parse", "HEAD")
    branch = _output(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote_url = ""
    upstream_branch = ""
    if branch:
        remote_name = _output(root, "config", "--get", f"branch.{branch}.remote")
        merge_ref = _output(root, "config", "--get", f"branch.{branch}.merge")
        if remote_name and merge_ref.startswith("refs/heads/"):
            remote_url = _output(root, "remote", "get-url", remote_name)
            if remote_url:
                upstream_branch = merge_ref.removeprefix("refs/heads/")
    if not remote_url:
        remote_url = _output(root, "remote", "get-url", "origin")
    remote_repository = normalize_remote_url(remote_url) or ""
    return RepositoryMetadata(
        remote_repository=remote_repository,
        upstream_branch=upstream_branch,
        head_commit=head_commit,
    )


def inspect_source(path: Path) -> SourceMetadata:
    root = find_repository(path)
    if root is None:
        return SourceMetadata()
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return SourceMetadata()

    committed_result = _git(root, "cat-file", "-e", f"HEAD:{relative}")
    committed = committed_result is not None and committed_result.returncode == 0
    status_result = _git(
        root, "status", "--porcelain=v1", "--untracked-files=all", "--", relative
    )
    clean = (
        committed
        and status_result is not None
        and status_result.returncode == 0
        and not status_result.stdout.strip()
    )
    return SourceMetadata(
        repository_path=str(root),
        relative_path=relative,
        committed=committed,
        clean=clean,
    )
