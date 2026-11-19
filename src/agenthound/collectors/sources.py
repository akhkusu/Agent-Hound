"""Collect metadata-only Source nodes and correctly scoped Influences edges."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.behavior_files import BehaviorFile, DiscoveryContext, read_text, safe_file
from agenthound.discovery.git_metadata import SourceMetadata, inspect_source
from agenthound.discovery import skills
from agenthound.discovery.source_registry import PLATFORM_NAMES, discover_behavior_files, matching_rule
from agenthound.discovery.workspace import find_source_files
from agenthound.models.edges import Edge
from agenthound.models.nodes import Agent, Asset, Source, SourceKind


def _source_name(path: Path, kind: str, workspace: Path, platform: str = "") -> str:
    """Keep established display names; naming does not determine applicability."""
    try:
        rel = path.resolve().relative_to(workspace.resolve()).parts
    except (OSError, ValueError):
        rel = ()
    root = rel[:2]
    label = {"Claude Code": "Claude", "Codex": "Codex", "GitHub Copilot": "Copilot", "Cursor": "Cursor"}.get(platform, "Custom")
    if kind == "Skill":
        return f"Skill: {path.parent.name}"
    if kind == "PromptFile":
        short = path.name.removesuffix(".prompt.md").removesuffix(".md")
        prefix = "Claude Command" if label == "Claude" else f"{label} Prompt"
        return f"{prefix}: {short}"
    if kind == "AgentDefinition":
        prefix = f"{label} Agent"
        return f"{prefix}: {path.stem.removesuffix('.agent')}"
    if kind == "AgentInstruction":
        if rel == (".github", "copilot-instructions.md"):
            return "Copilot Instructions"
        prefixes = {(".claude", "rules"): "Claude Rule", (".cursor", "rules"): "Cursor Rule",
                    (".github", "instructions"): "Copilot Instruction"}
        if root in prefixes:
            return f"{prefixes[root]}: {path.stem.removesuffix('.instructions')}"
    return path.name


def collect_sources(agent: Agent, workspace: Path, scope: str,
                    include_plugin_cache: bool = False) -> CollectionResult:
    return collect_sources_for_agents([agent], workspace, scope, include_plugin_cache)


def collect_sources_for_agents(agents: list[Agent], workspace: Path, scope: str,
                               include_plugin_cache: bool = False) -> CollectionResult:
    """Discover a workspace once and share metadata across owning agents."""
    result = CollectionResult()
    if not agents:
        return result
    workspace = workspace.resolve()
    context = DiscoveryContext(workspace, skills._user_home(), scope, include_plugin_cache)
    observations = {agent.objectid: discover_behavior_files(context, agent.name) for agent in agents}
    nodes: dict[tuple[Path, str], Source] = {}
    git_cache: dict[Path, SourceMetadata] = {}

    def add_source(path: Path, kind: SourceKind, candidate: BehaviorFile | None = None) -> Source:
        key = (path.resolve(), kind)
        if key in nodes:
            return nodes[key]
        rule = candidate.rule if candidate else matching_rule(path, workspace)
        source_scope = (candidate.scope or candidate.rule.scope) if candidate else "project"
        metadata = skills.skill_info(path, source_scope, candidate.origin if candidate else "project") if kind == "Skill" else None
        if source_scope == "project" and path not in git_cache:
            git_cache[path] = inspect_source(path)
        git = git_cache.get(path, SourceMetadata())
        repository_id = Asset(name=Path(git.repository_path).name, asset_kind="SourceCode", path=git.repository_path).objectid if git.repository_path else ""
        source = Source(
            name=f"Skill: {metadata.name}" if metadata else (candidate.name if candidate and candidate.name else _source_name(path, kind, workspace, rule.platform if rule else "")),
            source_kind=kind, path=str(path), is_external=source_scope != "project",
            source_scope=source_scope, description=metadata.description if metadata else "",
            discovery_status=candidate.discovery_status if candidate else ("available" if rule is None else "unlinked"),
            platform=rule.platform if rule else "Generic",
            origin=candidate.origin if candidate and candidate.origin else (rule.root if rule else "workspace"),
            influence_channel=(rule.influence_channel if rule else "prompt_context"),
            activation=(candidate.activation or candidate.rule.activation) if candidate else ("unknown" if rule else "on_demand"),
            precedence=candidate.precedence if candidate else 0,
            target_pattern=candidate.target_pattern if candidate else "",
            git_repository_asset_id=repository_id, git_relative_path=git.relative_path,
            git_committed=git.committed, git_clean=git.clean,
        )
        nodes[key] = source
        return source

    # Retain general documents and orphan platform-specific Source nodes, but
    # never infer an edge from classification alone.
    raw = find_source_files(workspace) if context.allows("project") else []
    specific_paths = {candidate.path.resolve() for candidates in observations.values() for candidate in candidates}
    docs: list[Source] = []
    for path_str, kind in raw:
        path = Path(path_str)
        if not safe_file(path, workspace) or path.resolve() in specific_paths:
            continue
        source = add_source(path, kind)
        if kind == "DocFile":
            docs.append(source)

    for agent in agents:
        for doc in docs:
            result.edges.append(Edge(start=doc.objectid, end=agent.objectid, kind="Influences", properties={
                "confidence": "suspected", "evidence": "Existing scoped document source; actual reading unverified",
                "influence_channel": "prompt_context", "activation": "on_demand", "source_scope": "project",
                "platform": PLATFORM_NAMES.get(agent.name, agent.platform), "content_exported": False,
            }))
        for candidate in observations[agent.objectid]:
            source = add_source(candidate.path, cast(SourceKind, candidate.rule.source_kind), candidate)
            activation = candidate.activation or candidate.rule.activation
            confidence = candidate.confidence or candidate.rule.confidence
            if activation in {"conditional", "on_demand", "session_resume", "unknown"}:
                confidence = "suspected"
            if candidate.discovery_status in {"overridden", "disabled", "out_of_scope"}:
                continue
            properties: dict[str, str | int | bool] = {
                "confidence": confidence, "evidence": candidate.evidence or candidate.rule.evidence,
                "influence_channel": candidate.rule.influence_channel, "activation": activation,
                "source_scope": candidate.scope or candidate.rule.scope, "platform": candidate.rule.platform,
                "origin": candidate.origin or candidate.rule.root, "content_exported": False,
                "precedence": candidate.precedence,
            }
            if activation == "on_demand":
                properties["evidence"] = str(properties["evidence"]) + "; invocation unverified"
            if candidate.target_pattern:
                properties["target_pattern"] = candidate.target_pattern
            if candidate.rule.metadata_parser and read_text(candidate.path, candidate.root) is None:
                properties["confidence"] = "suspected"
                properties["metadata_status"] = "unreadable_or_oversized"
            result.edges.append(Edge(start=source.objectid, end=agent.objectid, kind="Influences", properties=properties))

    result.sources = list(nodes.values())
    return result
