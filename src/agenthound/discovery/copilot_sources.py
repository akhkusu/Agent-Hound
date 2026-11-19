"""Documented Copilot customization locations and bounded local references.

The Copilot harness is shared by current VS Code and Copilot CLI. Sources
describe static availability; invocation, trust, and runtime toggles are not
observed. No application/session state or plugin data is read.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import re
from typing import Any, Iterable

from agenthound.discovery.behavior_files import (
    MAX_REFERENCES,
    BehaviorFile,
    DiscoveryContext,
    SourceRule,
    apply_rules,
    local_command_files,
    read_json,
    read_jsonc,
    read_text,
    safe_file,
    safe_glob,
)

TARGETS = ("vscode-copilot", "copilot-cli")


def _rule(pattern: str, kind: str, channel: str, activation: str,
          scope: str = "project", root: str = "workspace", confidence: str = "suspected",
          parser: str = "", targets: tuple[str, ...] = TARGETS) -> SourceRule:
    return SourceRule(
        platform="GitHub Copilot", pattern=pattern, scope=scope, source_kind=kind,
        influence_channel=channel, activation=activation, targets=targets,
        confidence=confidence, evidence=f"Documented Copilot customization: {pattern}; runtime invocation and trust unverified",
        metadata_parser=parser or ("agent_frontmatter" if kind == "AgentDefinition" else ""), root=root,
    )


_PROJECT_RULES = (
    _rule(".github/copilot-instructions.md", "AgentInstruction", "persistent_instruction", "automatic", confidence="confirmed", parser="imports"),
    _rule("**/.github/copilot-instructions.md", "AgentInstruction", "path_scoped_instruction", "conditional", parser="imports"),
    *(_rule(pattern, "AgentInstruction", "path_scoped_instruction", "conditional", parser="imports")
      for pattern in ("**/AGENTS.md", "**/CLAUDE.md", "**/.claude/CLAUDE.md", "**/GEMINI.md")),
    _rule(".github/instructions/**/*.instructions.md", "AgentInstruction", "path_scoped_instruction", "conditional", parser="frontmatter"),
    _rule(".github/prompts/**/*.prompt.md", "PromptFile", "reusable_prompt", "on_demand", targets=("vscode-copilot",)),
    _rule(".github/agents/**/*.agent.md", "AgentDefinition", "agent_definition", "on_demand"),
    _rule(".claude/agents/**/*.md", "AgentDefinition", "agent_definition", "on_demand"),
    *(_rule(f"{root}/**/SKILL.md", "Skill", "skill", "on_demand", parser="skill")
      for root in (".github/skills", ".claude/skills", ".agents/skills")),
    _rule(".github/hooks/**/*.json", "RuntimeConfiguration", "runtime_configuration", "configured", parser="hooks"),
    *(_rule(pattern, "RuntimeConfiguration", "runtime_configuration", "configured", parser="settings")
      for pattern in (".github/copilot/settings.json", ".github/copilot/settings.local.json",
                      ".claude/settings.json", ".claude/settings.local.json")),
    *(_rule(pattern, "RuntimeConfiguration", "runtime_configuration", "configured")
      for pattern in (".github/mcp.json", ".mcp.json")),
    _rule(".vscode/mcp.json", "RuntimeConfiguration", "runtime_configuration", "configured", targets=("vscode-copilot",)),
    _rule(".vscode/settings.json", "RuntimeConfiguration", "runtime_configuration", "configured", parser="settings", targets=("vscode-copilot",)),
    _rule(".github/lsp.json", "RuntimeConfiguration", "runtime_configuration", "configured", targets=("copilot-cli",)),
)
_USER_RULES = (
    _rule("copilot-instructions.md", "AgentInstruction", "persistent_instruction", "automatic", "user", "copilot_home", "confirmed", "imports"),
    _rule("instructions/**/*.instructions.md", "AgentInstruction", "path_scoped_instruction", "conditional", "user", "copilot_home", parser="frontmatter"),
    _rule("agents/**/*.agent.md", "AgentDefinition", "agent_definition", "on_demand", "user", "copilot_home"),
    _rule("skills/**/SKILL.md", "Skill", "skill", "on_demand", "user", "copilot_home", parser="skill"),
    _rule("skills/**/SKILL.md", "Skill", "skill", "on_demand", "user", "user_agents", parser="skill"),
    _rule("hooks/**/*.json", "RuntimeConfiguration", "runtime_configuration", "configured", "user", "copilot_home", parser="hooks"),
    _rule("settings.json", "RuntimeConfiguration", "runtime_configuration", "configured", "user", "copilot_home", parser="settings"),
    _rule("mcp-config.json", "RuntimeConfiguration", "runtime_configuration", "configured", "user", "copilot_home"),
    _rule("lsp-config.json", "RuntimeConfiguration", "runtime_configuration", "configured", "user", "copilot_home", targets=("copilot-cli",)),
    _rule("providers.json", "RuntimeConfiguration", "runtime_configuration", "configured", "user", "copilot_home", targets=("copilot-cli",)),
    _rule("*.json", "RuntimeConfiguration", "runtime_configuration", "configured", "managed", "policy", parser="hooks", targets=("copilot-cli",)),
)
_HOOK_SCRIPT = _rule("<referenced-hook-script>", "HookScript", "hook_execution", "configured", root="reference")
_IMPORTED = _rule("<relative-instruction-import>", "AgentInstruction", "persistent_instruction", "automatic", root="reference", parser="imports")
_PLUGIN_MANIFEST = _rule("<enabled-plugin>/plugin.json", "PluginManifest", "plugin", "configured", root="reference", parser="plugin")
_PLUGIN_RULES = (
    _rule("skills/**/SKILL.md", "Skill", "skill", "on_demand", root="plugin", parser="skill"),
    _rule("com.github.copilot/agents/**/*.agent.md", "AgentDefinition", "agent_definition", "on_demand", root="plugin"),
    _rule("com.github.copilot/commands/**/*.md", "PromptFile", "reusable_prompt", "on_demand", root="plugin"),
    _rule("com.github.copilot/rules/**/*.md", "AgentInstruction", "path_scoped_instruction", "conditional", root="plugin", parser="frontmatter"),
    _rule("com.github.copilot/hooks/hooks.json", "RuntimeConfiguration", "runtime_configuration", "configured", root="plugin", parser="hooks"),
    _rule("com.github.copilot/lsp.json", "RuntimeConfiguration", "runtime_configuration", "configured", root="plugin"),
    _rule("mcp.json", "RuntimeConfiguration", "runtime_configuration", "configured", root="plugin"),
)
_CONFIGURED_LOCATIONS = {
    "chat.instructionsFilesLocations": _rule("**/*.instructions.md", "AgentInstruction", "path_scoped_instruction", "conditional", root="configured", parser="frontmatter", targets=("vscode-copilot",)),
    "chat.agentFilesLocations": _rule("**/*.agent.md", "AgentDefinition", "agent_definition", "on_demand", root="configured", targets=("vscode-copilot",)),
    "chat.agentSkillsLocations": _rule("**/SKILL.md", "Skill", "skill", "on_demand", root="configured", parser="skill", targets=("vscode-copilot",)),
}
RULES = (*_PROJECT_RULES, *_USER_RULES, _HOOK_SCRIPT, _IMPORTED, _PLUGIN_MANIFEST,
         *_PLUGIN_RULES, *_CONFIGURED_LOCATIONS.values())


def _target_pattern(item: BehaviorFile) -> BehaviorFile:
    if item.rule.metadata_parser != "frontmatter":
        return item
    text = read_text(item.path, item.root)
    if text is None:
        return replace(item, discovery_status="metadata_unavailable")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return item
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return replace(item, discovery_status="metadata_unavailable")
    for line in lines[1:end]:
        if line.startswith("applyTo:"):
            value = line.partition(":")[2].strip().strip("\"'")
            # Only bounded glob-like metadata; never include arbitrary prose.
            if len(value) <= 500 and re.fullmatch(r"[\w./*?{},\[\] !-]+", value):
                return replace(item, target_pattern=value)
    return item


def _instructions(context: DiscoveryContext, items: list[BehaviorFile]) -> list[BehaviorFile]:
    active = set(context.ancestors)
    result: list[BehaviorFile] = []
    for item in items:
        if item.path.name in {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}:
            anchor = item.path.parent.parent if item.path.parent.name == ".claude" else item.path.parent
            if anchor in active:
                item = replace(item, rule=replace(item.rule, influence_channel="persistent_instruction"),
                               activation="automatic", confidence="confirmed")
        if item.path.name == "copilot-instructions.md" and item.path.parent.name == ".github":
            if item.path.parent.parent not in active:
                item = replace(item, activation="conditional", confidence="suspected")
            else:
                item = replace(item, rule=replace(item.rule, influence_channel="persistent_instruction"))
        result.append(_target_pattern(item))
    return result


def _imports(context: DiscoveryContext, items: Iterable[BehaviorFile]) -> list[BehaviorFile]:
    found: list[BehaviorFile] = []
    seen: set[Path] = set()
    queue = [(item, 0) for item in items if item.rule.metadata_parser == "imports" and item.path.name != "GEMINI.md"]
    while queue and len(seen) < MAX_REFERENCES:
        item, depth = queue.pop(0)
        resolved = item.path.resolve()
        if resolved in seen or depth >= 5:
            continue
        seen.add(resolved)
        text = read_text(item.path, item.root)
        if text is None:
            continue
        fenced = False
        for line in text.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced:
                continue
            for match in re.finditer(r"(?<![\w@`])@([^\s`<>\"']+)", line):
                value = match.group(1).rstrip(".,;:)")
                if value.startswith(("/", "~")):
                    continue
                path = item.path.parent / value
                root = context.project_root if (item.scope or item.rule.scope) == "project" else item.root
                if not safe_file(path, root) or path.resolve() in seen:
                    continue
                imported = BehaviorFile(
                    path=path, root=root, rule=replace(_IMPORTED, scope=item.scope or item.rule.scope),
                    activation=item.activation or item.rule.activation,
                    confidence=item.confidence or item.rule.confidence,
                    origin=str(item.path), evidence="Documented Copilot relative @file import; bounded static reference",
                )
                found.append(imported)
                queue.append((imported, depth + 1))
                if len(found) >= MAX_REFERENCES:
                    return found
    return found


def _settings(items: list[BehaviorFile]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    # Native settings take precedence over cross-tool repository compatibility.
    order = sorted((item for item in items if item.rule.metadata_parser == "settings"), key=lambda item: (
        0 if item.rule.scope == "user" else 1 if item.path.parent.name == ".claude" else 2,
        item.path.name.endswith(".local.json"), str(item.path),
    ))
    for item in order:
        raw = read_jsonc(item.path, item.root)
        if raw is None:
            continue
        for key in ("enabledPlugins", "chat.pluginLocations", "chat.plugins.enabledPlugins", *_CONFIGURED_LOCATIONS):
            value = raw.get(key)
            if isinstance(value, dict):
                previous = merged.setdefault(key, {})
                previous.update(value)
        if isinstance(raw.get("disableAllHooks"), bool):
            merged["disableAllHooks"] = raw["disableAllHooks"]
        if isinstance(raw.get("chat.plugins.enabled"), bool):
            merged["chat.plugins.enabled"] = raw["chat.plugins.enabled"]
    return merged


def _configured_locations(context: DiscoveryContext, agent_name: str,
                          settings: dict[str, Any], roots: list[Path]) -> list[BehaviorFile]:
    if agent_name != "vscode-copilot":
        return []
    found: list[BehaviorFile] = []
    for key, template in _CONFIGURED_LOCATIONS.items():
        for value, enabled in list(settings.get(key, {}).items())[:MAX_REFERENCES]:
            if not isinstance(value, str) or enabled is not True or any(char in value for char in ("$", "`", "%")):
                continue
            path = context.home / value[2:] if value.startswith("~/") else Path(value)
            if not path.is_absolute():
                path = context.workspace / path
            try:
                if not path.is_dir() or not any(path.resolve().is_relative_to(root.resolve()) for root in roots):
                    continue
                scope = "project" if path.resolve().is_relative_to(context.project_root.resolve()) else "user"
            except (OSError, RuntimeError):
                continue
            rule = replace(template, scope=scope)
            found.extend(apply_rules(context, agent_name, [rule], {"configured": path}))
    return found


def _hook_scripts(context: DiscoveryContext, items: list[BehaviorFile],
                  disabled: bool = False) -> list[BehaviorFile]:
    found: list[BehaviorFile] = []
    for item in items:
        if item.rule.metadata_parser not in {"hooks", "settings", "agent_frontmatter"}:
            continue
        if disabled and item.rule.scope != "managed":
            continue
        raw = _agent_hooks(item) if item.rule.metadata_parser == "agent_frontmatter" else read_jsonc(item.path, item.root)
        if raw is None or raw.get("disableAllHooks") is True:
            continue
        hooks = raw.get("hooks", {})
        if not isinstance(hooks, dict):
            continue
        roots = [item.root]
        if context.allows("project"):
            roots.append(context.project_root)
        variables = {"PLUGIN_ROOT": item.root, "CLAUDE_PLUGIN_ROOT": item.root,
                     "COPILOT_PROJECT_DIR": context.project_root}
        queue = list(hooks.values())[:MAX_REFERENCES]
        inspected = 0
        while queue and inspected < MAX_REFERENCES:
            entry = queue.pop(0)
            inspected += 1
            if isinstance(entry, list):
                queue.extend(entry[:MAX_REFERENCES])
                continue
            if not isinstance(entry, dict) or entry.get("type", "command") != "command":
                continue
            if isinstance(entry.get("hooks"), list):
                queue.extend(entry["hooks"][:MAX_REFERENCES])
            for key in ("bash", "powershell", "command", "windows", "linux", "osx"):
                command = entry.get(key)
                if not isinstance(command, str):
                    continue
                # Copilot commands default to the repository's working directory.
                declaring = context.project_root / ".copilot-hook-anchor"
                cwd = entry.get("cwd")
                if isinstance(cwd, str) and cwd:
                    if any(char in cwd for char in ("$", "`", "%")):
                        continue
                    directory = Path(cwd)
                    if not directory.is_absolute():
                        directory = context.project_root / directory
                    try:
                        if not directory.is_dir() or not any(directory.resolve().is_relative_to(root.resolve()) for root in roots):
                            continue
                    except (OSError, RuntimeError):
                        continue
                    declaring = directory / ".copilot-hook-anchor"
                for path in local_command_files(command, declaring, roots, variables):
                    root = next(root for root in roots if safe_file(path, root))
                    found.append(BehaviorFile(
                        path, root, replace(_HOOK_SCRIPT, scope=item.scope or item.rule.scope),
                        origin=str(item.path), evidence="Local script referenced by Copilot hook; execution not observed",
                    ))
    return found


def _agent_hooks(item: BehaviorFile) -> dict[str, Any] | None:
    """Recognize only literal command fields inside a bounded YAML hooks map.

    Full YAML evaluation, aliases, multiline shell bodies, and arbitrary tags
    are deliberately unsupported. Instruction text outside the header is ignored.
    """
    text = read_text(item.path, item.root)
    lines = text.splitlines() if text is not None else []
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return None
    in_hooks = False
    commands: list[dict[str, str]] = []
    for line in lines[1:end]:
        if not line[:1].isspace():
            in_hooks = line.strip() == "hooks:"
            continue
        if not in_hooks:
            continue
        match = re.match(r"\s*(?:-\s*)?(command|bash|powershell|windows|linux|osx):\s*(.+)$", line)
        if match:
            value = match.group(2).strip()
            if value.startswith(("|", ">", "*", "!", "&")):
                continue
            if len(value) > 1 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            commands.append({match.group(1): value})
    return {"hooks": {"agent_scoped": commands}}


def _plugin_root(value: str, context: DiscoveryContext, copilot_home: Path,
                 roots: list[Path]) -> Path | None:
    if "@" in value and not value.startswith((".", "/", "~")):
        if not context.include_plugin_cache:
            return None
        name, marketplace = value.rsplit("@", 1)
        if not all(re.fullmatch(r"[\w.-]+", part) and part not in {".", ".."} for part in (name, marketplace)):
            return None
        path = copilot_home / "installed-plugins" / marketplace / name
    else:
        if ":" in value or value.startswith("~") or not value.startswith((".", "/")):
            return None
        path = Path(value)
        if not path.is_absolute():
            path = context.project_root / path
    if not context.include_plugin_cache and any(part in {"installed-plugins", "cache", ".cache"} for part in path.parts):
        return None
    try:
        if path.is_dir() and any(path.resolve().is_relative_to(root.resolve()) for root in roots):
            return path
    except (OSError, RuntimeError):
        pass
    return None


def _plugin_files(context: DiscoveryContext, agent_name: str, settings: dict[str, Any],
                  copilot_home: Path, roots: list[Path]) -> list[BehaviorFile]:
    enabled: dict[str, Any] = dict(settings.get("enabledPlugins", {}))
    if agent_name == "vscode-copilot":
        if settings.get("chat.plugins.enabled") is False:
            return []
        enabled.update(settings.get("chat.plugins.enabledPlugins", {}))
        enabled.update(settings.get("chat.pluginLocations", {}))
    found: list[BehaviorFile] = []
    for spec, value in list(enabled.items())[:MAX_REFERENCES]:
        if value is not True or not isinstance(spec, str):
            continue
        root = _plugin_root(spec, context, copilot_home, roots)
        if root is None:
            continue
        scope = "project" if root.resolve().is_relative_to(context.project_root.resolve()) else "user"
        if not context.allows(scope):
            continue
        root_manifest = root / "plugin.json"
        root_metadata = read_json(root_manifest, root) or {}
        root_schema = root_metadata.get("$schema", "")
        supported_schemas = {
            "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "https://agent-plugins.org/schemas/1.1.0/plugin.schema.json",
        }
        if not isinstance(root_schema, str):
            continue
        if isinstance(root_schema, str) and "agent-plugins.org/" in root_schema and root_schema not in supported_schemas:
            continue
        paths = [root_manifest] if root_schema in supported_schemas else [
            root / suffix for suffix in (".plugin/plugin.json", "plugin.json", ".github/plugin/plugin.json", ".claude-plugin/plugin.json")
        ]
        manifest = next((path for path in paths if safe_file(path, root)), None)
        if manifest is None:
            continue
        raw = read_json(manifest, root)
        if raw is None or not isinstance(raw.get("name"), str):
            continue
        schema = raw.get("$schema", "")
        if not isinstance(schema, str):
            continue
        standard = schema in supported_schemas
        if standard and manifest != root_manifest:
            continue
        if isinstance(schema, str) and "agent-plugins.org/" in schema and not standard:
            continue
        found.append(BehaviorFile(manifest, root, replace(_PLUGIN_MANIFEST, scope=scope), origin="enabled-plugin"))
        if standard:
            rules = [replace(rule, scope=scope) for rule in _PLUGIN_RULES]
            found.extend(apply_rules(context, agent_name, rules, {"plugin": root}))
            continue
        legacy = {
            "agents": ("AgentDefinition", "agent_definition", "on_demand", "**/*.agent.md", ""),
            "skills": ("Skill", "skill", "on_demand", "**/SKILL.md", "skill"),
            "hooks": ("RuntimeConfiguration", "runtime_configuration", "configured", "**/*.json", "hooks"),
            "mcpServers": ("RuntimeConfiguration", "runtime_configuration", "configured", "**/*.json", ""),
            "lspServers": ("RuntimeConfiguration", "runtime_configuration", "configured", "**/*.json", ""),
        }
        defaults = {"agents": ["agents"], "skills": ["skills"], "hooks": ["hooks.json", "hooks/hooks.json"],
                    "mcpServers": [".mcp.json", ".github/mcp.json"], "lspServers": ["lsp.json", ".github/lsp.json"]}
        for key, (kind, channel, activation, pattern, parser) in legacy.items():
            configured = raw.get(key, defaults[key])
            values = [configured] if isinstance(configured, str) else configured if isinstance(configured, list) else []
            for relative in values[:MAX_REFERENCES]:
                if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                    continue
                path = root / relative
                paths = [path] if safe_file(path, root) else safe_glob(root, f"{relative.rstrip('/')}/{pattern}")
                for path in paths:
                    rule = _rule(relative, kind, channel, activation, scope, "plugin", parser=parser)
                    found.append(BehaviorFile(path, root, rule, origin="enabled-plugin"))
        if not (root / "skills").exists() and safe_file(root / "SKILL.md", root):
            found.append(BehaviorFile(root / "SKILL.md", root, replace(_PLUGIN_RULES[0], scope=scope), origin="enabled-plugin"))
        if isinstance(raw.get("hooks"), dict):
            found.append(BehaviorFile(manifest, root, _rule("plugin.json", "RuntimeConfiguration", "runtime_configuration", "configured", scope, "plugin", parser="hooks"), origin="enabled-plugin"))
    return found


def discover(context: DiscoveryContext, agent_name: str) -> list[BehaviorFile]:
    if agent_name not in TARGETS:
        return []
    copilot_home = Path(os.environ.get("COPILOT_HOME", str(context.home / ".copilot"))).expanduser()
    roots = {"workspace": context.workspace, "copilot_home": copilot_home,
             "user_agents": context.home / ".agents"}
    if os.name == "nt":
        roots["policy"] = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "GitHub" / "Copilot" / "policy.d"
    else:
        roots["policy"] = Path("/etc/github-copilot/policy.d")
    items = apply_rules(context, agent_name, (*_PROJECT_RULES, *_USER_RULES), roots)
    # Only the active chain above the selected workspace, never sibling projects.
    for directory in context.ancestors[:-1]:
        ancestor_rules = [rule for rule in _PROJECT_RULES if rule.pattern in {
            ".github/copilot-instructions.md", "**/AGENTS.md", "**/CLAUDE.md", "**/.claude/CLAUDE.md", "**/GEMINI.md",
        }]
        for rule in ancestor_rules:
            pattern = rule.pattern.removeprefix("**/")
            items.extend(apply_rules(context, agent_name, [replace(rule, pattern=pattern)], {"workspace": directory}))
    items = _instructions(context, items)
    approved: list[Path] = []
    if context.allows("project"):
        approved.append(context.project_root)
    if context.allows("user"):
        approved.extend([copilot_home, context.home / ".agents"])
    settings = _settings(items)
    items.extend(_target_pattern(item) for item in _configured_locations(context, agent_name, settings, approved))
    plugins = _plugin_files(context, agent_name, settings, copilot_home, approved)
    items.extend(_target_pattern(item) for item in plugins)
    items.extend(_imports(context, items))
    items.extend(_hook_scripts(context, items, disabled=settings.get("disableAllHooks") is True))
    result: dict[tuple[Path, str], BehaviorFile] = {}
    for item in items:
        key = (item.path.resolve(), item.rule.influence_channel)
        result.setdefault(key, item)
    return list(result.values())
