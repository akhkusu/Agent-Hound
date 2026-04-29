"""Build and write BloodHound OpenGraph JSON."""

from __future__ import annotations

import json
from typing import Any

from agenthound.collectors.base import CollectionResult


def build_opengraph(result: CollectionResult) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for agent in result.agents:
        nodes.append(agent.to_opengraph())
    for source in result.sources:
        nodes.append(source.to_opengraph())
    for cap in result.capabilities:
        nodes.append(cap.to_opengraph())
    for asset in result.assets:
        nodes.append(asset.to_opengraph())
    for impact in result.impacts:
        nodes.append(impact.to_opengraph())

    edges = [e.to_opengraph() for e in result.edges]

    return {
        "metadata": {"source_kind": "AgentHound"},
        "graph": {"nodes": nodes, "edges": edges},
    }


def write_opengraph(output: dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
        f.write("\n")
