"""Shared CollectionResult for all collectors."""

from __future__ import annotations

from agenthound.models.edges import Edge
from agenthound.models.nodes import Agent, Asset, Capability, Impact, Source


class CollectionResult:
    def __init__(self) -> None:
        self.agents: list[Agent] = []
        self.sources: list[Source] = []
        self.capabilities: list[Capability] = []
        self.assets: list[Asset] = []
        self.impacts: list[Impact] = []
        self.edges: list[Edge] = []

    @property
    def total_nodes(self) -> int:
        return (
            len(self.agents)
            + len(self.sources)
            + len(self.capabilities)
            + len(self.assets)
            + len(self.impacts)
        )

    @property
    def total_edges(self) -> int:
        return len(self.edges)
