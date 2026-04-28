"""Collect Capability nodes and HasCapability edges from agent config files."""

from __future__ import annotations

from pathlib import Path

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.config_parser import parse_config
from agenthound.models.edges import Edge


def collect_from_config(config_path: Path) -> CollectionResult:
    """Parse a single config file and return a CollectionResult with Agent and Capabilities."""
    result = CollectionResult()
    agent, caps = parse_config(config_path)
    result.agents.append(agent)
    result.capabilities.extend(caps)
    for cap in caps:
        result.edges.append(Edge(start=agent.objectid, end=cap.objectid, kind="HasCapability"))
    return result


def collect_from_configs(config_paths: list[Path]) -> CollectionResult:
    """Parse multiple config files, deduplicating agents and capabilities by objectid."""
    combined = CollectionResult()
    seen_agent_ids: set[str] = set()
    seen_cap_ids: set[str] = set()
    seen_edge_keys: set[tuple[str, str, str]] = set()

    for path in config_paths:
        partial = collect_from_config(path)

        for agent in partial.agents:
            if agent.objectid not in seen_agent_ids:
                combined.agents.append(agent)
                seen_agent_ids.add(agent.objectid)

        for cap in partial.capabilities:
            if cap.objectid not in seen_cap_ids:
                combined.capabilities.append(cap)
                seen_cap_ids.add(cap.objectid)

        for edge in partial.edges:
            key = (edge.start, edge.end, edge.kind)
            if key not in seen_edge_keys:
                combined.edges.append(edge)
                seen_edge_keys.add(key)

    return combined
