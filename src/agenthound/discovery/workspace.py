"""File-finding utilities for sources and assets."""

from __future__ import annotations

import os
import re
from pathlib import Path

from agenthound.models.nodes import AssetKind, SourceKind

_AGENT_INSTRUCTION_NAMES = {
    "CLAUDE.md", "AGENTS.md", "AGENTS.override.md", "GEMINI.md", ".cursorrules",
    "SYSTEM_PROMPT.md",
}

_COPILOT_INSTRUCTION_PATH = (".github", "copilot-instructions.md")

_DOC_EXTENSIONS = {".md", ".txt", ".html", ".rst", ".xml"}

_SOURCE_DIRS = {"docs", "context", "prompts", "instructions", "knowledge"}

# Dependency / build / VCS trees: files here are vendored or generated, not
# the project's own untrusted inputs or secrets, so both source and asset
# scanning skip them to cut false positives.
_EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "vendor",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "site-packages",
    ".next", "target",
}

# .env variants that are templates/samples, not real secrets.
_ENV_TEMPLATE_SUFFIXES = {
    "example", "sample", "template", "dist", "tpl", "defaults", "default",
}

_ASSET_RULES: list[tuple[re.Pattern[str], AssetKind]] = [
    (re.compile(r"(^|[/\\])\.ssh[/\\]id_[\w]+$"), "SSHKey"),
    (re.compile(r"(^|[/\\])\.ssh[/\\].*\.(pem|key)$"), "SSHKey"),
    (re.compile(r"(^|[/\\])\.env(\.[^/\\]+)?$"), "EnvFile"),
    (re.compile(r"(^|[/\\])[^/\\]*\.env(\.local)?$"), "EnvFile"),
    (re.compile(r"(^|[/\\])\.aws[/\\]credentials$"), "AwsCredentials"),
    # Require the standard gcloud directory so a stray sample file with this
    # name elsewhere is not treated as a real credential.
    (re.compile(r"(^|[/\\])gcloud[/\\].*application_default_credentials\.json$"), "GCloudCredentials"),
    (re.compile(r"(^|[/\\])\.kube[/\\]config$"), "KubeConfig"),
]

# Non-key files under .ssh that the name-based rule would otherwise catch.
_SSH_NON_KEY_NAMES = {"known_hosts", "known_hosts_old", "config", "authorized_keys"}


def _is_env_template(path: Path) -> bool:
    """True for .env.example / .env.sample / template-style env files."""
    parts = path.name.lower().split(".")
    return any(p in _ENV_TEMPLATE_SUFFIXES for p in parts)


def _looks_like_private_key(path: Path) -> bool:
    """Confirm an SSH-key candidate actually holds a private key header.

    Name-only matching flags things like `.ssh/config` or `id_something.pub`
    (a public key) as secrets. Reading the first line disambiguates. On any
    read error we keep it (fail-safe toward reporting a possible secret).
    """
    if path.name in _SSH_NON_KEY_NAMES or path.suffix == ".pub":
        return False
    try:
        # Read a few KB, not just the first line: some keys carry leading
        # comments / a BOM before the header.
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return True
    return b"PRIVATE KEY" in head or b"PuTTY-User-Key" in head

_GLOBAL_ASSET_ROOTS = [
    Path.home() / ".ssh",
    Path.home() / ".aws",
    Path.home() / ".config" / "gcloud",
    Path.home() / ".kube",
]


def _source_kind(path: Path) -> SourceKind | None:
    if path.name in _AGENT_INSTRUCTION_NAMES:
        return "AgentInstruction"
    if path.suffix in _DOC_EXTENSIONS:
        return "DocFile"
    return None


def _is_agent_instruction(path: Path, workspace: Path) -> bool:
    """Return whether path is a recognized always-on instruction file."""
    if path.name in _AGENT_INSTRUCTION_NAMES:
        return True
    try:
        return path.relative_to(workspace).parts == _COPILOT_INSTRUCTION_PATH
    except ValueError:
        return False


def _asset_kind(path: Path) -> AssetKind | None:
    path_str = str(path)
    for pattern, kind in _ASSET_RULES:
        if pattern.search(path_str):
            if kind == "EnvFile" and _is_env_template(path):
                return None
            if kind == "SSHKey" and not _looks_like_private_key(path):
                return None
            return kind
    return None


def _is_git_repo_root(path: Path) -> bool:
    """True if `path` holds a git repo: a `.git` directory, or a `.git` file
    whose first line is a `gitdir:` pointer (worktree / submodule)."""
    git = path / ".git"
    if git.is_dir():
        return True
    if git.is_file():
        try:
            with open(git, "rb") as f:
                return f.read(8).startswith(b"gitdir:")
        except OSError:
            return False
    return False


def _in_source_scope(root_path: Path, workspace: Path) -> bool:
    """True if root_path is workspace root or under a source directory."""
    if root_path == workspace:
        return True
    try:
        rel_parts = root_path.relative_to(workspace).parts
    except ValueError:
        return False
    return any(part in _SOURCE_DIRS for part in rel_parts)


def find_source_files(workspace: Path) -> list[tuple[str, SourceKind]]:
    """Return (path_str, source_kind) for IPI-injectable files in workspace."""
    results: list[tuple[str, SourceKind]] = []
    seen: set[str] = set()

    # Pass 1: walk all dirs (including hidden) for AgentInstruction files only,
    # skipping vendored/build trees.
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        root_path = Path(root)
        for fname in files:
            fpath = root_path / fname
            if _is_agent_instruction(fpath, workspace):
                key = str(fpath)
                if key not in seen:
                    results.append((key, "AgentInstruction"))
                    seen.add(key)

    # Pass 2: walk non-hidden dirs for DocFile types
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _EXCLUDED_DIRS]
        root_path = Path(root)
        in_source_dir = root_path == workspace or _in_source_scope(root_path, workspace)
        for fname in files:
            fpath = root_path / fname
            if _is_agent_instruction(fpath, workspace):
                continue  # already handled in pass 1
            if in_source_dir and fpath.suffix in _DOC_EXTENSIONS:
                key = str(fpath)
                if key not in seen:
                    results.append((key, "DocFile"))
                    seen.add(key)

    return results


def find_asset_files(scope: str, workspace: Path | None = None) -> list[tuple[str, AssetKind]]:
    """Return (path_str, asset_kind) for sensitive files.

    scope: 'workspace' | 'global' | 'all'
    """
    results: list[tuple[str, AssetKind]] = []
    seen: set[str] = set()

    def _scan(root: Path) -> None:
        # Check the root itself for git repo
        if _is_git_repo_root(root):
            key = str(root)
            if key not in seen:
                results.append((key, "SourceCode"))
                seen.add(key)

        for dirpath, dirs, files in os.walk(root):
            dp = Path(dirpath)
            # Detect nested git repos before pruning. A worktree / submodule has
            # `.git` as a file (a `gitdir:` pointer), not a directory.
            if _is_git_repo_root(dp) and str(dp) not in seen:
                results.append((str(dp), "SourceCode"))
                seen.add(str(dp))
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
            for fname in files:
                fpath = dp / fname
                kind = _asset_kind(fpath)
                if kind:
                    key = str(fpath)
                    if key not in seen:
                        results.append((key, kind))
                        seen.add(key)

    if scope in ("workspace", "all") and workspace:
        _scan(workspace)

    if scope in ("global", "all"):
        for root in _GLOBAL_ASSET_ROOTS:
            if root.exists():
                _scan(root)
        # Also check home dir top level for .env files
        for fpath in Path.home().iterdir():
            if fpath.is_file():
                kind = _asset_kind(fpath)
                if kind:
                    key = str(fpath)
                    if key not in seen:
                        results.append((key, kind))
                        seen.add(key)

    return results
