"""Collect Capability nodes and HasCapability edges from agent config files."""

from __future__ import annotations

from pathlib import Path

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.config_parser import claude_settings_paths, parse_config
from agenthound.discovery.claude_settings import collect_claude_settings
from agenthound.discovery.claude_mcp import claude_mcp_paths
from agenthound.discovery.codex_settings import codex_config_paths, collect_codex_settings
from agenthound.models.edges import Edge
from agenthound.models.nodes import Capability, Confidence

_CONF_ORDER: dict[Confidence, int] = {"none": 0, "suspected": 1, "confirmed": 2}


def _max_conf(a: Confidence, b: Confidence) -> Confidence:
    return a if _CONF_ORDER[a] >= _CONF_ORDER[b] else b


def _min_conf(a: Confidence, b: Confidence) -> Confidence:
    return a if _CONF_ORDER[a] <= _CONF_ORDER[b] else b


def _merge_evidence(a: str, b: str) -> str:
    parts: list[str] = []
    for part in (a, b):
        if part and part not in parts:
            parts.append(part)
    return "; ".join(parts)


def _merge_file_dimension(
    a: Capability, b: Capability
) -> tuple[Confidence, tuple[str, ...] | None, bool]:
    """Merge file access scope/confidence of two same-id capabilities.

    Scope widens to the union, but the confidence of the widened claim is
    capped at the confidence of whichever side justified the widening —
    otherwise a suspected unrestricted cap would inherit "confirmed" from a
    tightly-scoped one. Sides without file access do not contribute scope.
    """
    sides = [c for c in (a, b) if c.file_access != "none"]
    if not sides:
        return "none", None, False
    readonly = all(c.file_readonly for c in sides)
    if len(sides) == 1:
        return sides[0].file_access, sides[0].allowed_paths, readonly
    (ca, pa), (cb, pb) = [(c.file_access, c.allowed_paths) for c in sides]
    if pa is None and pb is None:
        return _max_conf(ca, cb), None, readonly
    if pa is None:
        return ca, None, readonly
    if pb is None:
        return cb, None, readonly
    union = tuple(sorted(set(pa) | set(pb)))
    if set(pa) == set(union) and set(pb) == set(union):
        return _max_conf(ca, cb), union, readonly
    if set(pa) == set(union):
        return ca, union, readonly
    if set(pb) == set(union):
        return cb, union, readonly
    return _min_conf(ca, cb), union, readonly


def _merge_repo_scope(
    a: tuple[str, ...] | None, b: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    # Widen access: if either side can reach any repo (None), so can the merge.
    if a is None or b is None:
        return None
    return tuple(sorted(set(a) | set(b)))


def _merge_capabilities(a: Capability, b: Capability) -> Capability:
    """Merge two capabilities that share an objectid (same name + kind).

    Access range merges wide (union / unknown wins), confidence merges
    conservatively so the graph never overstates certainty.
    """
    if a.cap_kind == "BuiltInTool":
        return a
    file_access, allowed_paths, file_readonly = _merge_file_dimension(a, b)
    return Capability(
        name=a.name,
        cap_kind=a.cap_kind,
        command=a.command if a.command is not None else b.command,
        transport=a.transport if a.transport is not None else b.transport,
        agent_scope=a.agent_scope,
        mcp_config_path=a.mcp_config_path, mcp_scope=a.mcp_scope, mcp_evidence=a.mcp_evidence,
        shell_exec=_max_conf(a.shell_exec, b.shell_exec),
        file_access=file_access,
        network_send=_max_conf(a.network_send, b.network_send),
        git_write=_max_conf(a.git_write, b.git_write),
        file_readonly=file_readonly,
        allowed_paths=allowed_paths,
        repo_scope=_merge_repo_scope(a.repo_scope, b.repo_scope),
        shell_evidence=_merge_evidence(a.shell_evidence, b.shell_evidence),
        file_evidence=_merge_evidence(a.file_evidence, b.file_evidence),
        network_evidence=_merge_evidence(a.network_evidence, b.network_evidence),
        git_evidence=_merge_evidence(a.git_evidence, b.git_evidence),
    )


def collect_from_config(config_path: Path) -> CollectionResult:
    """Parse a single config file and return a CollectionResult with Agent and Capabilities."""
    result = CollectionResult()
    agent, caps = parse_config(config_path)
    result.agents.append(agent)
    result.capabilities.extend(caps)
    for cap in caps:
        properties = {"confidence": "suspected", "evidence": (cap.shell_evidence or cap.file_evidence) + "; runtime tool restrictions and other settings unverified"} if cap.cap_kind == "BuiltInTool" else ({"confidence": "suspected", "evidence": cap.mcp_evidence} if cap.mcp_evidence else {})
        result.edges.append(Edge(start=agent.objectid, end=cap.objectid, kind="HasCapability", properties=properties))
    return result


def collect_from_configs(config_paths: list[Path], workspace: Path | None = None) -> CollectionResult:
    """Parse multiple config files, deduplicating agents and capabilities by objectid.

    Same-id capabilities from different configs are merged instead of dropped,
    so a scoped filesystem server in one config cannot mask (or borrow the
    confidence of) an equally-named server in another.
    """
    combined = CollectionResult()
    seen_agent_ids: set[str] = set()
    caps_by_id: dict[str, Capability] = {}
    seen_edge_keys: set[tuple[str, str, str]] = set()

    partials = []
    grouped_paths: set[Path] = set()
    if workspace is not None:
        candidates = {p.resolve() for p in [*claude_settings_paths(workspace), *claude_mcp_paths(workspace)]}
        grouped_paths = {p.resolve() for p in config_paths if p.resolve() in candidates}
        if grouped_paths:
            agent, caps = collect_claude_settings(config_paths, workspace)
            partial = CollectionResult()
            partial.agents.append(agent)
            # Reuse established edge creation, preserving one owner per cap.
            by_id = {cap.objectid: cap for cap in caps}
            partial.capabilities.extend(by_id.values())
            for cap in by_id.values():
                props = {"confidence": "suspected", "evidence": (cap.shell_evidence or cap.file_evidence) + "; managed policy, runtime restrictions and trust unobserved"} if cap.cap_kind == "BuiltInTool" else ({"confidence": "suspected", "evidence": cap.mcp_evidence} if cap.mcp_evidence else {})
                partial.edges.append(Edge(start=agent.objectid, end=cap.objectid, kind="HasCapability", properties=props))
            partials.append(partial)
        codex_candidates = {path.resolve() for path in codex_config_paths(workspace)}
        codex_paths = [path for path in config_paths if path.resolve() in codex_candidates]
        grouped_paths.update(path.resolve() for path in codex_paths)
        if codex_paths:
            agent, caps = collect_codex_settings(codex_paths, workspace)
            partial = CollectionResult()
            partial.agents.append(agent)
            partial.capabilities.extend(caps)
            partial.edges.extend(
                Edge(start=agent.objectid, end=cap.objectid, kind="HasCapability",
                     properties={"confidence": "suspected", "evidence": cap.mcp_evidence})
                for cap in caps
            )
            partials.append(partial)
    partials.extend(collect_from_config(path) for path in config_paths if path.resolve() not in grouped_paths)

    for partial in partials:

        for agent in partial.agents:
            if agent.objectid not in seen_agent_ids:
                combined.agents.append(agent)
                seen_agent_ids.add(agent.objectid)

        for cap in partial.capabilities:
            existing = caps_by_id.get(cap.objectid)
            caps_by_id[cap.objectid] = _merge_capabilities(existing, cap) if existing else cap

        for edge in partial.edges:
            key = (edge.start, edge.end, edge.kind)
            if key not in seen_edge_keys:
                combined.edges.append(edge)
                seen_edge_keys.add(key)

    combined.capabilities = list(caps_by_id.values())
    return combined
