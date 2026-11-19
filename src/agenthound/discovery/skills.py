"""Discover Agent Skills without exporting their instruction bodies."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from agenthound.discovery.behavior_files import read_text, safe_file


_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_NAME_LENGTH = 200
_MAX_DESCRIPTION_LENGTH = 1000
_EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "vendor",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "site-packages",
}


@dataclass(frozen=True)
class SkillInfo:
    path: Path
    name: str
    description: str
    scope: str
    origin: str


def _user_home() -> Path:
    return Path.home()


def _clean_scalar(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return " ".join(value.split())[:limit]


def _frontmatter_metadata(path: Path) -> tuple[str, str]:
    """Read only name/description from bounded, simple YAML front matter."""
    text = read_text(path, path.parent)
    if text is None:
        return path.parent.name, ""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return path.parent.name, ""
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return path.parent.name, ""

    values: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        if ":" not in line or line[:1].isspace():
            index += 1
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key not in {"name", "description"}:
            index += 1
            continue
        if value in {"|", ">", "|-", ">-", "|+", ">+"}:
            block: list[str] = []
            index += 1
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                block.append(lines[index].strip())
                index += 1
            value = " ".join(part for part in block if part)
            values[key] = value
            continue
        values[key] = value
        index += 1

    name = _clean_scalar(values.get("name", path.parent.name), _MAX_NAME_LENGTH)
    description = _clean_scalar(values.get("description", ""), _MAX_DESCRIPTION_LENGTH)
    return name or path.parent.name, description


def skill_info(path: Path, scope: str, origin: str) -> SkillInfo:
    name, description = _frontmatter_metadata(path)
    return SkillInfo(
        path=path,
        name=name,
        description=description,
        scope=scope,
        origin=origin,
    )


def _skill_manifests(root: Path, origin: str) -> list[SkillInfo]:
    if not root.is_dir():
        return []
    skills: list[SkillInfo] = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in _EXCLUDED_DIRS]
        for filename in files:
            if filename.lower() != "skill.md":
                continue
            path = Path(directory) / filename
            if not safe_file(path, root):
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            scope = "system" if relative.parts and relative.parts[0] == ".system" else "user"
            skills.append(skill_info(path, scope, origin))
    return skills


def find_user_skills(agent_name: str) -> list[SkillInfo]:
    """Return user/system skills available to the selected agent."""
    home = _user_home()
    roots: list[tuple[Path, str]] = []
    if agent_name == "codex":
        codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
        roots.extend([
            (home / ".agents" / "skills", "agents-user"),
            (codex_home / "skills", "codex-user"),
        ])
    elif agent_name == "claude-code":
        claude_home = Path(
            os.environ.get("CLAUDE_CONFIG_DIR", str(home / ".claude"))
        ).expanduser()
        roots.append((claude_home / "skills", "claude-user"))

    found: list[SkillInfo] = []
    seen: set[Path] = set()
    for root, origin in roots:
        for skill in _skill_manifests(root, origin):
            try:
                resolved = skill.path.resolve()
            except OSError:
                resolved = skill.path.absolute()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(skill)
    return found
