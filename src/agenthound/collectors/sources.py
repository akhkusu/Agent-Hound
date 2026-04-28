"""Collect Source nodes and Influences edges from the workspace."""

from __future__ import annotations

from pathlib import Path

from agenthound.collectors.base import CollectionResult
from agenthound.discovery.workspace import find_source_files
from agenthound.models.edges import Edge
from agenthound.models.nodes import Agent, Source


def collect_sources(agent: Agent, workspace: Path, scope: str) -> CollectionResult:
    result = CollectionResult()

    if scope == "global":
        return result

    raw = find_source_files(workspace)
    seen: set[str] = set()

    for path_str, kind in raw:
        fpath = Path(path_str)
        source = Source(
            name=fpath.name,
            source_kind=kind,
            path=path_str,
            is_external=False,
        )
        if source.objectid not in seen:
            result.sources.append(source)
            seen.add(source.objectid)
            result.edges.append(Edge(start=source.objectid, end=agent.objectid, kind="Influences"))

    return result
