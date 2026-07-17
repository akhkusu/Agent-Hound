"""Unit tests for CLI finding aggregation helpers."""

from agenthound.cli import _asset_confidence_note, _print_findings
from agenthound.collectors.base import CollectionResult
from agenthound.models.edges import Edge
from agenthound.models.nodes import Agent, Source


def test_asset_confidence_note_dedupes_by_asset():
    # Two edges to the same asset (one confirmed, one suspected) -> 1 asset,
    # counted as confirmed (highest wins).
    edges = [
        Edge(start="c1", end="asset-1", kind="CanAccess", properties={"confidence": "confirmed"}),
        Edge(start="c2", end="asset-1", kind="CanAccess", properties={"confidence": "suspected"}),
        Edge(start="c3", end="asset-2", kind="CanAccess", properties={"confidence": "suspected"}),
    ]
    count, note = _asset_confidence_note(edges)
    assert count == 2
    assert note == "1 confirmed, 1 suspected"


def test_print_findings_ignores_uninfluencing_source(capsys):
    # A source with no Influences edge must not be counted as an IPI source.
    result = CollectionResult()
    agent = Agent(name="claude-desktop", platform="Claude Desktop", config_path="/p")
    influencing = Source(name="README.md", source_kind="DocFile", path="/w/README.md")
    orphan = Source(name=".cursorrules", source_kind="AgentInstruction", path="/w/.cursorrules")
    result.agents.append(agent)
    result.sources.extend([influencing, orphan])
    result.edges.append(Edge(start=influencing.objectid, end=agent.objectid,
                             kind="Influences", properties={"confidence": "suspected"}))
    _print_findings(result)
    out = capsys.readouterr().out
    assert "1 IPI source(s)" in out
    assert "README.md" in out
    assert ".cursorrules" not in out
