"""Shared metadata-only primitives for documented behavior-file discovery."""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from functools import cached_property, lru_cache
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Iterable

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

MAX_READ_BYTES = 64 * 1024
MAX_REFERENCES = 128
EXCLUDED_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "vendor",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "site-packages",
    ".next", "target", ".local", ".cache", ".ruff_cache",
})
_PRIVATE_STATE_TREES = frozenset({
    (".claude", "projects"), (".claude", "worktrees"),
    (".codex", "sessions"), (".codex", "archived_sessions"),
    (".copilot", "session-state"), (".copilot", "session-store"),
})


def _excluded(parts: tuple[str, ...]) -> bool:
    return any(part in EXCLUDED_DIRS for part in parts) or any(
        parts[index:index + 2] in _PRIVATE_STATE_TREES
        or parts[index:index + 3] in {
            (".claude", "plugins", "cache"), (".codex", "plugins", "cache"),
        }
        for index in range(len(parts))
    )


def excluded_path(path: Path, root: Path) -> bool:
    try:
        return _excluded(path.absolute().relative_to(root.absolute()).parts)
    except ValueError:
        return True


def plugin_component(path: Path, root: Path, include_root: bool = True) -> bool:
    """Unconditionally walking plugin contents must not bypass enablement."""
    for directory in path.parents:
        if not directory.is_relative_to(root):
            break
        if directory == root and not include_root:
            break
        if (directory / "plugin.json").is_file() or any((directory / marker / "plugin.json").is_file()
               for marker in (".claude-plugin", ".codex-plugin", ".cursor-plugin")):
            return True
        if directory == root:
            break
    return False


@dataclass(frozen=True)
class SourceRule:
    platform: str
    pattern: str
    scope: str
    source_kind: str
    influence_channel: str
    activation: str
    targets: tuple[str, ...]
    confidence: str = "suspected"
    evidence: str = ""
    metadata_parser: str = ""
    root: str = "workspace"


@dataclass(frozen=True)
class BehaviorFile:
    path: Path
    root: Path
    rule: SourceRule
    name: str = ""
    description: str = ""
    activation: str = ""
    confidence: str = ""
    evidence: str = ""
    origin: str = ""
    precedence: int = 0
    target_pattern: str = ""
    discovery_status: str = "available"
    scope: str = ""


@dataclass(frozen=True)
class DiscoveryContext:
    workspace: Path
    home: Path
    scope: str
    include_plugin_cache: bool = False

    @cached_property
    def project_root(self) -> Path:
        workspace = self.workspace.resolve()
        for directory in (workspace, *workspace.parents):
            if (directory / ".git").is_dir() or (directory / ".git").is_file():
                return directory
            if directory == self.home.resolve():
                break
        return workspace

    @cached_property
    def ancestors(self) -> tuple[Path, ...]:
        root = self.project_root
        workspace = self.workspace.resolve()
        parents = [workspace]
        while parents[-1] != root and parents[-1].parent != parents[-1]:
            parents.append(parents[-1].parent)
        return tuple(reversed(parents))

    def allows(self, scope: str) -> bool:
        return self.scope == "all" or (self.scope == "workspace" and scope == "project") or (
            self.scope == "global" and scope in {"user", "system", "managed"}
        )


def safe_file(path: Path, root: Path) -> bool:
    """Accept files inside their approved root, including only contained links."""
    try:
        relative = path.absolute().relative_to(root.absolute())
        if _excluded(relative.parts):
            return False
        return path.is_file() and path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError, RuntimeError):
        return False


def read_text(path: Path, root: Path) -> str | None:
    if not safe_file(path, root):
        return None
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None
        with path.open("rb") as stream:
            return stream.read(MAX_READ_BYTES).decode("utf-8-sig")
    except (OSError, UnicodeError, ValueError):
        return None


def _json_text(text: str, comments: bool) -> str:
    if not comments:
        return text
    # Strip comments and trailing commas while preserving quoted strings.
    out: list[str] = []
    index = 0
    quoted = False
    while index < len(text):
        char = text[index]
        if quoted:
            out.append(char)
            if char == "\\" and index + 1 < len(text):
                index += 1
                out.append(text[index])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            out.append(char)
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
            out.append("\n")
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("Unclosed comment")
            index = end + 2
            out.append(" ")
            continue
        elif char == "," and re.match(r"(?:\s|//[^\n]*(?:\n|$)|/\*.*?\*/)*[}\]]", text[index + 1:], re.S):
            index += 1
            continue
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _read_json(path: Path, root: Path, comments: bool) -> dict[str, Any] | None:
    text = read_text(path, root)
    if text is None:
        return None
    try:
        value = json.loads(_json_text(text, comments))
        return value if isinstance(value, dict) else None
    except (ValueError, RecursionError):
        return None


def read_json(path: Path, root: Path) -> dict[str, Any] | None:
    return _read_json(path, root, False)


def read_jsonc(path: Path, root: Path) -> dict[str, Any] | None:
    return _read_json(path, root, True)


def read_toml(path: Path, root: Path) -> dict[str, Any] | None:
    text = read_text(path, root)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except (ValueError, RecursionError):
        return None


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    pieces = pattern.replace("\\", "/").split("/")
    expression = ""
    for index, part in enumerate(pieces):
        if part == "**":
            expression += "(?:[^/]+/)*" if index < len(pieces) - 1 else ".*"
        else:
            expression += r"(?i:SKILL\.md)" if part == "SKILL.md" else fnmatch.translate(part)[4:-3]
            if index < len(pieces) - 1:
                expression += "/"
    return re.compile("^" + expression + "$")


def matches_pattern(relative: str, pattern: str) -> bool:
    return _glob_regex(pattern).fullmatch(relative) is not None


def safe_glob(root: Path, pattern: str, *, include_plugin_containers: bool = False) -> list[Path]:
    """Match registered patterns without entering dependencies or linked trees."""
    parts = pattern.replace("\\", "/").split("/")
    if ".." in parts or Path(pattern).is_absolute():
        return []
    prefix: list[str] = []
    for part in parts:
        if any(char in part for char in "*?["):
            break
        prefix.append(part)
    if len(prefix) == len(parts):
        path = root.joinpath(*parts)
        return [path] if safe_file(path, root) and (include_plugin_containers or not plugin_component(path, root, include_root=False)) else []
    start = root.joinpath(*prefix)
    try:
        if _excluded(start.relative_to(root).parts) or not start.is_dir() or not start.resolve().is_relative_to(root.resolve()):
            return []
    except (OSError, RuntimeError):
        return []
    matcher = _glob_regex(pattern)
    found: list[Path] = []
    for directory, dirs, files in os.walk(start, followlinks=False):
        dirs[:] = sorted(name for name in dirs
                         if not _excluded((Path(directory) / name).relative_to(root).parts)
                         and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if matcher.fullmatch(path.relative_to(root).as_posix()) and safe_file(path, root) and (include_plugin_containers or not plugin_component(path, root, include_root=False)):
                found.append(path)
    return found


def apply_rules(context: DiscoveryContext, agent_name: str, rules: Iterable[SourceRule],
                roots: dict[str, Path]) -> list[BehaviorFile]:
    found: list[BehaviorFile] = []
    for rule in rules:
        root = roots.get(rule.root)
        if root is None or agent_name not in rule.targets or not context.allows(rule.scope):
            continue
        found.extend(BehaviorFile(path, root, rule, origin=rule.root)
                     for path in safe_glob(root, rule.pattern))
    return found


def resolve_reference(value: object, declaring: Path, roots: Iterable[Path],
                      variables: dict[str, Path] | None = None) -> Path | None:
    """Resolve only literal local references within explicitly approved roots."""
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value or "://" in value:
        return None
    for name, replacement in (variables or {}).items():
        value = value.replace("${" + name + "}", str(replacement)).replace("$" + name, str(replacement))
    if "$" in value or "`" in value or "%" in value:
        return None
    if value.startswith("~/") and variables and "HOME" in variables:
        value = str(variables["HOME"] / value[2:])
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = declaring.parent / path
    path = Path(os.path.abspath(path))
    return path if any(safe_file(path, root) for root in roots) else None


def local_command_files(command: object, declaring: Path, roots: Iterable[Path],
                        variables: dict[str, Path] | None = None) -> list[Path]:
    """Extract existing local file operands; never run or export command text."""
    if not isinstance(command, str) or len(command) > MAX_READ_BYTES:
        return []
    try:
        tokens = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return []
    tokens = [token.strip('"\'') for token in tokens]
    if not tokens or any(token.lower() in {";", "&&", "||", "|", ">", "<", "-c", "-e", "/c", "-command", "-encodedcommand"} for token in tokens):
        return []
    executable = Path(tokens[0].replace("\\", "/")).name.lower()
    interpreters = {"python", "python3", "python.exe", "node", "node.exe", "bash", "sh", "zsh",
                    "ruby", "perl", "pwsh", "pwsh.exe", "powershell", "powershell.exe", "bun", "deno"}
    value = tokens[0]
    if executable in interpreters or re.fullmatch(r"python3\.\d+", executable):
        index = 1
        while index < min(len(tokens), MAX_REFERENCES):
            token = tokens[index]
            if not token.startswith("-"):
                break
            if token.lower() == "-file" and executable in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
                index += 1
                break
            if token.lower() == "-executionpolicy" and executable in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
                index += 2
                continue
            if token not in {"-u", "-B", "-E", "-I", "-s", "-S", "-O", "-OO", "--no-warnings", "-NoProfile", "-NonInteractive"}:
                return []
            index += 1
        value = tokens[index] if index < len(tokens) else ""
    if os.name == "nt":
        value = value.replace("\\", "/")
    path = resolve_reference(value, declaring, tuple(roots), variables)
    return [path] if path is not None else []
