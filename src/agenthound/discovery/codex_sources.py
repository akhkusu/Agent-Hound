"""Documented Codex behavior sources; no runtime or instruction bodies exported."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agenthound.discovery.behavior_files import (
    BehaviorFile, DiscoveryContext, SourceRule, local_command_files, read_json,
    read_text, read_toml, resolve_reference, safe_file, safe_glob,
)

_DOCS = "https://learn.chatgpt.com/docs/agent-configuration/agents-md"
_CONFIG_DOCS = "https://learn.chatgpt.com/docs/config-file/config-reference"
_PLUGIN_DOCS = "https://developers.openai.com/plugins/build/plugins"
_SYSTEM_ROOT = Path("/etc/codex")


def _rule(pattern: str, scope: str, kind: str, channel: str, activation: str,
          parser: str = "", root: str = "workspace") -> SourceRule:
    return SourceRule(
        platform="Codex", pattern=pattern, scope=scope, source_kind=kind,
        influence_channel=channel, activation=activation, targets=("codex",),
        confidence="suspected", evidence=f"Documented Codex behavior file; {_CONFIG_DOCS}",
        metadata_parser=parser, root=root,
    )


PROJECT_OVERRIDE = _rule("**/AGENTS.override.md", "project", "AgentInstruction", "persistent_instruction", "conditional", "codex_precedence")
PROJECT_INSTRUCTION = _rule("**/AGENTS.md", "project", "AgentInstruction", "persistent_instruction", "conditional", "codex_precedence")
GLOBAL_OVERRIDE = _rule("AGENTS.override.md", "user", "AgentInstruction", "persistent_instruction", "automatic", "codex_precedence", "codex_home")
GLOBAL_INSTRUCTION = _rule("AGENTS.md", "user", "AgentInstruction", "persistent_instruction", "automatic", "codex_precedence", "codex_home")
FALLBACK = _rule("config.toml:project_doc_fallback_filenames", "project", "AgentInstruction", "persistent_instruction", "conditional", "codex_fallback")
PROJECT_CONFIG = _rule(".codex/config.toml", "project", "RuntimeConfiguration", "runtime_configuration", "configured", "toml")
USER_CONFIG = _rule("config.toml", "user", "RuntimeConfiguration", "runtime_configuration", "configured", "toml", "codex_home")
SYSTEM_CONFIG = _rule("config.toml", "system", "RuntimeConfiguration", "runtime_configuration", "configured", "toml", "codex_system")
PROJECT_SKILL = _rule(".agents/skills/**/SKILL.md", "project", "Skill", "skill", "on_demand", "skill")
USER_SKILL = _rule(".agents/skills/**/SKILL.md", "user", "Skill", "skill", "on_demand", "skill", "home")
CODEX_SKILL = _rule("skills/**/SKILL.md", "user", "Skill", "skill", "on_demand", "skill", "codex_home")
SYSTEM_SKILL = _rule("skills/**/SKILL.md", "system", "Skill", "skill", "on_demand", "skill", "codex_system")
PROJECT_HOOKS = _rule(".codex/hooks.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", "codex_hooks")
USER_HOOKS = _rule("hooks.json", "user", "RuntimeConfiguration", "runtime_configuration", "configured", "codex_hooks", "codex_home")
SYSTEM_HOOKS = _rule("hooks.json", "system", "RuntimeConfiguration", "runtime_configuration", "configured", "codex_hooks", "codex_system")
ROLE_CONFIG = _rule("config.toml:agents.<name>.config_file", "project", "AgentDefinition", "agent_definition", "on_demand", "toml_reference", "reference")
INSTRUCTION_REFERENCE = _rule("config.toml:model_instructions_file", "project", "AgentInstruction", "persistent_instruction", "configured", "reference", "reference")
COMPACT_REFERENCE = _rule("config.toml:experimental_compact_prompt_file", "project", "PromptFile", "reusable_prompt", "conditional", "reference", "reference")
HOOK_SCRIPT = _rule("hooks:command", "project", "HookScript", "hook_execution", "conditional", "command_reference", "reference")
MARKETPLACE = _rule(".agents/plugins/marketplace.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", "codex_marketplace")
PLUGIN_MANIFEST = _rule("plugin.json|.codex-plugin/plugin.json", "project", "PluginManifest", "plugin", "configured", "codex_plugin", "reference")
PLUGIN_SKILL = _rule("plugin:skills/**/SKILL.md", "project", "Skill", "skill", "on_demand", "skill", "reference")
PLUGIN_HOOKS = _rule("plugin:hooks/hooks.json", "project", "RuntimeConfiguration", "runtime_configuration", "configured", "codex_hooks", "reference")
PLUGIN_MCP = _rule("plugin:mcp.json|mcpServers", "project", "RuntimeConfiguration", "runtime_configuration", "configured", "reference", "reference")
RULES = (
    PROJECT_OVERRIDE, PROJECT_INSTRUCTION, GLOBAL_OVERRIDE, GLOBAL_INSTRUCTION, FALLBACK,
    PROJECT_CONFIG, USER_CONFIG, SYSTEM_CONFIG, PROJECT_SKILL, USER_SKILL, CODEX_SKILL,
    SYSTEM_SKILL, PROJECT_HOOKS, USER_HOOKS, SYSTEM_HOOKS, ROLE_CONFIG,
    INSTRUCTION_REFERENCE, COMPACT_REFERENCE, HOOK_SCRIPT, MARKETPLACE,
    PLUGIN_MANIFEST, PLUGIN_SKILL, PLUGIN_HOOKS, PLUGIN_MCP,
)


def _add(found: list[BehaviorFile], path: Path, root: Path, rule: SourceRule,
         scope: str = "", **values: Any) -> None:
    if safe_file(path, root):
        found.append(BehaviorFile(path=path, root=root, rule=rule, scope=scope or rule.scope, **values))


def _merge(lower: dict[str, Any], higher: dict[str, Any]) -> dict[str, Any]:
    result = dict(lower)
    for key, value in higher.items():
        previous = result.get(key)
        result[key] = _merge(previous, value) if isinstance(previous, dict) and isinstance(value, dict) else value
    return result


def _instructions(directory: Path, root: Path, names: list[str]) -> Path | None:
    """Select the first nonempty documented name, never union overridden files."""
    for name in names:
        path = directory / name
        if not safe_file(path, root):
            continue
        text = read_text(path, root)
        if text is None:
            # A bounded metadata read cannot establish whether this file loads.
            # Preserve its path conservatively rather than reading its body.
            return path
        if text.strip():
            return path
    return None


def _hook_scripts(found: list[BehaviorFile], raw: dict[str, Any], declaring: Path,
                  root: Path, scope: str, roots: tuple[Path, ...], cwd: Path, home: Path) -> None:
    hooks = raw.get("hooks", {})
    if not isinstance(hooks, dict):
        return
    for groups in list(hooks.values())[:64]:
        if not isinstance(groups, list):
            continue
        for group in groups[:64]:
            handlers = group.get("hooks", []) if isinstance(group, dict) else []
            if not isinstance(handlers, list):
                continue
            for handler in handlers[:64]:
                if not isinstance(handler, dict) or handler.get("type", "command") != "command":
                    continue
                command = handler.get("commandWindows", handler.get("command")) if os.name == "nt" else handler.get("command")
                if not isinstance(command, str):
                    continue
                for script in local_command_files(command, cwd / "_hook_reference", roots, {"CODEX_HOME": roots[1], "HOME": home, "PLUGIN_ROOT": root}):
                    approved = next((candidate for candidate in roots if safe_file(script, candidate)), root)
                    _add(found, script, approved, HOOK_SCRIPT, scope,
                         evidence="Local script explicitly referenced by a Codex hook; hook trust and execution unverified.")


def _config_references(found: list[BehaviorFile], path: Path, root: Path, scope: str,
                       roots: tuple[Path, ...], seen: set[Path], cwd: Path, home: Path, hooks_enabled: bool, depth: int = 0) -> None:
    if not safe_file(path, root) or depth > 4 or len(seen) >= 128 or path.resolve() in seen:
        return
    seen.add(path.resolve())
    raw = read_toml(path, root)
    if not isinstance(raw, dict):
        return
    for key, rule in (("model_instructions_file", INSTRUCTION_REFERENCE),
                      ("experimental_instructions_file", INSTRUCTION_REFERENCE),
                      ("experimental_compact_prompt_file", COMPACT_REFERENCE)):
        ref = resolve_reference(raw.get(key), path, roots, {"HOME": home, "CODEX_HOME": roots[1]})
        if ref is not None:
            approved = next(candidate for candidate in roots if safe_file(ref, candidate))
            _add(found, ref, approved, rule, scope,
                 evidence=f"Explicit {key} reference in Codex config; effective session selection unverified.")
    agents = raw.get("agents", {})
    if isinstance(agents, dict):
        for role in list(agents.values())[:64]:
            if not isinstance(role, dict):
                continue
            ref = resolve_reference(role.get("config_file"), path, roots, {"HOME": home, "CODEX_HOME": roots[1]})
            if ref is not None:
                approved = next(candidate for candidate in roots if safe_file(ref, candidate))
                _add(found, ref, approved, ROLE_CONFIG, scope,
                     evidence="Role TOML explicitly referenced by agents.<name>.config_file; subagent invocation unverified.")
                _config_references(found, ref, approved, scope, roots, seen, cwd, home, hooks_enabled, depth + 1)
    if hooks_enabled:
        _hook_scripts(found, raw, path, root, scope, roots, cwd, home)


def _paths(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else [item for item in value if isinstance(item, str)][:64] if isinstance(value, list) else []


def _plugin(found: list[BehaviorFile], plugin: Path, scope: str, cwd: Path, home: Path) -> None:
    portable_path = plugin / "plugin.json"
    legacy_path = plugin / ".codex-plugin/plugin.json"
    portable = read_json(portable_path, plugin)
    legacy = read_json(legacy_path, plugin)
    if portable is not None and not isinstance(portable.get("name"), str):
        portable = None
    if legacy is not None and not isinstance(legacy.get("name"), str):
        legacy = None
    if portable is None and legacy is None:
        return
    manifest = portable_path if portable is not None else legacy_path
    _add(found, manifest, plugin, PLUGIN_MANIFEST, scope,
         evidence=f"Explicitly enabled local-marketplace plugin; installed copy and runtime activation unverified; {_PLUGIN_DOCS}")
    extension = (portable or {}).get("extensions", {})
    overlay = extension.get("com.openai") if isinstance(extension, dict) else None
    if not isinstance(overlay, dict):
        overlay = legacy or {}
        if portable is not None and legacy is not None:
            _add(found, legacy_path, plugin, PLUGIN_MANIFEST, scope)
    skill_paths = ["skills"] if portable is not None else _paths(overlay.get("skills", "skills"))
    for value in skill_paths:
        target = resolve_reference(value, plugin / "manifest.json", (plugin,))
        # resolve_reference is file-only; directory declarations are bounded below.
        directory = plugin / value.removeprefix("./")
        if target is not None and target.name == "SKILL.md":
            _add(found, target, plugin, PLUGIN_SKILL, scope)
        for skill in safe_glob(directory, "**/SKILL.md") if directory.is_dir() and directory.resolve().is_relative_to(plugin.resolve()) else []:
            _add(found, skill, plugin, PLUGIN_SKILL, scope)
    mcp_paths = ["mcp.json"] if portable is not None else _paths(overlay.get("mcpServers", ".mcp.json"))
    for value in mcp_paths:
        ref = resolve_reference(value, plugin / "manifest.json", (plugin,))
        if ref is not None:
            _add(found, ref, plugin, PLUGIN_MCP, scope)
    hooks_value = overlay.get("hooks", "hooks/hooks.json")
    for value in _paths(hooks_value):
        ref = resolve_reference(value, plugin / "manifest.json", (plugin,))
        if ref is not None:
            _add(found, ref, plugin, PLUGIN_HOOKS, scope)
            _hook_scripts(found, read_json(ref, plugin) or {}, ref, plugin, scope, (plugin, plugin), cwd, home)
    if isinstance(hooks_value, dict):
        _hook_scripts(found, hooks_value, manifest, plugin, scope, (plugin, plugin), cwd, home)
    ref = resolve_reference(overlay.get("onboardingSkill"), plugin / "manifest.json", (plugin,))
    if ref is not None:
        _add(found, ref, plugin, PLUGIN_SKILL, scope)


def _plugins(found: list[BehaviorFile], context: DiscoveryContext, config: dict[str, Any], codex_home: Path,
             roots: tuple[Path, ...]) -> None:
    states = config.get("plugins", {})
    if not isinstance(states, dict):
        return
    catalogs = [(context.project_root, "project"), (context.home, "user")]
    for base, scope in catalogs:
        if not context.allows(scope):
            continue
        path = base / ".agents/plugins/marketplace.json"
        raw = read_json(path, base)
        if raw is None:
            continue
        _add(found, path, base, MARKETPLACE, scope)
        entries = raw.get("plugins", [])
        if not isinstance(entries, list) or not isinstance(raw.get("name"), str):
            continue
        for entry in entries[:128]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                continue
            key = f"{entry['name']}@{raw['name']}"
            state = states.get(key, {})
            if not isinstance(state, dict) or state.get("enabled") is not True:
                continue
            source = entry.get("source", {})
            if isinstance(source, dict) and source.get("source") == "local":
                ref = source.get("path")
                if isinstance(ref, str):
                    plugin = base / ref
                    if any(plugin.resolve().is_relative_to(root.resolve()) for root in roots):
                        _plugin(found, plugin, scope, context.workspace, context.home)
            if context.include_plugin_cache and all("/" not in item and "\\" not in item and item not in {".", ".."} for item in (raw["name"], entry["name"])):
                # The selected installed version is not publicly recorded here.
                # Candidates stay suspected and no cache is touched by default.
                cache = codex_home / "plugins/cache" / raw["name"] / entry["name"]
                for manifest in safe_glob(cache, "*/plugin.json", include_plugin_containers=True) + safe_glob(cache, "*/.codex-plugin/plugin.json", include_plugin_containers=True):
                    plugin = manifest.parent if manifest.name == "plugin.json" and manifest.parent.name != ".codex-plugin" else manifest.parent.parent
                    _plugin(found, plugin, scope, context.workspace, context.home)


def discover(context: DiscoveryContext, agent_name: str) -> list[BehaviorFile]:
    if agent_name != "codex":
        return []
    codex_home = Path(os.environ.get("CODEX_HOME", str(context.home / ".codex"))).expanduser()
    roots = (context.project_root, codex_home, context.home / ".agents", _SYSTEM_ROOT)
    found: list[BehaviorFile] = []
    layers: list[tuple[Path, Path, str, SourceRule]] = [
        (_SYSTEM_ROOT / "config.toml", _SYSTEM_ROOT, "system", SYSTEM_CONFIG),
        (codex_home / "config.toml", codex_home, "user", USER_CONFIG),
        *((base / ".codex/config.toml", context.project_root, "project", PROJECT_CONFIG) for base in context.ancestors),
    ]
    effective: dict[str, Any] = {}
    for path, root, scope, rule in layers:
        raw = read_toml(path, root)
        if raw is not None:
            effective = _merge(effective, raw)
    features = effective.get("features", {})
    hooks_enabled = not isinstance(features, dict) or features.get("hooks", features.get("codex_hooks")) is not False
    for path, root, scope, rule in layers:
        if context.allows(scope):
            _add(found, path, root, rule, scope)
            _config_references(found, path, root, scope, roots, set(), context.workspace, context.home, hooks_enabled)
    fallback_values = effective.get("project_doc_fallback_filenames", [])
    fallbacks = [name for name in _paths(fallback_values) if name not in {".", ".."} and "/" not in name and "\\" not in name]
    names = ["AGENTS.override.md", "AGENTS.md", *fallbacks]
    budget_value = effective.get("project_doc_max_bytes", 32768)
    budget = budget_value if isinstance(budget_value, int) and not isinstance(budget_value, bool) and budget_value >= 0 else 32768
    if context.allows("user"):
        selected = _instructions(codex_home, codex_home, names[:2])
        if selected is not None:
            rule = GLOBAL_OVERRIDE if selected.name == "AGENTS.override.md" else GLOBAL_INSTRUCTION
            readable = read_text(selected, codex_home) is not None
            _add(found, selected, codex_home, rule, confidence="confirmed" if readable else "suspected",
                 discovery_status="available" if readable else "metadata_unavailable",
                 precedence=0, evidence=f"Selected global instruction candidate; bounded metadata only; {_DOCS}")
    if context.allows("project"):
        directories = list(context.ancestors)
        directories.extend(sorted({path.parent for name in names for path in safe_glob(context.workspace, f"**/{name}")} - set(directories)))
        for directory in directories:
            selected = _instructions(directory, context.project_root, names)
            if selected is None:
                continue
            active = directory in context.ancestors
            if active and budget <= 0:
                continue
            text = read_text(selected, context.project_root)
            readable = text is not None
            status = "available" if readable else "metadata_unavailable"
            if active and text is not None:
                size = len(text.encode("utf-8"))
                status = "partially_available" if size > budget else status
                budget = max(0, budget - size)
            rule = PROJECT_OVERRIDE if selected.name == names[0] else PROJECT_INSTRUCTION if selected.name == names[1] else FALLBACK
            _add(found, selected, context.project_root, rule,
                 activation="automatic" if active else "conditional", confidence="confirmed" if active and readable else "suspected",
                 discovery_status=status, precedence=len(directory.relative_to(context.project_root).parts) + 1,
                 target_pattern=str(directory.relative_to(context.project_root) / "**"),
                 evidence=f"First nonempty instruction per directory, override then AGENTS then configured fallback; "
                          f"{'in startup ancestor chain' if active else 'subdirectory only; future working-directory use unverified'}; {_DOCS}")
    skill_roots = [(context.home / ".agents/skills", context.home, USER_SKILL),
                   (codex_home / "skills", codex_home, CODEX_SKILL),
                   (_SYSTEM_ROOT / "skills", _SYSTEM_ROOT, SYSTEM_SKILL)]
    skill_roots.extend((base / ".agents/skills", context.project_root, PROJECT_SKILL) for base in context.ancestors)
    from agenthound.discovery.skills import skill_info

    disabled = effective.get("skills", {})
    disabled_entries = disabled.get("config", []) if isinstance(disabled, dict) else []
    for directory, root, rule in skill_roots:
        if not context.allows(rule.scope):
            continue
        for path in safe_glob(directory, "**/SKILL.md"):
            enabled = True
            name = skill_info(path, rule.scope, "codex").name
            if isinstance(disabled_entries, list):
                for setting in disabled_entries:
                    if isinstance(setting, dict) and (setting.get("path") == str(path) or setting.get("name") == name) and isinstance(setting.get("enabled"), bool):
                        enabled = setting["enabled"]
            if enabled:
                scope = "system" if rule is CODEX_SKILL and ".system" in path.relative_to(directory).parts else rule.scope
                _add(found, path, root, rule, scope)
    for base, root, scope, rule in [
        (_SYSTEM_ROOT / "hooks.json", _SYSTEM_ROOT, "system", SYSTEM_HOOKS),
        (codex_home / "hooks.json", codex_home, "user", USER_HOOKS),
        *((directory / ".codex/hooks.json", context.project_root, "project", PROJECT_HOOKS) for directory in context.ancestors),
    ]:
        if context.allows(scope):
            _add(found, base, root, rule, scope)
            if hooks_enabled:
                _hook_scripts(found, read_json(base, root) or {}, base, root, scope, roots, context.workspace, context.home)
    _plugins(found, context, effective, codex_home, roots)
    return found
