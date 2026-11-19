"""Documented Claude Code behavior files, discovered without executing them."""

from __future__ import annotations

from dataclasses import replace
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any

from agenthound.discovery.behavior_files import (
    BehaviorFile,
    DiscoveryContext,
    MAX_REFERENCES,
    SourceRule,
    read_json,
    read_text,
    resolve_reference,
    safe_file,
    safe_glob,
)

_TARGETS = ("claude-code",)
_DOCS = "https://code.claude.com/docs/en/"
_MAX_IMPORT_DEPTH = 4


def _rule(pattern: str, scope: str, kind: str, channel: str, activation: str,
          *, root: str = "workspace", confidence: str = "suspected",
          parser: str = "", page: str = "memory") -> SourceRule:
    return SourceRule("Claude Code", pattern, scope, kind, channel, activation, _TARGETS,
                      confidence, _DOCS + page, parser, root)


_STATIC_RULES = (
    _rule("CLAUDE.md", "project", "AgentInstruction", "persistent_instruction", "automatic", confidence="confirmed", parser="imports", root="ancestor"),
    _rule(".claude/CLAUDE.md", "project", "AgentInstruction", "persistent_instruction", "automatic", confidence="confirmed", parser="imports", root="ancestor"),
    _rule("CLAUDE.local.md", "project", "AgentInstruction", "persistent_instruction", "automatic", confidence="confirmed", parser="imports", root="ancestor"),
    _rule("**/CLAUDE.md", "project", "AgentInstruction", "path_scoped_instruction", "conditional", parser="imports"),
    _rule("**/CLAUDE.local.md", "project", "AgentInstruction", "path_scoped_instruction", "conditional", parser="imports"),
    _rule(".claude/rules/**/*.md", "project", "AgentInstruction", "persistent_instruction", "automatic", confidence="confirmed", parser="rule", root="ancestor"),
    _rule("**/.claude/rules/**/*.md", "project", "AgentInstruction", "path_scoped_instruction", "conditional", parser="rule"),
    _rule(".claude/commands/**/*.md", "project", "PromptFile", "reusable_prompt", "on_demand", parser="frontmatter", page="skills", root="ancestor"),
    _rule(".claude/agents/**/*.md", "project", "AgentDefinition", "agent_definition", "on_demand", parser="frontmatter", page="sub-agents", root="ancestor"),
    _rule(".claude/skills/**/SKILL.md", "project", "Skill", "skill", "on_demand", parser="frontmatter", page="skills", root="ancestor"),
    _rule("**/.claude/skills/**/SKILL.md", "project", "Skill", "skill", "on_demand", parser="frontmatter", page="skills"),
    _rule(".claude/output-styles/**/*.md", "project", "OutputStyle", "persistent_instruction", "on_demand", parser="style", page="output-styles", root="ancestor"),
    _rule(".claude/settings.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", parser="settings", page="settings"),
    _rule(".claude/settings.local.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", parser="settings", page="settings"),
    _rule(".mcp.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", page="mcp"),
    _rule("CLAUDE.md", "user", "AgentInstruction", "persistent_instruction", "automatic", root="claude", confidence="confirmed", parser="imports"),
    _rule("rules/**/*.md", "user", "AgentInstruction", "persistent_instruction", "automatic", root="claude", confidence="confirmed", parser="rule"),
    _rule("commands/**/*.md", "user", "PromptFile", "reusable_prompt", "on_demand", root="claude", parser="frontmatter", page="skills"),
    _rule("agents/**/*.md", "user", "AgentDefinition", "agent_definition", "on_demand", root="claude", parser="frontmatter", page="sub-agents"),
    _rule("skills/**/SKILL.md", "user", "Skill", "skill", "on_demand", root="claude", parser="frontmatter", page="skills"),
    _rule("output-styles/**/*.md", "user", "OutputStyle", "persistent_instruction", "on_demand", root="claude", parser="style", page="output-styles"),
    _rule("settings.json", "user", "RuntimeConfiguration", "runtime_configuration", "configured", root="claude", parser="settings", page="settings"),
    _rule(".claude.json", "user", "RuntimeConfiguration", "runtime_configuration", "configured", root="state", page="settings"),
    _rule("CLAUDE.md", "managed", "AgentInstruction", "persistent_instruction", "automatic", root="managed", confidence="confirmed", parser="imports"),
    _rule("managed-settings.json", "managed", "RuntimeConfiguration", "runtime_configuration", "configured", root="managed", parser="settings", page="managed-settings"),
    _rule("managed-settings.d/*.json", "managed", "RuntimeConfiguration", "runtime_configuration", "configured", root="managed", parser="settings", page="managed-settings"),
    _rule("managed-mcp.json", "managed", "RuntimeConfiguration", "runtime_configuration", "configured", root="managed", page="managed-settings"),
    _rule(".claude/output-styles/**/*.md", "managed", "OutputStyle", "persistent_instruction", "on_demand", root="managed", parser="style", page="output-styles"),
    _rule(".claude/skills/**/SKILL.md", "managed", "Skill", "skill", "on_demand", root="managed", parser="frontmatter", page="skills"),
)
_IMPORT_RULE = _rule("@path", "project", "AgentInstruction", "persistent_instruction", "automatic", parser="imports", root="reference")
_MEMORY_RULE = _rule("MEMORY.md", "user", "Memory", "memory", "automatic", confidence="confirmed", root="memory")
_TOPIC_RULE = _rule("*.md", "user", "Memory", "memory", "on_demand", root="memory")
_EXPLICIT_MEMORY_RULE = _rule("@<literal-project-memory-file>.md", "user", "Memory", "memory", "configured", root="reference")
_HOOK_RULE = _rule("<referenced-hook-script>", "project", "HookScript", "hook_execution", "conditional", root="reference", page="hooks")
_PLUGIN_RULE = _rule(".claude-plugin/plugin.json", "project", "PluginManifest", "plugin", "configured", root="plugin", page="plugins/manifest-reference")
_AGENTS_RULE = _rule("AGENTS.md", "project", "AgentInstruction", "persistent_instruction", "automatic", parser="imports", root="reference")
_SUBAGENT_MEMORY_RULE = _rule("<configured-subagent-memory>/*.md", "project", "Memory", "memory", "conditional", root="memory", page="sub-agents")
_PLUGIN_COMPONENTS = (
    ("skills", "skills", "Skill", "skill", "on_demand"),
    ("commands", "commands", "PromptFile", "reusable_prompt", "on_demand"),
    ("agents", "agents", "AgentDefinition", "agent_definition", "on_demand"),
    ("outputStyles", "output-styles", "OutputStyle", "persistent_instruction", "on_demand"),
    ("hooks", "hooks/hooks.json", "RuntimeConfiguration", "runtime_configuration", "configured"),
    ("mcpServers", ".mcp.json", "RuntimeConfiguration", "runtime_configuration", "configured"),
    ("lspServers", ".lsp.json", "RuntimeConfiguration", "runtime_configuration", "configured"),
)
RULES = (*_STATIC_RULES, _IMPORT_RULE, _MEMORY_RULE, _TOPIC_RULE, _EXPLICIT_MEMORY_RULE, _HOOK_RULE, _PLUGIN_RULE, _AGENTS_RULE, _SUBAGENT_MEMORY_RULE,
         *(_rule(default, "project", kind, channel, activation, root="plugin", page="plugins/manifest-reference")
           for _, default, kind, channel, activation in _PLUGIN_COMPONENTS))


def _claude_home(context: DiscoveryContext) -> Path:
    value = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(value).expanduser() if value else context.home / ".claude"


def _managed_root() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "ClaudeCode"
    return Path("/etc/claude-code")


def _frontmatter(text: str | None) -> tuple[dict[str, str], str]:
    """Read only a few scalar selectors, never arbitrary YAML or instruction text."""
    if text is None:
        return {}, "metadata_unavailable"
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "available"
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        return {}, "metadata_invalid"
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"name", "paths", "force-for-plugin", "memory"}:
            fields[key] = value.strip().strip("\"'")
    # A paths declaration is enough to retain conditional confidence. A YAML
    # list is not exported here; other YAML fields can hold secret values.
    return fields, "available"


def _settings(context: DiscoveryContext, claude: Path, managed: Path) -> list[tuple[Path, Path, str, dict[str, Any]]]:
    candidates = [(claude / "settings.json", claude, "user")]
    candidates.extend((context.workspace / ".claude" / name, context.project_root, "project")
                      for name in ("settings.json", "settings.local.json"))
    repository = _repository_identity(context)
    try:
        root_local = sys.platform != "win32" and repository != context.home and all(
            path.stat().st_uid == os.getuid() for path in (repository, repository / ".git", repository / ".claude")
        )
    except (OSError, AttributeError):
        root_local = False
    if root_local and repository != context.workspace:
        candidates.append((repository / ".claude/settings.local.json", repository, "project"))
    candidates.append((managed / "managed-settings.json", managed, "managed"))
    candidates.extend((path, managed, "managed") for path in safe_glob(managed, "managed-settings.d/*.json"))
    return [(path, root, scope, data) for path, root, scope in candidates
            if (data := read_json(path, root)) is not None]


def _agents_files(context: DiscoveryContext, settings: dict[str, Any], items: list[BehaviorFile]) -> list[BehaviorFile]:
    if not context.allows("project"):
        return []
    mode = settings.get("instructionFiles", "claude-md-or-agents-md")
    enabled = settings.get("enabledPlugins", {})
    if mode in {"claude-md", "managed-only"} or any(
        enabled.get(key) is False for key in ("cc-plugin-agents-md@builtin", "agents-md@builtin")
    ):
        return []
    launch_instructions = any(item.rule.scope == "project" and item.rule.metadata_parser == "imports"
                              and item.rule.activation == "automatic" for item in items)
    if mode != "claude-md-and-agents-md" and launch_instructions:
        return []
    paths = [path for root in context.ancestors for pattern in ("AGENTS.md", ".claude/AGENTS.md")
             for path in safe_glob(root, pattern)]
    paths.extend(safe_glob(context.workspace, "**/AGENTS.md"))
    found = []
    for path in paths:
        relative = path.relative_to(context.project_root)
        if ".agents" in relative.parts:
            continue
        directory = path.parent.parent if path.parent.name == ".claude" else path.parent
        automatic = directory in context.ancestors
        if not automatic and mode != "claude-md-and-agents-md" and any(
            safe_file(directory / name, context.project_root)
            for name in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
        ):
            continue
        found.append(BehaviorFile(path, context.project_root, _AGENTS_RULE,
                                  activation="automatic" if automatic else "conditional",
                                  confidence="suspected", origin="claude-agents-fallback",
                                  evidence="Documented AGENTS.md mode; Claude Code version and built-in plugin availability unverified"))
    return found


def _metadata(item: BehaviorFile, settings: dict[str, Any]) -> BehaviorFile:
    parser = item.rule.metadata_parser
    if parser not in {"imports", "rule", "frontmatter", "style", "settings"}:
        return item
    if parser == "settings":
        return item if read_json(item.path, item.root) is not None else replace(item, discovery_status="metadata_unavailable")
    text = read_text(item.path, item.root)
    frontmatter, status = _frontmatter(text)
    updates: dict[str, Any] = {"discovery_status": status}
    if parser == "rule" and ("paths" in frontmatter or status != "available"):
        updates.update(activation="conditional", confidence="suspected")
        # Do not export free-form rule strings. Presence is enough to explain
        # that matching a target file is required.
    if parser == "style":
        name = frontmatter.get("name", item.path.stem)
        if settings.get("outputStyle") == name:
            updates["activation"] = "configured"
        elif item.origin == "plugin" and frontmatter.get("force-for-plugin") == "true":
            updates["activation"] = "automatic"
    return replace(item, **updates)


def _static_files(context: DiscoveryContext, claude: Path, managed: Path) -> list[BehaviorFile]:
    found: dict[Path, BehaviorFile] = {}
    state = claude if os.environ.get("CLAUDE_CONFIG_DIR") else context.home
    roots = {"workspace": context.workspace, "claude": claude, "managed": managed, "state": state}
    for rule in _STATIC_RULES:
        if not context.allows(rule.scope):
            continue
        candidates = context.ancestors if rule.root == "ancestor" else (roots[rule.root],)
        for index, root in enumerate(candidates):
            for path in safe_glob(root, rule.pattern):
                # The parent root is the approval boundary for ancestor files.
                boundary = context.project_root if rule.scope == "project" else root
                item = BehaviorFile(path, boundary, rule, origin="claude-" + rule.root, precedence=index)
                old = found.get(path.resolve())
                if old is None or (rule.confidence == "confirmed" and old.rule.confidence != "confirmed"):
                    found[path.resolve()] = item
    return list(found.values())


def _imports(text: str) -> list[str]:
    # Markdown code and HTML comments are literal, not imports.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if match:
            marker = match[1]
            if fence is None:
                fence = (marker[0], len(marker))
            elif marker[0] == fence[0] and len(marker) >= fence[1]:
                fence = None
            continue
        if fence is None:
            lines.append(re.sub(r"(`+).*?\1", "", line))
    return [match[1].replace("\\ ", " ").replace("\\\t", "\t")
            for match in re.finditer(r"(?<![\w@])@((?:\\[ \t]|[^\s\"'`<>])+)", "\n".join(lines))][:MAX_REFERENCES]


def _instruction_excluded(path: Path, context: DiscoveryContext, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path.as_posix(), pattern)
               or (path.is_relative_to(context.project_root) and fnmatch.fnmatch(path.relative_to(context.project_root).as_posix(), pattern))
               for pattern in patterns)


def _private_state_reference(path: Path, claude: Path) -> bool:
    try:
        candidates = (Path(os.path.abspath(path)), path.resolve())
        roots = (Path(os.path.abspath(claude)), claude.resolve())
        return any(candidate.is_relative_to(root / name) for candidate in candidates for root in roots
                   for name in ("projects", "worktrees", "plugins/cache", "plugins/.trash"))
    except (OSError, RuntimeError):
        return True


def _explicit_memory_reference(reference: str, declaring: Path, context: DiscoveryContext,
                               claude: Path) -> tuple[Path, Path] | None:
    """Authorize one literal memory file, never a project-state directory walk."""
    if not context.allows("user") or not reference or len(reference) > 4096 or any(
        marker in reference for marker in ("\x00", "://", "$", "`", "%")
    ):
        return None
    if reference.startswith("~/"):
        reference = str(context.home / reference[2:])
    path = Path(reference)
    if not path.is_absolute():
        path = declaring.parent / path
    path = Path(os.path.abspath(path))
    claude = Path(os.path.abspath(claude))
    try:
        parts = path.relative_to(claude).parts
        if len(parts) != 4 or parts[0] != "projects" or parts[2] != "memory" or path.suffix != ".md":
            return None
        root = claude / "projects" / parts[1] / "memory"
        if any(directory.is_symlink() for directory in (root, root.parent, root.parent.parent)):
            return None
        if safe_file(path, root) and root.resolve().is_relative_to(claude.resolve()):
            return path, root
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def _imported_files(items: list[BehaviorFile], context: DiscoveryContext, claude: Path,
                    exclusions: list[str]) -> list[BehaviorFile]:
    roots = [context.project_root]
    if context.allows("user"):
        roots.extend((claude, context.home))
    managed_roots = {item.root for item in items if item.rule.scope == "managed"}
    roots.extend(root for root in managed_roots if root not in roots)
    found: list[BehaviorFile] = []
    queue = [(item, 0) for item in items if item.rule.metadata_parser in {"imports", "rule"}]
    visited: set[Path] = set()
    while queue and len(visited) < MAX_REFERENCES:
        item, depth = queue.pop(0)
        canonical = item.path.resolve()
        if canonical in visited or depth >= _MAX_IMPORT_DEPTH:
            continue
        visited.add(canonical)
        text = read_text(item.path, item.root)
        if text is None:
            continue
        for reference in _imports(text):
            memory = _explicit_memory_reference(reference, item.path, context, claude)
            if memory is not None:
                memory_path, memory_root = memory
                if memory_path.resolve() not in visited and not _instruction_excluded(memory_path, context, exclusions):
                    found.append(BehaviorFile(
                        memory_path, memory_root, _EXPLICIT_MEMORY_RULE, scope="user", origin="claude-explicit-memory-import",
                        activation=item.activation or item.rule.activation, confidence="suspected",
                        evidence="Explicit literal @path memory reference; external approval and runtime use unverified; content not read",
                    ))
                    visited.add(memory_path.resolve())
                    if len(found) >= MAX_REFERENCES:
                        return found
                continue
            if reference.startswith("~/"):
                reference = str(context.home / reference[2:])
            path = resolve_reference(reference, item.path, roots)
            if path is None or path.resolve() in visited or _instruction_excluded(path, context, exclusions) or _private_state_reference(path, claude):
                continue
            root = next(root for root in roots if safe_file(path, root))
            scope = "project" if root == context.project_root else ("managed" if root in managed_roots else "user")
            if not context.allows(scope):
                continue
            external = root != item.root
            imported = BehaviorFile(
                path, root, _IMPORT_RULE, scope=scope, origin="claude-import",
                activation=item.activation or item.rule.activation,
                confidence="suspected" if external else (item.confidence or item.rule.confidence),
                evidence="Documented @path import; external approval unverified" if external else "Documented @path import from an applicable instruction file",
            )
            found.append(imported)
            queue.append((imported, depth + 1))
            if len(found) >= MAX_REFERENCES:
                return found
    return found


def _repository_identity(context: DiscoveryContext) -> Path:
    """Recognize standard Git worktree pointers without spawning git."""
    root = context.project_root
    git = root / ".git"
    try:
        if not git.is_symlink() and git.is_file() and git.stat().st_size <= 4096:
            with git.open("rb") as stream:
                pointer = stream.read(4096).decode("utf-8").strip()
            if pointer.startswith("gitdir: "):
                directory = Path(pointer[8:])
                if not directory.is_absolute():
                    directory = root / directory
                directory = directory.resolve()
                common = directory / "commondir"
                if directory.parent.name == "worktrees" and directory.parent.parent.name == ".git" and not common.is_symlink() and common.stat().st_size <= 4096:
                    with common.open("rb") as stream:
                        if stream.read(4096).strip() == b"../..":
                            return directory.parent.parent.parent
    except (OSError, ValueError, UnicodeError, RuntimeError):
        pass
    return root


def _memory_files(context: DiscoveryContext, claude: Path, settings: dict[str, Any]) -> list[BehaviorFile]:
    if not context.allows("user") or settings.get("autoMemoryEnabled") is False or os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1":
        return []
    if settings.get("memoryDirectoryProject") and settings.get("blockReadsOutsideWorkingDirectories") is True:
        return []
    custom = settings.get("autoMemoryDirectory")
    if custom is not None and (not isinstance(custom, str) or not (Path(custom).is_absolute() or custom.startswith("~/"))):
        return []
    if isinstance(custom, str) and (Path(custom).is_absolute() or custom.startswith("~/")):
        root = context.home / custom[2:] if custom.startswith("~/") else Path(custom)
        if root.is_symlink():
            return []
        custom_memory = True
    else:
        key = os.environ.get("CLAUDE_CODE_PROJECT_DIR_NAME") if os.environ.get("CLAUDE_CONFIG_DIR") else None
        if key is None:
            key = re.sub(r"[^a-zA-Z0-9]", "-", str(_repository_identity(context).resolve()))
            # The documented >200-character hash is intentionally not guessed.
            if len(key) > 200:
                return []
        if not key or Path(key).name != key or key in {".", ".."} or "\\" in key:
            return []
        root = claude / "projects" / key / "memory"
        custom_memory = False
        if any(path.is_symlink() for path in (claude / "projects", root.parent, root)):
            return []
    # Approve only this exact directory. Never enumerate projects or transcripts.
    found = []
    for path in safe_glob(root, "*.md"):
        rule = _MEMORY_RULE if path.name == "MEMORY.md" else _TOPIC_RULE
        found.append(BehaviorFile(
            path, root, rule, origin="claude-auto-memory",
            confidence="suspected" if custom_memory else rule.confidence,
            evidence="Explicit autoMemoryDirectory; workspace trust and runtime memory enablement unverified" if custom_memory else "Selected repository auto-memory; index available at session start, topics read on demand",
        ))
    return found


def _command_paths(hook: dict[str, Any], context: DiscoveryContext, roots: list[Path],
                   variables: dict[str, Path]) -> list[Path]:
    command = hook.get("command")
    if not isinstance(command, str):
        return []
    args = hook.get("args")
    if isinstance(args, list):
        tokens = [command, *args[:2]]
    else:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return []
    if not tokens or not all(isinstance(token, str) for token in tokens):
        return []
    candidates = [tokens[0]]
    interpreter = Path(tokens[0]).name.lower()
    if interpreter in {"python", "python3", "python.exe", "node", "node.exe", "bash", "sh", "pwsh", "powershell.exe"}:
        if len(tokens) > 1 and not tokens[1].startswith("-"):
            candidates.append(tokens[1])
        elif len(tokens) > 2 and tokens[1].lower() in {"-file", "-f"} and interpreter in {"pwsh", "powershell.exe"}:
            candidates.append(tokens[2])
    found = []
    for value in candidates:
        if value.startswith("~/"):
            value = str(context.home / value[2:])
        # Relative hook paths are evaluated from the launch CWD, not from the
        # settings file. This synthetic anchor is never opened or created.
        path = resolve_reference(value, context.workspace / "__hook_cwd__", roots, variables)
        if path is not None:
            found.append(path)
    return found


def _subagent_memory(items: list[BehaviorFile], context: DiscoveryContext,
                     claude: Path, settings: dict[str, Any]) -> list[BehaviorFile]:
    if settings.get("autoMemoryEnabled") is False or os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1":
        return []
    found = []
    for item in items:
        if item.rule.source_kind != "AgentDefinition":
            continue
        fields, status = _frontmatter(read_text(item.path, item.root))
        name, memory = fields.get("name", ""), fields.get("memory", "")
        if status != "available" or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,199}", name):
            continue
        if memory not in {"user", "project", "local"}:
            continue
        scope = "user" if memory == "user" else "project"
        if not context.allows(scope):
            continue
        boundary = claude if scope == "user" else context.project_root
        directory = claude if scope == "user" else context.workspace / ".claude"
        root = directory / ("agent-memory-local" if memory == "local" else "agent-memory") / name
        if root.is_symlink() or root.parent.is_symlink():
            continue
        for path in safe_glob(root, "*.md"):
            if safe_file(path, boundary):
                found.append(BehaviorFile(path, root, _SUBAGENT_MEMORY_RULE, scope=scope,
                                          activation="conditional" if path.name == "MEMORY.md" else "on_demand",
                                          origin="claude-subagent-memory", confidence="suspected",
                                          evidence="Persistent memory explicitly configured by a subagent definition; invocation unverified"))
    return found


def _hook_scripts(data: dict[str, Any], declaring: Path, roots: list[Path], scope: str,
                  context: DiscoveryContext, plugin: Path | None = None) -> list[BehaviorFile]:
    events = data.get("hooks", data)
    if not isinstance(events, dict):
        return []
    variables = {"CLAUDE_PROJECT_DIR": context.workspace}
    if plugin is not None:
        variables["CLAUDE_PLUGIN_ROOT"] = plugin
    found: list[BehaviorFile] = []
    for groups in events.values():
        if not isinstance(groups, list):
            continue
        for group in groups[:MAX_REFERENCES]:
            handlers = group.get("hooks", []) if isinstance(group, dict) else []
            if not isinstance(handlers, list):
                continue
            for hook in handlers[:MAX_REFERENCES]:
                if not isinstance(hook, dict) or hook.get("type") != "command" or not isinstance(hook.get("command"), str):
                    continue
                paths = _command_paths(hook, context, roots, variables)
                for path in paths:
                    if _private_state_reference(path, _claude_home(context)) and not (plugin is not None and safe_file(path, plugin)):
                        continue
                    root = next(root for root in roots if safe_file(path, root))
                    found.append(BehaviorFile(path, root, _HOOK_RULE, scope=scope, origin="claude-hook",
                                              evidence="Local script referenced by a configured command hook; execution unverified"))
                    if len(found) >= MAX_REFERENCES:
                        return found
    return found


def _frontmatter_hooks(item: BehaviorFile, context: DiscoveryContext) -> list[BehaviorFile]:
    """Conservatively recognize scalar command hooks in bounded frontmatter."""
    if item.rule.metadata_parser != "frontmatter":
        return []
    text = read_text(item.path, item.root)
    if text is None or not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    if end < 0:
        return []
    lines = text[4:end].splitlines()
    in_hooks = False
    command_type = False
    hooks = []
    for line in lines:
        if line == "hooks:":
            in_hooks = True
            continue
        if not line.startswith((" ", "\t")):
            in_hooks = False
        if not in_hooks:
            continue
        typed = re.match(r"\s+(?:-\s*)?type:\s*(\S+)\s*$", line)
        if typed:
            command_type = typed[1].strip("\"'") == "command"
        match = re.match(r"\s+command:\s*(.+?)\s*$", line)
        if match and command_type:
            value = match[1]
            try:
                if value.startswith('"'):
                    value = json.loads(value)
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1].replace("''", "'")
                elif value in {"|", ">", "|-", ">-"}:
                    continue
            except ValueError:
                continue
            if isinstance(value, str):
                hooks.append({"type": "command", "command": value})
    data = {"event": [{"hooks": hooks}]}
    plugin = item.root if item.origin == "plugin" else None
    return _hook_scripts(data, item.path, [item.root], item.scope or item.rule.scope, context, plugin)


def _declared_paths(value: Any) -> list[str]:
    paths: list[str] = []
    pending = [(value, 0)]
    inspected = 0
    while pending and inspected < MAX_REFERENCES and len(paths) < MAX_REFERENCES:
        item, depth = pending.pop()
        inspected += 1
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, list) and depth < 8:
            pending.extend((child, depth + 1) for child in reversed(item[:MAX_REFERENCES]))
        elif isinstance(item, dict):
            paths.extend(child["source"] for child in list(item.values())[:MAX_REFERENCES]
                         if isinstance(child, dict) and isinstance(child.get("source"), str))
    return paths[:MAX_REFERENCES]


def _plugin_files(manifest: Path, root: Path, scope: str, context: DiscoveryContext,
                  settings: dict[str, Any]) -> list[BehaviorFile]:
    data = read_json(manifest, root)
    if data is None:
        if safe_file(manifest, root):
            return []
        data = {}
    found = [BehaviorFile(manifest, root, _PLUGIN_RULE, scope=scope, origin="plugin")] if safe_file(manifest, root) else []
    # The documented single-skill plugin layout uses SKILL.md at its root
    # when neither the skills directory nor a manifest skills key is present.
    if "skills" not in data and not (root / "skills").is_dir():
        for path in safe_glob(root, "SKILL.md"):
            rule = _rule("./SKILL.md", scope, "Skill", "skill", "on_demand", root="plugin",
                         parser="frontmatter", page="plugins/manifest-reference")
            item = _metadata(BehaviorFile(path, root, rule, scope=scope, origin="plugin"), settings)
            found.append(item)
            if settings.get("pluginHooksAllowed", True):
                found.extend(_frontmatter_hooks(item, context))
    for key, default, kind, channel, activation in _PLUGIN_COMPONENTS:
        paths = _declared_paths(data.get(key))
        if key not in data or key in {"skills", "hooks", "mcpServers", "lspServers"}:
            paths.insert(0, "./" + default)
        for value in paths[:MAX_REFERENCES]:
            if value == "." and key == "skills":
                value = "./"
            if not value.startswith("./") or ".." in Path(value).parts or any(char in value for char in "*?["):
                continue
            relative = value[2:].rstrip("/")
            target = root / relative
            if target.is_dir():
                if key in {"hooks", "mcpServers", "lspServers"}:
                    continue
                suffix = "/**/SKILL.md" if key == "skills" else "/**/*.md"
                pattern = relative + suffix if relative else suffix.lstrip("/")
            else:
                if key in {"hooks", "mcpServers", "lspServers"} and target.suffix != ".json":
                    continue
                pattern = relative
            for path in safe_glob(root, pattern):
                parser = "style" if key == "outputStyles" else ("frontmatter" if key in {"skills", "commands", "agents"} else "")
                rule = _rule(value, scope, kind, channel, activation, root="plugin", parser=parser, page="plugins/manifest-reference")
                item = _metadata(BehaviorFile(path, root, rule, scope=scope, origin="plugin"), settings)
                found.append(item)
                if parser == "frontmatter" and settings.get("pluginHooksAllowed", True):
                    found.extend(_frontmatter_hooks(item, context))
                if key == "hooks" and settings.get("pluginHooksAllowed", True):
                    hooks = read_json(path, root)
                    if hooks is not None:
                        found.extend(_hook_scripts(hooks, path, [root], scope, context, root))
                if len(found) >= MAX_REFERENCES:
                    return found[:MAX_REFERENCES]
        if key == "hooks" and settings.get("pluginHooksAllowed", True):
            hook_value = data.get(key)
            shapes: list[Any] = hook_value if isinstance(hook_value, list) else [hook_value]
            for shape in shapes[:MAX_REFERENCES]:
                if isinstance(shape, dict):
                    found.extend(_hook_scripts(shape, manifest, [root], scope, context, root))
    return found[:MAX_REFERENCES]


def _plugins(context: DiscoveryContext, claude: Path, settings: dict[str, Any]) -> tuple[list[BehaviorFile], set[Path]]:
    found: list[BehaviorFile] = []
    plugin_roots: set[Path] = set()
    enabled = settings.get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        enabled = {}
    # A plugin under a documented skills directory is explicitly declared by
    # its manifest and needs no cache traversal or extra flag.
    bases = [(context.workspace / ".claude", "project", "skills/**/.claude-plugin/plugin.json", True),
             (claude, "user", "skills/**/.claude-plugin/plugin.json", True)]
    # Plain skills search ancestor/nested directories; skills-directory plugins
    # use only the primary working directory. Recognize the other manifests
    # solely to avoid mistaking their components for plain skills.
    bases.extend((root / ".claude", "project", "skills/**/.claude-plugin/plugin.json", False)
                 for root in context.ancestors if root != context.workspace)
    bases.append((context.workspace, "project", "**/.claude/skills/**/.claude-plugin/plugin.json", False))
    seen_manifests: set[Path] = set()
    managed_enabled = settings.get("managedEnabledPlugins", {})

    def hook_settings(identity: str) -> dict[str, Any]:
        allowed = settings.get("disableAllHooks") is not True and (
            settings.get("allowManagedHooksOnly") is not True or managed_enabled.get(identity) is True
        )
        return dict(settings, pluginHooksAllowed=allowed)

    for base, scope, pattern, loadable in bases:
        if not context.allows(scope):
            continue
        for manifest in safe_glob(base, pattern, include_plugin_containers=True):
            if manifest.resolve() in seen_manifests:
                continue
            seen_manifests.add(manifest.resolve())
            root = manifest.parent.parent
            plugin_roots.add(root.resolve())
            if not loadable or settings.get("blockSkillsDirectoryPlugins"):
                continue
            data = read_json(manifest, root)
            if data is None or not isinstance(data.get("name"), str):
                continue
            active = enabled.get(data["name"] + "@skills-dir", data.get("defaultEnabled", True))
            if active is True:
                found.extend(_plugin_files(manifest, root, scope, context, hook_settings(data["name"] + "@skills-dir")))
    if not context.include_plugin_cache:
        return found, plugin_roots
    installed = read_json(claude / "plugins" / "installed_plugins.json", claude)
    records = installed.get("plugins", {}) if installed else {}
    if not isinstance(records, dict):
        return found, plugin_roots
    for identity, entries in list(records.items())[:MAX_REFERENCES]:
        if enabled.get(identity) is not True or not isinstance(entries, list):
            continue
        for entry in entries[:MAX_REFERENCES]:
            if not isinstance(entry, dict) or not isinstance(entry.get("installPath"), str):
                continue
            install_scope = entry.get("scope")
            if install_scope not in {"project", "local", "user", "managed"}:
                continue
            scope = "project" if install_scope in {"project", "local"} else install_scope
            if not context.allows(scope):
                continue
            if scope == "project":
                project = entry.get("projectPath")
                if not isinstance(project, str) or Path(project).resolve() not in context.ancestors:
                    continue
            root = Path(entry["installPath"])
            cache = claude / "plugins" / "cache"
            try:
                cache_root = cache.resolve()
                if not cache_root.is_relative_to(claude.resolve()) or not root.resolve().is_relative_to(cache_root):
                    continue
            except (OSError, RuntimeError):
                continue
            manifest = root / ".claude-plugin" / "plugin.json"
            if root.is_dir():
                found.extend(_plugin_files(manifest, root, scope, context, hook_settings(identity)))
    return found, plugin_roots


def discover(context: DiscoveryContext, agent_name: str) -> list[BehaviorFile]:
    if agent_name not in _TARGETS:
        return []
    claude, managed = _claude_home(context), _managed_root()
    settings_files = _settings(context, claude, managed)
    settings: dict[str, Any] = {}
    for _, _, scope, data in settings_files:
        for key in ("autoMemoryDirectory", "autoMemoryEnabled", "outputStyle", "disableAllHooks"):
            if key in data:
                settings[key] = data[key]
                if key == "autoMemoryDirectory":
                    settings["memoryDirectoryProject"] = scope == "project"
        permissions = data.get("permissions")
        if isinstance(permissions, dict) and "blockReadsOutsideWorkingDirectories" in permissions:
            settings["blockReadsOutsideWorkingDirectories"] = permissions["blockReadsOutsideWorkingDirectories"]
        if isinstance(data.get("claudeMdExcludes"), list):
            settings.setdefault("claudeMdExcludes", []).extend(
                pattern for pattern in data["claudeMdExcludes"][:MAX_REFERENCES] if isinstance(pattern, str)
            )
        if scope in {"user", "managed"} and isinstance(data.get("pluginConfigs"), dict):
            for key in ("agents-md@builtin", "cc-plugin-agents-md@builtin"):
                plugin = data["pluginConfigs"].get(key)
                options = plugin.get("options", {}) if isinstance(plugin, dict) else {}
                if isinstance(options, dict) and options.get("instructionFiles") in {
                    "claude-md-or-agents-md", "claude-md-and-agents-md", "claude-md", "managed-only",
                }:
                    settings["instructionFiles"] = options["instructionFiles"]
        if isinstance(data.get("enabledPlugins"), dict):
            settings.setdefault("enabledPlugins", {}).update(data["enabledPlugins"])
            if scope == "managed":
                settings.setdefault("managedEnabledPlugins", {}).update(data["enabledPlugins"])
        if scope == "managed":
            if "disableAllHooks" in data:
                settings["managedDisableAllHooks"] = data["disableAllHooks"]
            if "allowManagedHooksOnly" in data:
                settings["allowManagedHooksOnly"] = data["allowManagedHooksOnly"]
            locked = data.get("strictPluginOnlyCustomization")
            if locked is True or (isinstance(locked, list) and locked):
                settings["blockSkillsDirectoryPlugins"] = True
    items = [_metadata(item, settings) for item in _static_files(context, claude, managed)]
    local_rule = next(rule for rule in RULES if rule.pattern == ".claude/settings.local.json")
    for path, root, scope, _ in settings_files:
        if path.name == "settings.local.json" and context.allows(scope) and not any(item.path == path for item in items):
            items.append(BehaviorFile(path, root, local_rule, origin="claude-repository-local-settings"))
    items.extend(_agents_files(context, settings, items))
    if settings.get("instructionFiles") == "managed-only":
        items = [item for item in items if not (
            item.rule.scope in {"project", "user"} and item.rule.metadata_parser in {"imports", "rule"}
            and (item.activation or item.rule.activation) == "automatic"
        )]
    plugins, plugin_roots = _plugins(context, claude, settings)
    items = [item for item in items if not any(item.path.resolve().is_relative_to(root) for root in plugin_roots)]
    exclusions = settings.get("claudeMdExcludes", [])

    def included(item: BehaviorFile) -> bool:
        return item.rule.metadata_parser not in {"imports", "rule"} or not _instruction_excluded(item.path, context, exclusions)

    items = [item for item in items if included(item)]
    items.extend(item for item in _imported_files(items, context, claude, exclusions) if included(item))
    items.extend(_memory_files(context, claude, settings))
    items.extend(plugins)
    items.extend(_subagent_memory(items, context, claude, settings))
    for path, root, scope, data in settings_files:
        allowed = settings.get("managedDisableAllHooks") is not True if scope == "managed" else (
            settings.get("disableAllHooks") is not True and settings.get("allowManagedHooksOnly") is not True
        )
        if context.allows(scope) and allowed:
            roots = [root, context.project_root] if scope == "project" else [root]
            items.extend(_hook_scripts(data, path, roots, scope, context))
    for item in list(items):
        if item.origin != "plugin" and settings.get("disableAllHooks") is not True and (
            settings.get("allowManagedHooksOnly") is not True or (item.scope or item.rule.scope) == "managed"
        ):
            items.extend(_frontmatter_hooks(item, context))
    # Keep the first (most specific registered) representation of each path.
    found: dict[Path, BehaviorFile] = {}
    for item in items:
        found.setdefault(item.path.resolve(), item)
    return list(found.values())
