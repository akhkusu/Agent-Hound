"""Registry of documented source locations and platform-specific resolvers.

Static classification is shared with workspace discovery. Resolvers apply the
selected platform's loading, scope, precedence and explicit-reference rules.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from typing import Iterable, cast

from agenthound.discovery.behavior_files import (
    BehaviorFile, DiscoveryContext, SourceRule, apply_rules, matches_pattern, plugin_component, safe_file,
)
from agenthound.discovery import claude_sources, codex_sources, copilot_sources, cursor_sources
from agenthound.models.nodes import SourceKind

_LEGACY_RULES = (
    SourceRule("Gemini", "**/GEMINI.md", "project", "AgentInstruction", "persistent_instruction",
               "automatic", ("gemini",), "confirmed", "Existing GEMINI.md instruction discovery"),
    SourceRule("Generic", "**/SYSTEM_PROMPT.md", "project", "AgentInstruction", "prompt_context",
               "unknown", (), evidence="Existing convention; no platform loading inferred"),
    SourceRule("GitHub Copilot", ".github/agents/**/*.md", "project", "AgentDefinition", "agent_definition",
               "on_demand", ("vscode-copilot",), evidence="Existing agent-definition source; invocation and format unverified"),
)

RULES: tuple[SourceRule, ...] = (
    *claude_sources.RULES, *codex_sources.RULES, *copilot_sources.RULES,
    *cursor_sources.RULES, *_LEGACY_RULES,
)
PLATFORM_NAMES = {target: rule.platform for rule in RULES for target in rule.targets}


def matching_rule(path: Path, workspace: Path) -> SourceRule | None:
    try:
        relative = path.absolute().relative_to(workspace.absolute()).as_posix()
    except ValueError:
        return None
    for rule in RULES:
        if rule.scope == "project" and rule.root in {"workspace", "ancestor"} and matches_pattern(relative, rule.pattern):
            return rule
    return None


def classify_source(path: Path, workspace: Path) -> SourceKind | None:
    rule = matching_rule(path, workspace)
    return cast(SourceKind, rule.source_kind) if rule is not None else None


def discover_behavior_files(context: DiscoveryContext, agent_name: str) -> list[BehaviorFile]:
    candidates: list[BehaviorFile] = []
    for module in (claude_sources, codex_sources, copilot_sources, cursor_sources):
        if any(agent_name in rule.targets for rule in module.RULES):
            candidates.extend(module.discover(context, agent_name))
    legacy = apply_rules(context, agent_name, _LEGACY_RULES, {"workspace": context.workspace})
    # Prefer documented agent formats over retained legacy classifications.
    seen = {(candidate.path.resolve(), candidate.rule.source_kind) for candidate in candidates}
    candidates.extend(candidate for candidate in legacy
                      if (candidate.path.resolve(), candidate.rule.source_kind) not in seen)
    return validate_candidates(context, agent_name, candidates)


def validate_candidates(context: DiscoveryContext, agent_name: str,
                        candidates: Iterable[BehaviorFile]) -> list[BehaviorFile]:
    result: dict[tuple[Path, str], BehaviorFile] = {}
    project = context.project_root.resolve()
    home = context.home.resolve()
    user_roots = [home / name for name in (".claude", ".codex", ".copilot", ".cursor", ".agents")]
    user_roots.extend(Path(value).expanduser().resolve() for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "COPILOT_HOME")
                      if (value := os.environ.get(name)))
    for candidate in candidates:
        scope = candidate.scope or candidate.rule.scope
        path = candidate.path.resolve()
        is_user = path == home / ".claude.json" or any(path.is_relative_to(root) for root in user_roots)
        if scope == "project" and (is_user or not path.is_relative_to(project)):
            scope = "user"
        elif scope in {"user", "system", "managed"} and not is_user and path.is_relative_to(project):
            scope = "project"
        if not context.allows(scope) or agent_name not in candidate.rule.targets or not safe_file(candidate.path, candidate.root):
            continue
        if candidate.rule.root not in {"plugin", "reference", "configured"} and candidate.origin not in {"plugin", "enabled-plugin", "claude-import"}:
            boundary = context.workspace if scope == "project" else candidate.root
            if plugin_component(candidate.path, boundary, include_root=False):
                continue
        key = (path, candidate.rule.source_kind)
        previous = result.get(key)
        if previous is None:
            result[key] = replace(candidate, scope=scope)
        elif previous.rule.influence_channel == candidate.rule.influence_channel:
            # A single file is one Source. Prefer the more specific resolver.
            old_conf = previous.confidence or previous.rule.confidence
            new_conf = candidate.confidence or candidate.rule.confidence
            if old_conf != "confirmed" and new_conf == "confirmed":
                result[key] = replace(candidate, scope=scope)
    return sorted(result.values(), key=lambda candidate: (str(candidate.path), candidate.rule.source_kind))
