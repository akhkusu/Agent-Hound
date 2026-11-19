"""Documented Cursor files and declared local plugin components."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from agenthound.discovery.behavior_files import (
    BehaviorFile, DiscoveryContext, SourceRule, apply_rules, local_command_files,
    read_json, read_text, resolve_reference, safe_file, safe_glob,
)

_RULE_DOCS = "https://cursor.com/docs/rules"
_PLUGIN_DOCS = "https://cursor.com/docs/reference/plugins"


def _rule(pattern: str, scope: str, kind: str, channel: str, activation: str,
          parser: str = "", root: str = "workspace", confidence: str = "suspected") -> SourceRule:
    return SourceRule(platform="Cursor", pattern=pattern, scope=scope, source_kind=kind,
                      influence_channel=channel, activation=activation, targets=("cursor",),
                      confidence=confidence, evidence=f"Documented Cursor behavior source; {_RULE_DOCS}",
                      metadata_parser=parser, root=root)


LEGACY = _rule(".cursorrules", "project", "AgentInstruction", "persistent_instruction", "automatic", confidence="confirmed")
INSTRUCTIONS = _rule("**/AGENTS.md", "project", "AgentInstruction", "persistent_instruction", "conditional")
PROJECT_RULES = _rule("**/.cursor/rules/**/*.mdc", "project", "AgentInstruction", "path_scoped_instruction", "conditional", "cursor_frontmatter")
PROJECT_MCP = _rule(".cursor/mcp.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", "json")
USER_MCP = _rule(".cursor/mcp.json", "user", "RuntimeConfiguration", "runtime_configuration", "configured", "json", "home")
IGNORE = _rule(".cursorignore", "project", "ContextVisibility", "context_visibility", "configured")
INDEX_IGNORE = _rule(".cursorindexingignore", "project", "ContextVisibility", "context_visibility", "configured")
PROJECT_HOOKS = _rule(".cursor/hooks.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", "cursor_hooks")
USER_HOOKS = _rule(".cursor/hooks.json", "user", "RuntimeConfiguration", "runtime_configuration", "configured", "cursor_hooks", "home")
HOOK_SCRIPT = _rule("hooks:command", "project", "HookScript", "hook_execution", "conditional", "command_reference", "reference")
RULE_REFERENCE = _rule("rule:@path", "project", "DocFile", "prompt_context", "on_demand", "reference", "reference")
PROJECT_SKILLS = tuple(_rule(f"**/{directory}/skills/**/SKILL.md", "project", "Skill", "skill", "on_demand", "skill")
                       for directory in (".cursor", ".agents", ".claude", ".codex"))
USER_SKILLS = tuple(_rule(f"{directory}/skills/**/SKILL.md", "user", "Skill", "skill", "on_demand", "skill", "home")
                    for directory in (".cursor", ".agents", ".claude", ".codex"))
PROJECT_AGENTS = tuple(_rule(f"{directory}/agents/**/*.md", "project", "AgentDefinition", "agent_definition", "on_demand")
                       for directory in (".cursor", ".claude", ".codex"))
USER_AGENTS = tuple(_rule(f"{directory}/agents/**/*.md", "user", "AgentDefinition", "agent_definition", "on_demand", root="home")
                    for directory in (".cursor", ".claude", ".codex"))
PLUGIN_MANIFEST = _rule(".cursor/plugins/local/*/{plugin.json,.cursor-plugin/plugin.json}", "user", "PluginManifest", "plugin", "conditional", "cursor_plugin", "home")
PLUGIN_SKILL = _rule("plugin:skills/**/SKILL.md", "user", "Skill", "skill", "on_demand", "skill", "reference")
PLUGIN_RULE = _rule("plugin:rules/*.{md,mdc,markdown}", "user", "AgentInstruction", "path_scoped_instruction", "conditional", "cursor_frontmatter", "reference")
PLUGIN_AGENT = _rule("plugin:agents/*.{md,mdc,markdown}", "user", "AgentDefinition", "agent_definition", "on_demand", root="reference")
PLUGIN_COMMAND = _rule("plugin:commands/*.{md,mdc,markdown,txt}", "user", "PromptFile", "reusable_prompt", "on_demand", root="reference")
PLUGIN_HOOKS = _rule("plugin:hooks/hooks.json", "user", "RuntimeConfiguration", "runtime_configuration", "configured", "cursor_hooks", "reference")
PLUGIN_MCP = _rule("plugin:mcp.json|mcpServers", "user", "RuntimeConfiguration", "runtime_configuration", "configured", "reference", "reference")
RULES = (LEGACY, INSTRUCTIONS, PROJECT_RULES, PROJECT_MCP, USER_MCP, IGNORE, INDEX_IGNORE,
         PROJECT_HOOKS, USER_HOOKS, HOOK_SCRIPT, RULE_REFERENCE, *PROJECT_SKILLS, *USER_SKILLS,
         *PROJECT_AGENTS, *USER_AGENTS, PLUGIN_MANIFEST, PLUGIN_SKILL, PLUGIN_RULE,
         PLUGIN_AGENT, PLUGIN_COMMAND, PLUGIN_HOOKS, PLUGIN_MCP)


def _add(found: list[BehaviorFile], path: Path, root: Path, rule: SourceRule,
         scope: str = "", **values: Any) -> None:
    if safe_file(path, root):
        found.append(BehaviorFile(path=path, root=root, rule=rule, scope=scope or rule.scope, **values))


def _frontmatter(text: str | None) -> dict[str, str] | None:
    """Parse only the small scalar fields needed for activation, never the body."""
    if text is None:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return None
    result: dict[str, str] = {}
    for line in lines[1:end]:
        if line[:1].isspace() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"alwaysApply", "description", "globs"}:
            result[key] = value.strip().strip('"\'')
    return result


def _rule_file(found: list[BehaviorFile], path: Path, root: Path, rule: SourceRule,
               base: Path, plugin: bool = False, active: bool = True) -> None:
    text = read_text(path, root)
    metadata = _frontmatter(text)
    values: dict[str, Any] = {}
    if metadata is None:
        values.update(activation="unknown", confidence="suspected", discovery_status="metadata_unavailable")
    elif metadata.get("alwaysApply", "false").lower() == "true":
        values.update(activation="automatic", confidence="suspected" if plugin or not active else "confirmed")
    elif metadata.get("globs"):
        values.update(activation="conditional", confidence="suspected")
        globs = metadata["globs"]
        if len(globs) <= 512 and re.fullmatch(r"[\w*/.,{}\[\]!? :@\\-]+", globs):
            values["target_pattern"] = globs
    elif metadata.get("description"):
        values.update(activation="conditional", confidence="suspected")
    else:
        values.update(activation="on_demand", confidence="suspected")
    if not active and values.get("activation") == "automatic":
        values["activation"] = "conditional"
    values["evidence"] = "Cursor rule metadata controls availability; matching files, manual selection and runtime use unverified. " + _RULE_DOCS
    _add(found, path, root, rule, **values)
    if text is not None:
        for value in re.findall(r"(?<![\w@])@([\w./-]+\.[A-Za-z0-9]+)", text)[:128]:
            ref = resolve_reference(value, path, (root,)) or resolve_reference(value, base / "_rule_reference", (root,))
            if ref is not None:
                _add(found, ref, root, RULE_REFERENCE, rule.scope,
                     evidence="File explicitly mentioned by a Cursor rule; contents are tool-read on demand, not automatically inlined.")


def _hook_scripts(found: list[BehaviorFile], raw: dict[str, Any], declaring: Path, root: Path,
                  scope: str, base: Path, home: Path) -> None:
    hooks = raw.get("hooks", {})
    if not isinstance(hooks, dict):
        return
    for entries in list(hooks.values())[:64]:
        if not isinstance(entries, list):
            continue
        for entry in entries[:64]:
            if not isinstance(entry, dict) or entry.get("type", "command") != "command":
                continue
            for script in local_command_files(entry.get("command"), base / "_hook_reference", (root,),
                                              {"CURSOR_PLUGIN_ROOT": root, "HOME": home}):
                _add(found, script, root, HOOK_SCRIPT, scope,
                     evidence="Local file explicitly referenced by a Cursor command hook; execution unverified.")


def _strings(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else [item for item in value if isinstance(item, str)][:64] if isinstance(value, list) else []


def _component_paths(plugin: Path, value: Any, suffixes: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for name in _strings(value):
        path = plugin / name
        if safe_file(path, plugin) and path.suffix.lower() in suffixes:
            found.append(path)
        elif path.is_dir():
            try:
                if not path.resolve().is_relative_to(plugin.resolve()):
                    continue
            except (OSError, RuntimeError):
                continue
            for suffix in suffixes:
                found.extend(safe_glob(path, f"**/*{suffix}"))
    return found


def _plugins(found: list[BehaviorFile], context: DiscoveryContext) -> None:
    if not context.allows("user"):
        return
    local = context.home / ".cursor/plugins/local"
    manifests = safe_glob(local, "*/.cursor-plugin/plugin.json", include_plugin_containers=True) + safe_glob(local, "*/plugin.json", include_plugin_containers=True)
    seen: set[Path] = set()
    for manifest in manifests[:128]:
        native = manifest.parent.name == ".cursor-plugin"
        plugin = manifest.parent.parent if native else manifest.parent
        if plugin.resolve() in seen:
            continue
        raw = read_json(manifest, local)
        if raw is None or not isinstance(raw.get("name"), str):
            continue
        if not native and raw.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
            continue
        seen.add(plugin.resolve())
        _add(found, manifest, local, PLUGIN_MANIFEST,
             evidence="Manifest in documented local-plugin import directory; import policy, overrides and activation unverified. " + _PLUGIN_DOCS)
        components: list[tuple[str, str, SourceRule, tuple[str, ...]]] = [("skills", "skills", PLUGIN_SKILL, (".md",))]
        if native:
            components.extend([
                ("rules", "rules", PLUGIN_RULE, (".md", ".mdc", ".markdown")),
                ("agents", "agents", PLUGIN_AGENT, (".md", ".mdc", ".markdown")),
                ("commands", "commands", PLUGIN_COMMAND, (".md", ".mdc", ".markdown", ".txt")),
            ])
        for key, default, rule, suffixes in components:
            for path in _component_paths(plugin, raw.get(key, default) if native else default, suffixes):
                if rule is PLUGIN_SKILL and path.name != "SKILL.md":
                    continue
                if rule is PLUGIN_RULE:
                    _rule_file(found, path, plugin, rule, plugin, plugin=True)
                else:
                    _add(found, path, plugin, rule)
        if native and "skills" not in raw and not (plugin / "skills").is_dir():
            _add(found, plugin / "SKILL.md", plugin, PLUGIN_SKILL)
        for name in _strings(raw.get("mcpServers", "mcp.json") if native else "mcp.json"):
            ref = resolve_reference(name, plugin / "_manifest", (plugin,))
            if ref is not None:
                _add(found, ref, plugin, PLUGIN_MCP)
        if native:
            hooks_value = raw.get("hooks", "hooks/hooks.json")
            if isinstance(hooks_value, dict):
                _hook_scripts(found, hooks_value, manifest, plugin, "user", plugin, context.home)
            for name in _strings(hooks_value):
                ref = resolve_reference(name, plugin / "_manifest", (plugin,))
                if ref is not None:
                    _add(found, ref, plugin, PLUGIN_HOOKS)
                    _hook_scripts(found, read_json(ref, plugin) or {}, ref, plugin, "user", plugin, context.home)


def discover(context: DiscoveryContext, agent_name: str) -> list[BehaviorFile]:
    if agent_name != "cursor":
        return []
    found = apply_rules(context, agent_name, (LEGACY, PROJECT_MCP, USER_MCP, IGNORE, INDEX_IGNORE,
                                            PROJECT_HOOKS, USER_HOOKS),
                        {"workspace": context.workspace, "home": context.home})
    if context.allows("project"):
        for base in context.ancestors:
            _add(found, base / "AGENTS.md", context.project_root, INSTRUCTIONS,
                 activation="automatic", confidence="confirmed",
                 precedence=len(base.relative_to(context.project_root).parts) + 1,
                 evidence="Cursor AGENTS.md in the active workspace ancestor chain; more specific guidance takes precedence.")
        for path in safe_glob(context.workspace, "**/AGENTS.md"):
            if path.parent not in context.ancestors:
                _add(found, path, context.project_root, INSTRUCTIONS,
                     target_pattern=str(path.parent.relative_to(context.project_root) / "**"),
                     evidence="Nested AGENTS.md applies when working on its subtree; current matching file unverified.")
        rule_paths = set(safe_glob(context.workspace, PROJECT_RULES.pattern))
        for base in context.ancestors:
            rule_paths.update(safe_glob(base, ".cursor/rules/**/*.mdc"))
        for path in sorted(rule_paths):
            base = next(parent.parent for parent in path.parents if parent.name == ".cursor")
            _rule_file(found, path, context.project_root, PROJECT_RULES, base, active=base in context.ancestors)
        for rule in PROJECT_SKILLS:
            for path in safe_glob(context.workspace, rule.pattern):
                _add(found, path, context.project_root, rule)
        _hook_scripts(found, read_json(context.workspace / ".cursor/hooks.json", context.workspace) or {},
                      context.workspace / ".cursor/hooks.json", context.workspace, "project", context.workspace, context.home)
    if context.allows("user"):
        found.extend(apply_rules(context, agent_name, USER_SKILLS, {"home": context.home}))
        _hook_scripts(found, read_json(context.home / ".cursor/hooks.json", context.home) or {},
                      context.home / ".cursor/hooks.json", context.home, "user", context.home / ".cursor", context.home)
    # Documented subagent precedence: project over user; Cursor over compatibility.
    seen_names: set[str] = set()
    for scope, root, rules in (("project", context.workspace, PROJECT_AGENTS), ("user", context.home, USER_AGENTS)):
        if not context.allows(scope):
            continue
        for rule in rules:
            for path in safe_glob(root, rule.pattern):
                if path.stem in seen_names:
                    continue
                seen_names.add(path.stem)
                _add(found, path, root, rule)
    _plugins(found, context)
    return found
