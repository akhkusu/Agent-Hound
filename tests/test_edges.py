from agenthound.collectors.base import CollectionResult
from agenthound.models.edges import Edge
from agenthound.models.nodes import Agent, Asset, Capability, Impact, Source


def test_edge_to_opengraph():
    edge = Edge(start="agent-abc123", end="cap-def456", kind="HasCapability")
    d = edge.to_opengraph()
    assert d["start"] == {"value": "agent-abc123", "match_by": "id"}
    assert d["end"] == {"value": "cap-def456", "match_by": "id"}
    assert d["kind"] == "HasCapability"


def test_all_edge_kinds_valid():
    for kind in ("Influences", "HasCapability", "CanAccess", "Triggers"):
        e = Edge(start="x", end="y", kind=kind)  # type: ignore[arg-type]
        assert e.kind == kind


def test_collection_result_counts():
    r = CollectionResult()
    r.agents.append(Agent(name="a", platform="P", config_path="/p"))
    r.capabilities.append(Capability(name="c", cap_kind="MCPServer"))
    r.assets.append(Asset(name="k", asset_kind="SSHKey", path="/k"))
    r.impacts.append(Impact(name="i", impact_kind="Exfiltration"))
    r.sources.append(Source(name="s", source_kind="DocFile", path="/s"))
    r.edges.append(Edge(start="x", end="y", kind="Triggers"))
    assert r.total_nodes == 5
    assert r.total_edges == 1
